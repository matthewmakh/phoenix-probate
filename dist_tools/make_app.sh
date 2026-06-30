#!/bin/bash
# ===========================================================================
# ADVANCED / EXPERIMENTAL — build a fully standalone "Phoenix Probate.app"
# that needs NO Python on the recipient's machine.  (Run this ON YOUR MAC.)
#
# The recommended, well-tested path is the double-click launcher DMG:
#     bash dist_tools/make_dmg.sh
# Use this only if you want a true no-Terminal .app and are willing to test
# it. Streamlit + PyInstaller sometimes needs small per-machine tweaks.
# ===========================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Creating a clean build environment…"
python3 -m venv .build-venv
# shellcheck disable=SC1091
source .build-venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt
pip install pyinstaller

echo "==> Running PyInstaller…"
pyinstaller --noconfirm --clean dist_tools/PhoenixProbate.spec

APP="dist/Phoenix Probate.app"
if [ -d "$APP" ]; then
  echo
  echo "✅ Built: $APP"
  echo "   1. Test it: open \"$APP\"  (a browser tab should open)."
  echo "   2. Wrap it in a DMG to send:"
  echo "      hdiutil create -volname \"Phoenix Probate\" -srcfolder \"$APP\" \\"
  echo "        -ov -format UDZO dist/PhoenixProbate-app.dmg"
  echo
  echo "   Note: unsigned apps trigger Gatekeeper. The recipient right-clicks"
  echo "   the app, chooses Open, then Open again. To sign/notarize you need an"
  echo "   Apple Developer ID — see DISTRIBUTION.md."
else
  echo "❌ Build did not produce the .app — check the PyInstaller output above."
  exit 1
fi
