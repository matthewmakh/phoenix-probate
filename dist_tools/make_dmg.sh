#!/bin/bash
# ===========================================================================
# Build a sendable Phoenix Probate.dmg  (run this ON YOUR MAC)
# ---------------------------------------------------------------------------
# Produces dist/PhoenixProbate.dmg containing a "Phoenix Probate" folder with
# the app + double-click launcher. The recipient drags the folder out of the
# DMG and double-clicks "Launch Phoenix Probate.command".
#
# If a real ".env" exists, its API keys are BUNDLED so the recipient needs zero
# setup. Pass --no-keys to ship the placeholder template instead.
#
#   bash dist_tools/make_dmg.sh            # bundle your keys (if .env exists)
#   bash dist_tools/make_dmg.sh --no-keys  # ship without your keys
# ===========================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VOL_NAME="Phoenix Probate"
STAGE_PARENT="$(mktemp -d)"
APP_FOLDER="$STAGE_PARENT/$VOL_NAME"   # the single folder the recipient drags out
OUT_DIR="$ROOT/dist"
DMG_PATH="$OUT_DIR/PhoenixProbate.dmg"
BUNDLE_KEYS=1
[ "${1:-}" = "--no-keys" ] && BUNDLE_KEYS=0

echo "==> Staging app files…"
mkdir -p "$APP_FOLDER" "$OUT_DIR"

# Copy the source, skipping environments, caches, git, and build output.
rsync -a \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.cache' \
  --exclude 'dist' \
  --exclude 'build' \
  --exclude '.build-venv' \
  --exclude 'ocr_json' \
  --exclude 'ocr_text' \
  --exclude 'structured_json' \
  --exclude 'logs' \
  --exclude 'token.json' \
  "$ROOT/" "$APP_FOLDER/"

# Decide which config file ships.
if [ "$BUNDLE_KEYS" = "1" ]; then
  if [ ! -f "$ROOT/.env" ]; then
    echo "ERROR: No .env file found, so there are no keys to bundle." >&2
    echo "  Run:  cp .env.example .env   then edit .env and add your keys." >&2
    echo "  (Or build without keys:  bash dist_tools/make_dmg.sh --no-keys )" >&2
    exit 1
  fi
  # Refuse to ship a .env that still has placeholder values like AZ_KEY=<your-azure-key>.
  if grep -Eq '=[[:space:]]*<' "$ROOT/.env"; then
    echo "ERROR: .env still contains placeholder values (e.g. <your-azure-key>)." >&2
    echo "  Open it and paste your real keys, then run this again:" >&2
    echo "      open -e \"$ROOT/.env\"" >&2
    echo "  (Or build without keys:  bash dist_tools/make_dmg.sh --no-keys )" >&2
    exit 1
  fi
  echo "==> Bundling your API keys from .env (recipient needs no setup)."
  cp "$ROOT/.env" "$APP_FOLDER/.env"
else
  echo "==> Shipping placeholder config (recipient enters their own keys)."
  rm -f "$APP_FOLDER/.env"
  cp "$ROOT/.env.example" "$APP_FOLDER/.env.example"
fi

chmod +x "$APP_FOLDER/Launch Phoenix Probate.command" 2>/dev/null || true

# A short read-me at the DMG root (next to the folder) so the recipient knows what to do.
cat > "$STAGE_PARENT/START HERE.txt" <<'TXT'
Phoenix Probate
===============

1. Drag the "Phoenix Probate" folder to your Applications (or Desktop) folder.
2. Open that folder and double-click  "Launch Phoenix Probate.command".
3. First time only: if macOS says it is from an unidentified developer,
   right-click the launcher, choose Open, then click Open in the dialog.
4. A web browser tab opens with the app. Follow Step 1 → 2 → 3 on screen.

Keep the Terminal window that appears open while you work.
TXT

echo "==> Building DMG…"
rm -f "$DMG_PATH"
hdiutil create \
  -volname "$VOL_NAME" \
  -srcfolder "$STAGE_PARENT" \
  -ov -format UDZO \
  "$DMG_PATH"

# Best-effort: strip quarantine so a locally-opened copy is smoother to test.
xattr -dr com.apple.quarantine "$DMG_PATH" 2>/dev/null || true

echo
echo "✅ Done:  $DMG_PATH"
echo "   Send that file. (Recipient: see START HERE.txt inside the DMG.)"
