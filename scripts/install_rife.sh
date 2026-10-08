#!/usr/bin/env bash
# RIFE frame interpolator (rife-ncnn-vulkan, MIT) for generated clips: 24/25 fps takes become 30 fps by motion
# interpolation instead of repeated frames (studio/generative/clip.py retime 'interpolate').
# Pinned release, checked against the sha256 recorded here; only the binary and the rife-v4.6 model are kept.
# Usage: scripts/install_rife.sh   (installs into .venvs/tools/, which is not committed)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="20221029"
case "$(uname -s)" in
  Darwin) PLATFORM="macos"; SHA="4a63a1f3c9c715773c57d2ee51df1b315ed20cd6c63103e45c483ecc4400b595" ;;
  Linux)  PLATFORM="ubuntu"; SHA="1e2c7ee7fa7daa326542d50622f0afedc80cf6f1858bda411d16385ffa5cdf68" ;;
  *) echo "no RIFE build for $(uname -s)" >&2; exit 1 ;;
esac
NAME="rife-ncnn-vulkan-$VERSION-$PLATFORM"
DEST="$ROOT/.venvs/tools/rife-ncnn-vulkan-$VERSION"
if [ -x "$DEST/rife-ncnn-vulkan" ] && [ -f "$DEST/rife-v4.6/flownet.param" ]; then
  echo "RIFE $VERSION already installed: $DEST"; exit 0
fi
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -sSfL -o "$TMP/rife.zip" "https://github.com/nihui/rife-ncnn-vulkan/releases/download/$VERSION/$NAME.zip"
echo "$SHA  $TMP/rife.zip" | shasum -a 256 -c - >/dev/null
unzip -q "$TMP/rife.zip" "$NAME/rife-ncnn-vulkan" "$NAME/rife-v4.6/*" "$NAME/LICENSE" -d "$TMP"
mkdir -p "$DEST"
cp -R "$TMP/$NAME/rife-ncnn-vulkan" "$TMP/$NAME/rife-v4.6" "$TMP/$NAME/LICENSE" "$DEST/"
chmod +x "$DEST/rife-ncnn-vulkan"
echo "RIFE $VERSION installed: $DEST"
