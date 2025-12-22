#!/usr/bin/env bash
set -euo pipefail

# Launch a clean Chrome instance for Selenium remote debugging on macOS.
# Uses a dedicated user-data-dir so it won't collide with your main browser.
# After launching, verify with: lsof -nP -iTCP:9222 -sTCP:LISTEN

CHROME_APP="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_DIR="${HOME}/chrome-remote"
PORT=9222

# Kill any leftover debug instance bound to the same user-data-dir (optional)
# pkill -f "${USER_DIR}" || true

mkdir -p "${USER_DIR}"

"${CHROME_APP}" \
  --remote-debugging-port=${PORT} \
  --user-data-dir="${USER_DIR}" \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=AutofillServerCommunication \
  --disable-breakpad \
  --enable-logging=stderr \
  --v=0 &

echo "Started debug Chrome on ws://127.0.0.1:${PORT} (user-data-dir=${USER_DIR})"
