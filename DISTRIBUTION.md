# Distributing Phoenix Probate (for you, the sender)

This explains how to turn the project into something you can hand to someone so
the scraper runs on **their** Mac. There are two ways; the first is recommended.

---

## Recommended: a DMG with a double-click launcher

The recipient mounts the DMG, drags out a folder, and double-clicks one file.
On first run it quietly builds its own private Python environment and opens a
friendly control panel in their browser — no Terminal commands, no editing code.

### Steps (run on your Mac)

1. **Put your API keys in `.env`** (so the recipient needs zero setup):
   ```bash
   cp .env.example .env
   # then edit .env and fill in AZ_ENDPOINT, AZ_KEY, OPENAI_API_KEY
   ```
2. **Build the DMG:**
   ```bash
   bash dist_tools/make_dmg.sh
   ```
   This creates **`dist/PhoenixProbate.dmg`**.
3. **Send** `dist/PhoenixProbate.dmg`. The recipient follows `SETUP.md`
   (also printed as `START HERE.txt` inside the DMG).

Build **without** bundling your keys (recipient supplies their own in the
sidebar):
```bash
bash dist_tools/make_dmg.sh --no-keys
```

### What the recipient needs
- A Mac, Google Chrome, and internet. Nothing else — Python is set up for them
  automatically on first launch.

---

## ⚠️ About bundling your API keys ("same db / everything is ok")

Bundling `.env` is the most convenient option and matches "same keys/DB is
fine." Be aware:

- The recipient's scraping runs **on your Azure and OpenAI accounts** and
  counts against your usage/billing.
- A technical recipient could open `.env` and read the keys.

If that's acceptable for someone you trust, bundle the keys. Otherwise use
`--no-keys` and have them paste their own keys into the app's sidebar (it saves
them locally to their own `.env`). You can rotate/disable the keys anytime in
the Azure and OpenAI dashboards.

The "database" is the file `data/probate_records.csv`. It's bundled so the
recipient starts with your existing records and duplicates are skipped. Each
person's copy is independent — there's no shared live database, which is fine
for this workflow.

---

## Advanced: a fully standalone `.app` (no Python at all)

If you want a true double-click app with nothing to install:

```bash
bash dist_tools/make_app.sh   # builds dist/Phoenix Probate.app
```

This uses PyInstaller (`dist_tools/PhoenixProbate.spec`, entry `dist_tools/entry.py`).
It's marked experimental because Streamlit + PyInstaller occasionally needs
small tweaks for a given machine — **test the built app before sending**.

### Gatekeeper / notarization
Unsigned apps and launchers trigger a macOS warning. Options:
- **Easiest:** tell the recipient to right-click → **Open** the first time
  (covered in `SETUP.md`).
- **Polished:** sign and notarize with an **Apple Developer ID** ($99/yr):
  ```bash
  codesign --deep --force --options runtime \
    --sign "Developer ID Application: YOUR NAME (TEAMID)" "dist/Phoenix Probate.app"
  xcrun notarytool submit dist/PhoenixProbate-app.dmg \
    --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PW --wait
  xcrun stapler staple "dist/Phoenix Probate.app"
  ```

---

## Notes & limitations
- **Mac to Mac.** The launcher and build scripts target macOS (matching the
  scraper's Chrome workflow). The recipient should be on a Mac.
- **The login/captcha is manual by design** — the site requires a human to log
  in and solve a captcha. The app opens Chrome to the right page and waits; the
  person clicks through once, then scraping is automatic.
- **First launch needs internet** to install components (recommended path) or
  to fetch the matching ChromeDriver (Selenium does this automatically and
  caches it).
- Keys, `.venv/`, `token.json`, and `logs/` are git-ignored and never
  committed.
