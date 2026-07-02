#!/bin/bash
# ===========================================================================
# Phoenix Probate — double-click launcher (macOS)
# ---------------------------------------------------------------------------
# First run: creates an isolated Python environment and installs everything.
# Every run after that: starts instantly and opens the control panel in your
# web browser. Keep the Terminal window that appears OPEN while you work —
# closing it stops the app.
# ===========================================================================
set -euo pipefail

# Always work from the folder this script lives in (even when double-clicked).
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

VENV="$APP_DIR/.venv"
PYBIN="$VENV/bin/python"
HASH_FILE="$VENV/.deps_hash"

say() { printf "\n\033[1;36m%s\033[0m\n" "$*"; }
warn() { printf "\n\033[1;33m%s\033[0m\n" "$*"; }

say "Phoenix Probate — starting up…"

# --- 0. Refuse to run from a read-only spot (e.g. inside the mounted DMG) ---
if ! touch "$APP_DIR/.perm_test" 2>/dev/null; then
  warn "This app is running from a read-only location, so it can't set itself up here."
  echo "You're probably running it straight from the disk image (a /Volumes path)."
  echo
  echo "Fix: drag the \"Phoenix Probate\" folder to your Applications or Desktop"
  echo "folder, then open it from there and double-click this launcher again."
  echo
  read -r -p "Press Return to close." _ || true
  exit 1
fi
rm -f "$APP_DIR/.perm_test"

# --- 1. Make sure Python 3 is available -----------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  warn "Python 3 is required but was not found."
  echo "A macOS dialog may appear to install the Command Line Tools — please"
  echo "click Install, wait for it to finish, then run this launcher again."
  xcode-select --install >/dev/null 2>&1 || true
  echo "If no dialog appears, download Python from https://www.python.org/downloads/macos/"
  read -r -p "Press Return to close." _ || true
  exit 1
fi

# --- 2. Create the isolated environment on first run ----------------------
if [ ! -x "$PYBIN" ]; then
  say "First-time setup: creating a private Python environment (one minute)…"
  python3 -m venv "$VENV"
fi

# --- 3. Install / update dependencies only when requirements change --------
REQ_HASH="$(shasum requirements.txt 2>/dev/null | awk '{print $1}')"
if [ ! -f "$HASH_FILE" ] || [ "$(cat "$HASH_FILE" 2>/dev/null)" != "$REQ_HASH" ]; then
  say "Installing components (this only happens once, may take a few minutes)…"
  "$PYBIN" -m pip install --upgrade pip >/dev/null
  "$PYBIN" -m pip install -r requirements.txt
  echo "$REQ_HASH" > "$HASH_FILE"
else
  say "Components already installed — skipping setup."
fi

# --- 4. Make sure a config file exists (keys may be pre-filled) ------------
if [ ! -f "$APP_DIR/.env" ] && [ -f "$APP_DIR/.env.example" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  warn "Created .env — if the app shows a missing-key warning, add your keys in the sidebar."
fi

# --- 5. Launch the control panel ------------------------------------------
say "Opening Phoenix Probate in your web browser…"
echo "If a browser tab does not open, go to:  http://localhost:8501"
echo "(Keep this Terminal window open while you use the app.)"
exec "$PYBIN" -m streamlit run control_panel.py \
  --server.headless=false \
  --server.port=8501 \
  --browser.gatherUsageStats=false
