#!/usr/bin/env bash
set -euo pipefail

studio_root="$(cd "$(dirname "$0")/.." && pwd)"
host_root="$(cd "$studio_root/.." && pwd)"
container_args=()
for value in "$@"; do
  if [[ "$value" == "$host_root"/* ]]; then
    container_args+=("/workspace/${value#"$host_root"/}")
  else
    container_args+=("$value")
  fi
done
exec docker run --rm --platform linux/arm64 -v "$host_root:/workspace" -w /workspace \
  -e STUDIO_RENDER_ENGINE="${STUDIO_RENDER_ENGINE:-BLENDER_EEVEE}" \
  -e LIBGL_ALWAYS_SOFTWARE=1 -e EGL_PLATFORM=surfaceless \
  technical-studio-blender:4.0 "${container_args[@]}"
