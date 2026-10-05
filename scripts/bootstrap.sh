#!/usr/bin/env bash
# Fresh-clone setup: Python venv, Blender check, Blender API index, Codex MCP config, doctor.
# Usage: scripts/bootstrap.sh [--cad]   (run from anywhere; everything lands inside this repository)
#   STUDIO_BLENDER=/path/to/blender  overrides the Blender lookup (PATH, then /Applications/Blender.app on macOS).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
WANT_BLENDER="5.2.2"
PY="${PYTHON:-python3}"

echo "== python venv (.venv)"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
if [ "${1:-}" = "--cad" ]; then   # optional CAD factory venv (asset generate); see studio/asset_factory/requirements-cad.txt
  [ -x .venvs/cad/bin/python ] || "$PY" -m venv .venvs/cad
  .venvs/cad/bin/python -m pip install --quiet -r studio/asset_factory/requirements-cad.txt
fi

echo "== blender"
BLENDER="$(.venv/bin/python -c 'from studio.common import blender_binary; print(blender_binary())')" || {
  echo "Blender not found. Install Blender $WANT_BLENDER (official build: it bundles numpy) and set STUDIO_BLENDER." >&2; exit 1; }
VERSION="$("$BLENDER" --version 2>/dev/null | head -1)"
echo "$VERSION"
case "$VERSION" in
  *"$WANT_BLENDER"*) ;;
  *) echo "WARNING: measured results (BUILD_REPORT) were made with Blender $WANT_BLENDER; other versions may differ." >&2 ;;
esac

echo "== blender API index (library/api)"
API="library/api/blender-$(echo "$VERSION" | awk '{print $2}').json"
if [ ! -f "$API" ]; then
  mkdir -p library/api
  "$BLENDER" -b --factory-startup --python scripts/build_bpy_index.py -- "$API" >/dev/null
fi

echo "== codex MCP config (.codex/config.toml, per machine)"
sed -e "s|@PYTHON@|$ROOT/.venv/bin/python|" -e "s|@ROOT@|$ROOT|" .codex/config.toml.template > .codex/config.toml

echo "== doctor"
.venv/bin/python -m studio doctor
