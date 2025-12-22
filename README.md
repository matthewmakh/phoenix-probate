# NY Surrogate Court Probate Scraper

This script automates searching the NY Surrogate Court site after you manually solve the captcha, then downloads the "Probate Petition" PDF for each case and uploads it to Google Drive.

## What it does
- Attaches to your running Chrome (via remote debugging) so it uses your cookies/profile
- Fills out the search form (court, proceeding, date range)
- Opens each result in a new tab, finds the Probate Petition document, auto-downloads it
- Saves to `~/Downloads/ny-probate` and uploads to Drive under folder `Probate_Petitions`

## Requirements
- macOS with Google Chrome installed
- Python 3.12 (a `.venv` is already configured for this workspace)
- Packages: `selenium`, `google-api-python-client`, `google-auth`, `google-auth-oauthlib` (see `requirements.txt`)
- A Google account to authorize Drive access on first run

## One‑time setup
1) Install Python dependencies (if you recreate the venv)

```zsh
/Users/matthewmakh/PycharmProjects/TyeNY/Phoenix\ Realty\ -\ Probate/.venv/bin/python -m pip install -r requirements.txt
```

2) Ensure ChromeDriver compatibility
- This script launches Selenium in **attach mode** to your existing Chrome via DevTools, so you typically don't need to manage chromedriver manually if your Selenium can locate it.
- If Selenium complains about driver, install the matching ChromeDriver or use `webdriver-manager`.

## How to run
1) Start Chrome with remote debugging and a dedicated user-data-dir (avoid permission issues):

```zsh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/chrome-remote"
```

Notes:
- Keep this Chrome window open. Log in or solve captcha at:
  https://websurrogates.nycourts.gov/Home/AuthenticatePage
- Click into File Search so the search page is loaded in that window.

2) In a new terminal, run the scraper from the project folder:

```zsh
/Users/matthewmakh/PycharmProjects/TyeNY/Phoenix\ Realty\ -\ Probate/.venv/bin/python probate_scraper.py
```

The script will:
- Attach to Chrome at `127.0.0.1:9222`
- Fill Court / Proceeding / Date range from constants at top of the file
- Submit the search, open each case in new tabs
- Download any "Probate Petition" PDFs to `~/Downloads/ny-probate`
- Upload to Google Drive (first run opens a browser to grant permission; token saved to `token.json`)

## Extracting data from PDFs and maintaining a CSV

Use `pdf_data_extractor.py` to parse PDFs in a folder and append/update a CSV "sheet" at `data/probate_records.csv`.

Run it with zero flags—by default it will process `~/Downloads/ny-probate` and upsert into `data/probate_records.csv`:

```zsh
/Users/matthewmakh/PycharmProjects/TyeNY/Phoenix\ Realty\ -\ Probate/.venv/bin/python pdf_data_extractor.py
```

Examples:

```zsh
# Single PDF -> JSON to stdout, and upsert into data/probate_records.csv
/Users/matthewmakh/PycharmProjects/TyeNY/Phoenix\ Realty\ -\ Probate/.venv/bin/python pdf_data_extractor.py \
  /path/to/file.pdf

# Directory mode -> JSONL to file and upsert into CSV
/Users/matthewmakh/PycharmProjects/TyeNY/Phoenix\ Realty\ -\ Probate/.venv/bin/python pdf_data_extractor.py \
  --dir "$HOME/Downloads/ny-probate" --out extracted.jsonl

# Customize CSV path or disable CSV
/Users/matthewmakh/PycharmProjects/TyeNY/Phoenix\ Realty\ -\ Probate/.venv/bin/python pdf_data_extractor.py \
  --dir "$HOME/Downloads/ny-probate" --csv-out data/custom.csv
/Users/matthewmakh/PycharmProjects/TyeNY/Phoenix\ Realty\ -\ Probate/.venv/bin/python pdf_data_extractor.py \
  --dir "$HOME/Downloads/ny-probate" --no-csv
```

Dedup rule: rows are upserted using the key (file_number + docket), or just file_number if docket is missing; otherwise it falls back to source_file.

## Streamlit dashboard

You can explore the aggregated CSV with a simple dashboard:

Quick start (no extra flags needed):

```zsh
/Users/matthewmakh/PycharmProjects/TyeNY/Phoenix\ Realty\ -\ Probate/.venv/bin/python -m pip install -r requirements.txt
/Users/matthewmakh/PycharmProjects/TyeNY/Phoenix\ Realty\ -\ Probate/.venv/bin/streamlit run streamlit_app.py
```

The app will automatically load `data/probate_records.csv`. In the sidebar, you can set a custom CSV path if needed and click "Reload CSV" to refresh the view. You can filter the data (County, Case Type, name search). The table supports sorting and scrolling. Bar charts show counts by County and Case Type.

## Configuration
Edit the constants near the top of `probate_scraper.py`:
- `COURT_VALUE` (Queens = 41)
- `PROCEEDING_TEXT` (e.g., "PROBATE PETITION")
- `DATE_FROM`, `DATE_TO` (MM/DD/YYYY)
- `DEBUG_ADDRESS` (default `127.0.0.1:9222`)
- `DOWNLOAD_DIR` (defaults to `~/Downloads/ny-probate`)
- Google Drive: `DRIVE_FOLDER_NAME` or set env `GOOGLE_DRIVE_FOLDER_ID` to upload into a specific folder

## Troubleshooting
- If Selenium cannot attach: confirm Chrome is running with `--remote-debugging-port=9222` and no firewall is blocking localhost.
- If downloads don’t trigger: ensure Chrome's download prefs allow downloads and "Always open PDFs externally" is enabled (the script sets prefs, but an existing profile policy may override).
- If the script says it only works with `--user-data-dir=$HOME/chrome-remote`, that typically means your default profile directory is locked by a running Chrome. Use a dedicated dir as shown above.
- If Drive auth fails, delete `token.json` and retry; or supply your own OAuth client via environment variables or `credentials.json`.
