#!/usr/bin/env bash
# Download the optional Piper TTS voices (Hindi + Telugu, ~63 MB each) into ./piper_voices
set -euo pipefail

BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main"
DEST="${PIPER_VOICE_DIR:-$(cd "$(dirname "$0")/.." && pwd)/piper_voices}"
mkdir -p "$DEST"

download() {
  local path="$1" name="$2"
  for ext in onnx onnx.json; do
    if [ -f "$DEST/$name.$ext" ]; then
      echo "✓ $name.$ext already present"
    else
      echo "↓ $name.$ext"
      curl -fL --retry 3 -o "$DEST/$name.$ext.part" "$BASE_URL/$path/$name.$ext"
      mv "$DEST/$name.$ext.part" "$DEST/$name.$ext"
    fi
  done
}

download "hi/hi_IN/rohan/medium" "hi_IN-rohan-medium"
download "te/te_IN/maya/medium" "te_IN-maya-medium"
echo "Voices saved to $DEST"
