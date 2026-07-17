#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""

first run this in terminal

"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/Library/Application Support/Google/Chrome" \
  --profile-directory="Default"

  
  for some reason now it only works with this:
     
 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/chrome-remote"

  figure out why this is the case



then visit this: https://websurrogates.nycourts.gov/Home/AuthenticatePage

then click into file search

then run the file


NY Surrogate Court scraper after captcha is solved.

- Attaches to Chrome with your profile
- Fills out Court, Proceeding, Date range
- Submits search
- Opens each case in a new tab
- Scrapes header + executor info
- If "Probate Petition" is in Documents table:
    * Auto-downloads PDF
    * Renames file to <FileNumber>_Probate_Petition.pdf
"""

import os
import io
import csv
import glob
import time
import random
import mimetypes
from typing import Optional

# --- Selenium ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

# --- Google Drive (OAuth user consent) ---
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# =========================
# Config (edit as needed)
# =========================
COURT_VALUE = os.environ.get("PB_COURT_VALUE", "41")  # Queens County = 41
# Single switch for the target document type. Options: "PROBATE", "VOLUNTARY"
# Overridable from the control panel / environment (PB_SELECTED_DOC = "PROBATE" or "VOLUNTARY")
SELECTED_DOC = os.environ.get("PB_SELECTED_DOC", "VOLUNTARY").upper()

# Central mapping for proceeding dropdown text, document row match, and filename suffix
DOC_OPTIONS = {
    "PROBATE": {
        "proceeding": "PROBATE PETITION",
        "doc_label": "PROBATE PETITION",
        "filename": "Probate_Petition",
    },
    "VOLUNTARY": {
        "proceeding": "VOLUNTARY ADMIN AFFIDAVIT (ARTICLE 13) WITHOUT WILL",
        # Use a stable substring to match row labels even if site text varies slightly
        "doc_label": "VOLUNTARY ADMIN AFFIDAVIT",
        "filename": "Voluntary_Admin_Affidavit",
    },
}

# Derived proceeding text for the search form from the selection above
PROCEEDING_TEXT = DOC_OPTIONS[SELECTED_DOC]["proceeding"]
DATE_FROM = os.environ.get("PB_DATE_FROM", "11/01/2025")
DATE_TO   = os.environ.get("PB_DATE_TO", "11/30/2025")

# Pagination controls: set to 0 to disable pagination, or specify pages to skip (comma-separated)
# Examples:
#   PAGINATION_ENABLED = True  # process all pages
#   SKIP_PAGES = "1,3"         # skip pages 1 and 3
#   SKIP_PAGES = ""            # don't skip any pages
PAGINATION_ENABLED = os.environ.get("PB_PAGINATION_ENABLED", "1") not in ("0", "false", "False", "")
SKIP_PAGES = os.environ.get("PB_SKIP_PAGES", "")  # comma-separated page numbers to skip, e.g. "1,3,5"

# CSV skip configuration: skip cases if file number already exists in CSV
SKIP_EXISTING_IN_CSV = True
CSV_PATH = os.environ.get(
    "PB_CSV_PATH", os.path.join(os.path.dirname(__file__), "data", "probate_records.csv")
)

DEBUG_ADDRESS = os.environ.get("PB_DEBUG_ADDRESS", "127.0.0.1:9222")
WAIT_SEC = 30
LIST_WAIT_SEC = 20
KEEP_BROWSER_OPEN = True

# Dedicated folder to avoid mixing with other PDFs
DOWNLOAD_DIR = os.environ.get(
    "PB_DOWNLOAD_DIR", os.path.join(os.path.expanduser("~/Downloads"), "ny-probate")
)
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Slow-and-steady pacing between cases to reduce bursts against Azure
INTER_CASE_DELAY_SEC = float(os.environ.get("INTER_CASE_DELAY_SEC", "2.0"))

# Drive target
DRIVE_FOLDER_NAME = "Probate_Petitions"
# Optional: if you want to force a specific folder, put its ID here (or leave empty)
DRIVE_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "").strip()

# OAuth scope:
# Use full Drive so we can find an existing folder by name.
# If you prefer least-privilege, switch to drive.file, but then ensure the script creates the folder.
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Read client id/secret from env only; otherwise require credentials.json next to this script.
# This avoids using an org-restricted OAuth client that could block access.
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()

# Allow disabling Drive upload (set ENABLE_DRIVE_UPLOAD=0 to skip uploading)
ENABLE_DRIVE_UPLOAD = False

# Enable immediate OCR of the first page right after download (Azure-only)
ENABLE_AZURE_OCR_AFTER_DOWNLOAD = True

# Caches
_drive_service = None
_drive_folder_id_cache = None

# Simple run stats
STATS = {"downloads": 0, "ocr_ok": 0, "ocr_err": 0}

# =========================
# Utility
# =========================
def log(msg: str):
    print(msg, flush=True)

def human_sleep(a: float, b: float):
    time.sleep(random.uniform(a, b))

def clean(txt: Optional[str]) -> str:
    return " ".join((txt or "").split())

def safe_click(driver, element):
    """Click robustly: scroll the element to the center of the viewport, try a
    native click, and fall back to a JavaScript click if something overlays it.
    This avoids the 'element click intercepted' error seen on newer Chrome when
    a sticky header/banner covers the target."""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    except Exception:
        pass
    human_sleep(0.1, 0.25)
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)

# County mapping for known court values (extend as needed)
COURT_TO_COUNTY = {
    "41": "Queens",
    "30": "Nassau",
    "24": "Kings",
    "31": "New York",
    "3": "Bronx"
}

# Cache for existing file numbers in CSV
_existing_file_numbers = None

def load_existing_file_numbers():
    """Load all file numbers from the CSV into a set for fast lookup."""
    global _existing_file_numbers
    if _existing_file_numbers is not None:
        return _existing_file_numbers
    
    _existing_file_numbers = set()
    if not SKIP_EXISTING_IN_CSV:
        return _existing_file_numbers
    
    if not os.path.exists(CSV_PATH):
        log(f"[CSV] File not found: {CSV_PATH}, will not skip any cases")
        return _existing_file_numbers
    
    try:
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                file_num = row.get("File number", "").strip()
                if file_num:
                    _existing_file_numbers.add(file_num)
        log(f"[CSV] Loaded {len(_existing_file_numbers)} existing file numbers from CSV")
    except Exception as e:
        log(f"[CSV] Error reading CSV: {e}")
    
    return _existing_file_numbers

def is_file_number_in_csv(file_number: str) -> bool:
    """Check if a file number already exists in the CSV."""
    existing = load_existing_file_numbers()
    return file_number.strip() in existing

def add_file_number_to_cache(file_number: str) -> None:
    """Add a file number to the cache after processing."""
    global _existing_file_numbers
    if _existing_file_numbers is not None:
        _existing_file_numbers.add(file_number.strip())

def selected_county_name() -> str:
    return COURT_TO_COUNTY.get(COURT_VALUE, "")

# =========================
# Browser attach
# =========================
def attach_driver(debug_address: str = DEBUG_ADDRESS):
    opts = Options()
    opts.add_experimental_option("debuggerAddress", debug_address)
    # When attaching to an already-running Chrome (debuggerAddress), the browser
    # is already launched, so launch-time options like "prefs" are rejected by
    # newer ChromeDriver ("unrecognized chrome option: prefs"). The download
    # folder is set at runtime via CDP (Page.setDownloadBehavior) just below.
    driver = webdriver.Chrome(options=opts)
    wait = WebDriverWait(driver, WAIT_SEC)
    # Ensure downloads are allowed to our folder even when attaching to an existing
    # Chrome. Try the browser-wide command first (more reliable on newer Chrome),
    # then the per-page one; both are best-effort.
    for _cmd in ("Browser.setDownloadBehavior", "Page.setDownloadBehavior"):
        try:
            driver.execute_cdp_cmd(_cmd, {"behavior": "allow", "downloadPath": DOWNLOAD_DIR})
        except Exception:
            # Not fatal; some driver versions gate these differently
            pass
    return driver, wait

# =========================
# Tab/frame helpers (NEW)
# =========================
def switch_to_websurrogates_tab(driver) -> bool:
    """Switch to the tab that has the WebSurrogates File Search URL, if present."""
    targets = []
    for h in driver.window_handles:
        try:
            driver.switch_to.window(h)
            url = driver.current_url
        except Exception:
            continue
        if "websurrogates.nycourts.gov" in url:
            # Prefer FileSearch page if multiple
            score = 2 if "File/FileSearch" in url or "FileSearch" in url else 1
            targets.append((score, h))
    if not targets:
        return False
    # Pick the highest score
    targets.sort(reverse=True)
    driver.switch_to.window(targets[0][1])
    return True


def switch_into_frame_with(driver, by: By, value: str, max_depth: int = 2) -> bool:
    """
    Try default content first; if not found, iterate through iframes/frames and
    switch into the first frame that contains the target element. Shallow search
    to avoid heavy recursion.
    """
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    # Default content check
    try:
        driver.find_element(by, value)
        return True
    except Exception:
        pass

    # One-level frame scan
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    for fr in frames:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(fr)
            driver.find_element(by, value)
            return True
        except Exception:
            continue

    # Optional: second level (light scan)
    if max_depth > 1:
        for fr in frames:
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(fr)
                sub_frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
                for sub in sub_frames:
                    try:
                        driver.switch_to.frame(sub)
                        driver.find_element(by, value)
                        return True
                    except Exception:
                        driver.switch_to.parent_frame()
                        continue
            except Exception:
                continue

    # Not found
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return False

# =========================
# Google Drive (OAuth)
# =========================
def _client_config_from_env_or_file():
    if GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET:
        return {
            "installed": {
                "client_id": GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"]
            }
        }
    # fallback: credentials.json next to this script
    cred_path = os.path.join(os.path.dirname(__file__), "credentials.json")
    if os.path.exists(cred_path):
        import json
        with open(cred_path, "r") as f:
            cfg = json.load(f)
        inst = cfg.get("installed") or cfg.get("web")
        if inst and inst.get("client_id") and inst.get("client_secret"):
            return {
                "installed": {
                    "client_id": inst["client_id"],
                    "client_secret": inst["client_secret"],
                    "auth_uri": inst.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
                    "token_uri": inst.get("token_uri", "https://oauth2.googleapis.com/token"),
                    "redirect_uris": inst.get("redirect_uris", ["http://localhost"])
                }
            }
    raise RuntimeError("Provide GOOGLE_OAUTH_CLIENT_ID/SECRET in environment or a credentials.json in the script directory.")

def get_drive_service():
    """Build and cache a Drive service using user OAuth. Saves token.json for reuse."""
    global _drive_service
    if _drive_service is not None:
        return _drive_service

    creds: Optional[Credentials] = None
    token_path = "token.json"

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_config(_client_config_from_env_or_file(), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    _drive_service = build("drive", "v3", credentials=creds)
    return _drive_service

def ensure_drive_folder(service, folder_name: str) -> str:
    """Return folder ID for `folder_name` (create if missing)."""
    escaped = folder_name.replace("'", "\\'")
    query = (
        "mimeType='application/vnd.google-apps.folder' "
        "and trashed=false "
        f"and name='{escaped}'"
    )
    resp = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name)",
        pageSize=5
    ).execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]

    meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    new_folder = service.files().create(body=meta, fields="id").execute()
    return new_folder["id"]

def resolve_target_folder_id(service) -> str:
    """Use explicit folder ID if provided, else find/create by name."""
    if DRIVE_FOLDER_ID:
        return DRIVE_FOLDER_ID
    return ensure_drive_folder(service, DRIVE_FOLDER_NAME)

def upload_file_to_drive(service, file_path: str, folder_id: Optional[str] = None):
    """Upload a local file to Drive under folder_id (if provided). Returns file id."""
    mime_type, _ = mimetypes.guess_type(file_path)
    mime_type = mime_type or "application/octet-stream"
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)

    metadata = {"name": os.path.basename(file_path)}
    if folder_id:
        metadata["parents"] = [folder_id]

    created = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    file_id = created.get("id")
    log(f"🟢 Uploaded to Drive as {file_id}")
    if created.get("webViewLink"):
        log(f"    Open: {created['webViewLink']}")
    return file_id

# =========================
# Download handling (NEW)
# =========================
def wait_for_download_and_rename(file_number: str, timeout: int = 90):
    """
    Wait for the *newly triggered* download in DOWNLOAD_DIR, then rename it
    to <file_number>_Probate_Petition.pdf and upload it to Drive.
    Ignores any PDFs that were present before the click.
    """
    global _drive_folder_id_cache
    start = time.time()

    # Snapshot existing files to only consider new ones
    preexisting = set(os.listdir(DOWNLOAD_DIR))
    log(f"[dl] Snapshot has {len(preexisting)} files in {DOWNLOAD_DIR}")

    new_pdf_path = None
    candidate_crdownload = None
    tmp_sizes = {}
    reported = set()

    while time.time() - start < timeout:
        current = set(os.listdir(DOWNLOAD_DIR))
        new_names = [n for n in current if n not in preexisting]

        # Log newly observed files once
        unseen = [n for n in new_names if n not in reported]
        if unseen:
            log(f"[dl] New files detected: {', '.join(unseen)}")
            reported.update(unseen)

        # See if a fresh download started
        for name in new_names:
            if name.endswith(".crdownload"):
                candidate_crdownload = os.path.join(DOWNLOAD_DIR, name)
                log(f"[dl] Detected crdownload: {os.path.basename(candidate_crdownload)}")

        # If a crdownload finished, locate the final PDF
        if candidate_crdownload and not os.path.exists(candidate_crdownload):
            stem = os.path.splitext(os.path.basename(candidate_crdownload))[0]
            candidate_pdf = os.path.join(DOWNLOAD_DIR, stem)
            log(f"[dl] crdownload finished: looking for final PDF '{os.path.basename(candidate_pdf)}'")
            if candidate_pdf.lower().endswith(".pdf") and os.path.exists(candidate_pdf):
                new_pdf_path = candidate_pdf
                log(f"[dl] Found finalized PDF: {os.path.basename(new_pdf_path)}")
            else:
                # Fallback: any new PDF since snapshot
                for name in os.listdir(DOWNLOAD_DIR):
                    if name.endswith(".pdf") and name not in preexisting:
                        new_pdf_path = os.path.join(DOWNLOAD_DIR, name)
                        log(f"[dl] Fallback detected new PDF: {os.path.basename(new_pdf_path)}")
                        break

        # Very fast/cached downloads: no crdownload seen; accept any new PDF
        if not candidate_crdownload:
            for name in new_names:
                if name.endswith(".pdf"):
                    new_pdf_path = os.path.join(DOWNLOAD_DIR, name)
                    log(f"[dl] New PDF without crdownload: {os.path.basename(new_pdf_path)}")
                    break

        # Handle .tmp downloads from viewer/streamed paths: detect when size stops changing
        if not new_pdf_path:
            for name in new_names:
                if name.lower().endswith('.tmp'):
                    p = os.path.join(DOWNLOAD_DIR, name)
                    try:
                        sz = os.path.getsize(p)
                        prev = tmp_sizes.get(name)
                        now = time.time()
                        STABLE_SECS = 2.5
                        if not prev:
                            tmp_sizes[name] = (sz, now)
                            log(f"[dl] Tracking tmp: {name} size={sz}")
                        elif sz != prev[0]:
                            log(f"[dl] tmp growing: {name} size {prev[0]} -> {sz}")
                            # Size changed; reset timer
                            tmp_sizes[name] = (sz, now)
                        else:
                            # Size unchanged; do NOT refresh timestamp so window can accumulate
                            if (now - prev[1]) > STABLE_SECS:
                                new_pdf_path = p
                                log(f"[dl] tmp stabilized: {name} size={sz} for >{STABLE_SECS}s; treating as complete")
                                break
                    except Exception:
                        continue

        if new_pdf_path:
            # Use selected filename suffix
            suffix = DOC_OPTIONS[SELECTED_DOC]["filename"]
            # Sanitize file number: replace / with - to avoid path issues
            safe_file_number = file_number.replace("/", "-").replace("\\", "-")
            final_path = os.path.join(DOWNLOAD_DIR, f"{safe_file_number}_{suffix}.pdf")
            # Move/rename
            try:
                log(f"[dl] Renaming '{os.path.basename(new_pdf_path)}' -> '{os.path.basename(final_path)}'")
                os.rename(new_pdf_path, final_path)
            except Exception as e_rename:
                log(f"[dl] Rename failed ({e_rename}); copying to final then removing source")
                try:
                    import shutil
                    shutil.copy2(new_pdf_path, final_path)
                    os.remove(new_pdf_path)
                except Exception as e:
                    log(f"⚠️ Could not move downloaded file: {e}")
                    final_path = new_pdf_path  # fallback

            log(f"✅ Saved {final_path}")
            # Tiny settle delay to let OS/Chrome finish closing handles
            time.sleep(0.2)

            # Upload to Drive (optional)
            if ENABLE_DRIVE_UPLOAD:
                try:
                    service = get_drive_service()
                    if not _drive_folder_id_cache:
                        _drive_folder_id_cache = resolve_target_folder_id(service)
                    upload_file_to_drive(service, final_path, _drive_folder_id_cache)
                except Exception as e:
                    log(f"⚠️ Drive upload failed or not configured: {e}")
            else:
                log("[i] Drive upload disabled (ENABLE_DRIVE_UPLOAD=0). Skipping upload.")

            # Optional: OCR first page immediately via Azure and append to CSV
            if ENABLE_AZURE_OCR_AFTER_DOWNLOAD:
                try:
                    ok = process_downloaded_pdf(final_path, site_file_number=file_number, county_name=selected_county_name())
                    if ok:
                        STATS["ocr_ok"] += 1
                    else:
                        STATS["ocr_err"] += 1
                except Exception as e:
                    STATS["ocr_err"] += 1
                    log(f"⚠️ Azure OCR after download failed: {e}")

            STATS["downloads"] += 1
            return final_path

        time.sleep(0.5)

    log("⚠️ Timed out waiting for the new download.")
    return None


def process_downloaded_pdf(final_path: str, site_file_number: Optional[str] = None, county_name: Optional[str] = None) -> bool:
    """Run Azure OCR for first page, use OpenAI LLM to extract structured data, and upsert to CSV."""
    from azure_ocr import ocr_first_page
    from llm_extractor import extract_file
    import csv

    # Run Azure OCR to get text from first page
    ocr_result = ocr_first_page(final_path)
    text_file_path = ocr_result.get("text_file")
    
    if not text_file_path or not os.path.exists(text_file_path):
        log(f"⚠️ OCR text file not found: {text_file_path}")
        return False
    
    # Use OpenAI LLM to extract structured fields from OCR text
    try:
        llm_data = extract_file(text_file_path)
    except Exception as e:
        log(f"⚠️ LLM extraction failed: {e}")
        return False
    
    if not llm_data:
        log("⚠️ LLM extraction returned no data")
        return False

    # Prepare CSV row. Use the absolute, module-level CSV_PATH so records always land
    # in the same file no matter which directory the app was launched from.
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    columns = [
        "County",
        "File number",
        "Date of death",
        "Decedent name",
        "Decedent address",
        "Executor/administrator name",
        "Executor/administrator phone",
        "Executor/administrator address",
        "Executor/administrator email",
    ]
    
    row = {
        "County": (county_name or llm_data.get("County") or ""),
        "File number": (site_file_number or llm_data.get("File number") or ""),
        "Date of death": (llm_data.get("Date of death") or ""),
        "Decedent name": (llm_data.get("Decedent name") or ""),
        "Decedent address": (llm_data.get("Decedent address") or ""),
        "Executor/administrator name": (llm_data.get("Executor/administrator name") or ""),
        "Executor/administrator phone": (llm_data.get("Executor/administrator phone") or ""),
        "Executor/administrator address": (llm_data.get("Executor/administrator address") or ""),
        "Executor/administrator email": (llm_data.get("Executor/administrator email") or ""),
    }

    # Upsert into CSV by file_number
    existing = []
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
            rdr = csv.DictReader(f)
            existing = [dict(r) for r in rdr]

    def _key(r: dict):
        fn = (r.get("File number") or "").strip()
        if fn:
            return ("fn", fn)
        # Fallback to decedent name if file number is missing
        return ("name", (r.get("Decedent name") or "").strip())

    key_new = _key(row)
    replaced = False
    for i, r in enumerate(existing):
        if _key(r) == key_new:
            existing[i] = {**r, **{k: ("" if row.get(k) is None else str(row.get(k))) for k in columns}}
            replaced = True
            break
    if not replaced:
        existing.append({k: ("" if row.get(k) is None else str(row.get(k))) for k in columns})

    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in existing:
            w.writerow({k: ("" if r.get(k) is None else str(r.get(k))) for k in columns})
    
    # Add to cache so we don't re-process this file number
    file_number = row.get("File number", "").strip()
    if file_number:
        add_file_number_to_cache(file_number)
    
    log("[i] OCR + CSV updated for dashboard")
    return True

# =========================
# Scraping logic
# =========================
def scrape_case(driver, root, idx, row):
    try:
        btn = row.find_element(By.CSS_SELECTOR, "button.ButtonAsLink")
        file_num = btn.get_attribute("value") or btn.text.strip()
        
        # Check if this file number already exists in CSV
        if SKIP_EXISTING_IN_CSV and is_file_number_in_csv(file_num):
            log(f"[{idx}] ⏭️  Skipping {file_num} - already exists in CSV")
            return
        
        log(f"[{idx}] Opening file {file_num} in a new tab")

        # Snapshot existing tabs so we can reliably detect the NEW one, even when
        # the user already has other tabs open in this Chrome window (otherwise we
        # might switch to — and later close — one of their existing tabs).
        handles_before = set(driver.window_handles)

        # Open result in NEW TAB by posting current form data with the row's button value
        driver.execute_script("""
            (function(btn){
                var form = btn.form || document.querySelector('form');
                if (!form) throw new Error('No form found for results button');
                var f = document.createElement('form');
                f.method = form.method || 'post';
                f.action = form.action;
                f.target = '_blank';
                var inp = document.createElement('input');
                inp.type='hidden';
                inp.name = btn.name || 'button';
                inp.value = btn.value;
                f.appendChild(inp);
                var tokens = form.querySelectorAll('input[type=hidden]');
                tokens.forEach(function(h){
                    if (h.name && h.value && h.name !== (btn.name || 'button')) {
                        var c = document.createElement('input');
                        c.type='hidden'; c.name=h.name; c.value=h.value;
                        f.appendChild(c);
                    }
                });
                document.body.appendChild(f);
                f.submit();
            })(arguments[0]);
        """, btn)
        # Tiny pause helps page script events settle
        human_sleep(0.15, 0.35)
        try:
            WebDriverWait(driver, 15).until(
                lambda d: len(set(d.window_handles) - handles_before) >= 1
            )
        except Exception:
            log(f"[{idx}] ⚠️ The case tab did not open (popup blocked?) — skipping this one.")
            return
        new_handles = [h for h in driver.window_handles if h not in handles_before]
        new_tab = new_handles[-1]
        driver.switch_to.window(new_tab)

        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        human_sleep(0.15, 0.35)

        header = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.ResultsDivBackgroundColor"))
        )
        # Ensure header inputs have populated values
        wait_local = WebDriverWait(driver, 15)
        wait_local.until(lambda d: header.find_element(By.CSS_SELECTOR, "input#Court").get_attribute("value"))
        wait_local.until(lambda d: header.find_element(By.CSS_SELECTOR, "input#FileNumber").get_attribute("value"))
        wait_local.until(lambda d: header.find_element(By.CSS_SELECTOR, "input#FileDetail_BuiltFileName").get_attribute("value"))

        court = header.find_element(By.CSS_SELECTOR, "input#Court").get_attribute("value")
        file_number = header.find_element(By.CSS_SELECTOR, "input#FileNumber").get_attribute("value")
        file_name = header.find_element(By.CSS_SELECTOR, "input#FileDetail_BuiltFileName").get_attribute("value")

        print("=" * 90)
        print(f"COURT: {court}")
        print(f"{file_number} - {file_name}")

        # Look for selected document in the Documents table and download
        target_found = False
        try:
            # Wait for at least one row to exist before scanning
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
            docs_rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
            for r in docs_rows:
                label = r.find_element(By.TAG_NAME, "td").text.strip().upper()
                target_label = DOC_OPTIONS[SELECTED_DOC]["doc_label"].upper()
                if target_label in label:
                    try:
                        probate_btn = r.find_element(By.CSS_SELECTOR, "button.ButtonAsLink")
                        # Ensure clickable before clicking to avoid intercept issues
                        WebDriverWait(driver, 10).until(EC.element_to_be_clickable(probate_btn))
                        safe_click(driver, probate_btn)
                        human_sleep(0.15, 0.35)
                        target_found = True
                        log(f"📥 {DOC_OPTIONS[SELECTED_DOC]['filename']} download triggered for {file_number}")
                        wait_for_download_and_rename(file_number)
                        break
                    except Exception:
                        log("⚠️ Target document found but no button available.")
        except Exception as e:
            log(f"⚠️ Could not parse documents table: {e}")

        if not target_found:
            log("❌ No target document for this case.")

        # Close tab and return
        driver.close()
        driver.switch_to.window(root)
        human_sleep(0.4, 0.8)

    except Exception as e:
        log(f"⚠️ Row {idx} failed: {type(e).__name__}: {e}")
        try:
            if driver.current_window_handle != root:
                driver.close()
                driver.switch_to.window(root)
        except Exception:
            try:
                driver.switch_to.window(root)
            except Exception:
                pass

# =========================
# Pagination
# =========================
def get_pagination_links(driver) -> list:
    """Extract all available page links from the pagination ul element.
    Returns a list of dicts: [{'page': 1, 'element': <link element>}, ...]
    Store the element reference so we can click it later.
    """
    try:
        # Bootstrap pagination list (robust to layout changes vs. an absolute XPath)
        ul = driver.find_element(By.CSS_SELECTOR, "ul.pagination")
        page_items = ul.find_elements(By.CSS_SELECTOR, "li.page-item a.page-link")
        pages = []
        for item in page_items:
            try:
                page_num = int(item.text.strip())
                # Store the page number - we'll find the link again when we need to click it
                pages.append({"page": page_num})
            except ValueError:
                # Skip non-numeric items (like "Next" or "Previous")
                continue
        return pages
    except Exception as e:
        log(f"[pagination] Could not find pagination element: {e}")
        return []


def parse_skip_pages(skip_str: str) -> set:
    """Parse comma-separated page numbers into a set of ints."""
    if not skip_str.strip():
        return set()
    try:
        return {int(p.strip()) for p in skip_str.split(",") if p.strip()}
    except Exception:
        return set()


def scrape_current_page(driver, wait, root):
    """Scrape all rows on the current results page."""
    log("[5] Waiting for results on current page")
    WebDriverWait(driver, LIST_WAIT_SEC).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#NameResultsTable tbody tr"))
    )
    human_sleep(0.2, 0.4)
    rows = driver.find_elements(By.CSS_SELECTOR, "#NameResultsTable tbody tr")
    log(f"[OK] Found {len(rows)} rows on this page")

    for idx, row in enumerate(rows, start=1):
        scrape_case(driver, root, idx, row)
        try:
            time.sleep(INTER_CASE_DELAY_SEC)
        except Exception:
            pass


# =========================
# Main
# =========================
def main():
    try:
        driver, wait = attach_driver()
    except Exception as e:
        log(f"[x] Could not connect to Chrome: {e}")
        log("[i] Make sure you clicked 'Open Chrome & log in' in the app and "
            "finished logging in (solve the captcha, then click into File Search), "
            "then start scraping again.")
        return

    # Load existing CSV records for skip checking
    if SKIP_EXISTING_IN_CSV:
        load_existing_file_numbers()

    try:
        # Ensure we're controlling the correct tab
        if not switch_to_websurrogates_tab(driver):
            log("[i] Could not find an open WebSurrogates tab. Please open File Search in the debug Chrome window and rerun.")
            return

        # Make sure we are in the right frame for the File Search form
        if not switch_into_frame_with(driver, By.ID, "CourtSelect"):
            log("[i] Waiting briefly for the File Search form (CourtSelect) to be available…")
            WebDriverWait(driver, LIST_WAIT_SEC).until(lambda d: switch_into_frame_with(d, By.ID, "CourtSelect"))

        log("[1] Filling Court dropdown")
        Select(driver.find_element(By.ID, "CourtSelect")).select_by_value(COURT_VALUE)
        human_sleep(0.15, 0.35)

        log("[2] Selecting proceeding")
        try:
            Select(driver.find_element(By.ID, "SelectedProceeding")).select_by_visible_text(PROCEEDING_TEXT)
        except Exception:
            pass
        human_sleep(0.15, 0.35)

        log("[3] Filling dates")
        f_from = driver.find_element(By.ID, "txtFilingDateFrom")
        f_to   = driver.find_element(By.ID, "txtFilingDateTo")
        f_from.clear(); f_from.send_keys(DATE_FROM)
        f_to.clear();   f_to.send_keys(DATE_TO)
        human_sleep(0.15, 0.35)

        log("[4] Submitting search")
        safe_click(driver, driver.find_element(By.ID, "FileSearchSubmit2"))
        human_sleep(0.15, 0.35)

        root = driver.current_window_handle

        if not PAGINATION_ENABLED:
            # Old behavior: scrape only first page
            scrape_current_page(driver, wait, root)
        else:
            # Pagination enabled: scrape all pages
            skip_set = parse_skip_pages(SKIP_PAGES)
            
            # Get all page links
            pagination_links = get_pagination_links(driver)
            if not pagination_links:
                log("[pagination] No pagination found; scraping current page only")
                scrape_current_page(driver, wait, root)
            else:
                log(f"[pagination] Found {len(pagination_links)} pages")
                
                # Always scrape page 1 first (current page after search)
                if 1 not in skip_set:
                    log(f"[pagination] Scraping page 1")
                    scrape_current_page(driver, wait, root)
                    # Return to main search results tab to clean browser state for pagination
                    driver.switch_to.window(driver.window_handles[0])
                else:
                    log(f"[pagination] Skipping page 1 (configured in SKIP_PAGES)")
                
                # Navigate to remaining pages by clicking the pagination links
                for page_info in pagination_links:
                    page_num = page_info["page"]
                    
                    if page_num == 1:
                        continue  # Already scraped above
                    
                    if page_num in skip_set:
                        log(f"[pagination] Skipping page {page_num} (configured in SKIP_PAGES)")
                        continue
                    
                    log(f"[pagination] Clicking to page {page_num}")
                    
                    # Capture current page content to verify actual navigation
                    try:
                        current_rows = driver.find_elements(By.CSS_SELECTOR, "#NameResultsTable tbody tr")
                        current_file_numbers = []
                        for row in current_rows[:3]:  # Just check first 3 rows for comparison
                            try:
                                file_link = row.find_element(By.CSS_SELECTOR, "td:nth-child(2) a")
                                current_file_numbers.append(file_link.text.strip())
                            except Exception:
                                pass
                        log(f"[pagination] Current page first files: {current_file_numbers[:3]}")
                    except Exception:
                        current_file_numbers = []
                    
                    # Retry logic for clicking pagination links
                    max_retries = 3
                    retry_delay = 1.0
                    page_clicked_successfully = False
                    
                    for attempt in range(max_retries):
                        try:
                            log(f"[pagination] Attempt {attempt + 1}/{max_retries} for page {page_num}")
                            
                            # Find and click the pagination link for this page
                            # We need to find it fresh each time since the DOM may have changed
                            ul = driver.find_element(By.CSS_SELECTOR, "ul.pagination")
                            
                            # Scroll to pagination area first
                            log(f"[pagination] Scrolling to pagination area")
                            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", ul)
                            human_sleep(0.5, 1.0)
                            
                            page_links = ul.find_elements(By.CSS_SELECTOR, "li.page-item a.page-link")
                            
                            clicked = False
                            for link in page_links:
                                try:
                                    if int(link.text.strip()) == page_num:
                                        # Scroll to specific link to ensure it's visible and centered
                                        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", link)
                                        human_sleep(0.2, 0.4)
                                        
                                        # Try regular click first
                                        link.click()
                                        clicked = True
                                        break
                                except (ValueError, Exception) as click_err:
                                    log(f"[pagination] Click failed on attempt {attempt + 1}: {click_err}")
                                    # Try JavaScript click as fallback
                                    try:
                                        driver.execute_script("arguments[0].click();", link)
                                        clicked = True
                                        break
                                    except Exception as js_err:
                                        log(f"[pagination] JavaScript click also failed: {js_err}")
                                        continue
                            
                            if not clicked:
                                raise Exception(f"Could not find clickable link for page {page_num}")
                            
                            # Wait for initial page response
                            human_sleep(0.5, 1.0)
                            
                            # Wait for the results table to reload with increased timeout
                            WebDriverWait(driver, LIST_WAIT_SEC * 2).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, "#NameResultsTable tbody tr"))
                            )
                            
                            # Additional wait to ensure page is fully loaded
                            human_sleep(0.3, 0.6)
                            
                            # TWO-STEP VERIFICATION: Check if we're actually on the target page
                            try:
                                # Find the active page indicator to verify which page we're on
                                active_page_elem = driver.find_element(By.CSS_SELECTOR, ".pagination .page-item.active .page-link")
                                actual_page = int(active_page_elem.text.strip())
                                log(f"[pagination] After click, actually on page: {actual_page}, target: {page_num}")
                                
                                # If we're not on the target page, click again (two-step verification)
                                if actual_page != page_num:
                                    log(f"[pagination] Page mismatch! Attempting second click to page {page_num}")
                                    
                                    # Find and click the target page again
                                    ul = driver.find_element(By.CSS_SELECTOR, "ul.pagination")
                                    page_links = ul.find_elements(By.CSS_SELECTOR, "li.page-item a.page-link")
                                    
                                    for link in page_links:
                                        if int(link.text.strip()) == page_num:
                                            driver.execute_script("arguments[0].scrollIntoView(true);", link)
                                            human_sleep(0.2, 0.4)
                                            link.click()
                                            log(f"[pagination] Second click executed for page {page_num}")
                                            break
                                    
                                    # Wait again after second click
                                    time.sleep(3)
                                    WebDriverWait(driver, LIST_WAIT_SEC).until(
                                        EC.presence_of_element_located((By.CSS_SELECTOR, "#NameResultsTable tbody tr"))
                                    )
                                    
                            except Exception as verification_err:
                                log(f"[pagination] Could not verify page number: {verification_err}")
                                # Continue anyway - we'll catch it in content verification below
                            
                            # Verify we actually have results AND they're different from before
                            new_rows = driver.find_elements(By.CSS_SELECTOR, "#NameResultsTable tbody tr")
                            if not new_rows:
                                raise Exception("No results found after page load")
                            
                            # Check if content actually changed
                            new_file_numbers = []
                            for row in new_rows[:3]:  # Check first 3 rows
                                try:
                                    file_link = row.find_element(By.CSS_SELECTOR, "td:nth-child(2) a")
                                    new_file_numbers.append(file_link.text.strip())
                                except Exception:
                                    pass
                            
                            log(f"[pagination] New page first files: {new_file_numbers[:3]}")
                            
                            # Compare content to verify we actually navigated
                            if current_file_numbers and new_file_numbers == current_file_numbers:
                                raise Exception(f"Page content unchanged - still showing same files: {new_file_numbers}")
                            
                            page_clicked_successfully = True
                            log(f"[pagination] Successfully navigated to page {page_num} - content changed!")
                            break
                            
                        except Exception as e:
                            log(f"[pagination] Attempt {attempt + 1} failed for page {page_num}: {e}")
                            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                                log(f"[pagination] Waiting {retry_delay} seconds before retry...")
                                time.sleep(retry_delay)
                                retry_delay *= 1.5  # Exponential backoff
                            continue
                    
                    if page_clicked_successfully:
                        try:
                            scrape_current_page(driver, wait, root)
                        except Exception as scrape_err:
                            log(f"[pagination] Error scraping page {page_num}: {scrape_err}")
                    else:
                        log(f"[pagination] Failed to navigate to page {page_num} after {max_retries} attempts, skipping...")
                        continue

        log("[✓] Finished all pages")
        log(f"[stats] downloads={STATS['downloads']} ocr_ok={STATS['ocr_ok']} ocr_err={STATS['ocr_err']}")

    except Exception as e:
        log(f"[x] Error: {e}")
    finally:
        if KEEP_BROWSER_OPEN:
            log("[i] Leaving your Chrome open. Close manually when finished.")
        else:
            driver.quit()

if __name__ == "__main__":
    main()