#!/usr/bin/env zsh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_FILE="$SCRIPT_DIR/control_panel.py"

if [ -x "$SCRIPT_DIR/.venv/bin/streamlit" ]; then
  "$SCRIPT_DIR/.venv/bin/streamlit" run "$APP_FILE"
else
  # Fallback to global streamlit if venv isn't active
  streamlit run "$APP_FILE"
fi
