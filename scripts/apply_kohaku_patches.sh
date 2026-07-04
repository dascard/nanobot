#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KT_DIR="${KT_DIR:-$ROOT_DIR/vendor/KohakuTerrarium}"
PATCH_FILE="${PATCH_FILE:-$ROOT_DIR/patches/kohakuterrarium/stream-message-flag.patch}"

if [[ ! -d "$KT_DIR/.git" && ! -f "$KT_DIR/.git" ]]; then
  echo "KohakuTerrarium submodule not found: $KT_DIR" >&2
  echo "Run: git submodule update --init --recursive" >&2
  exit 1
fi

if [[ ! -f "$PATCH_FILE" ]]; then
  echo "KohakuTerrarium patch not found: $PATCH_FILE" >&2
  exit 1
fi

if grep -q 'conversation.append("user", user_content, stream=user_stream)' "$KT_DIR/src/kohakuterrarium/core/controller.py" \
  && grep -q "stream=stream" "$KT_DIR/src/kohakuterrarium/llm/message.py"; then
  echo "KohakuTerrarium patch already applied"
  exit 0
fi

if git -C "$KT_DIR" apply --check "$PATCH_FILE"; then
  git -C "$KT_DIR" apply "$PATCH_FILE"
  echo "Applied KohakuTerrarium patch: $PATCH_FILE"
  exit 0
fi

echo "Failed to apply KohakuTerrarium patch: $PATCH_FILE" >&2
git -C "$KT_DIR" status --short >&2 || true
exit 1
