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
import shutil
import mimetypes
from typing import Optional

import requests as http_requests  # For direct downloads

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
COURT_VALUE = "24"          # Queens County = 41
# Single switch for the target document type. Options: "PROBATE", "VOLUNTARY"
#SELECTED_DOC = "PROBATE"  # Change to "VOLUNTARY" to target Voluntary Admin Affidavit
SELECTED_DOC = "VOLUNTARY"  # Change to "VOLUNTARY" to target Voluntary Admin Affidavit

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

# Calculate date range for the last 30 days
from datetime import datetime, timedelta

def get_last_30_days_range():
    """Returns dates for the last 30 days in MM/DD/YYYY format."""
    today = datetime.now()
    thirty_days_ago = today - timedelta(days=30)
    
    date_from = thirty_days_ago.strftime("%m/%d/%Y")
    date_to = today.strftime("%m/%d/%Y")
    return date_from, date_to

DATE_FROM, DATE_TO = get_last_30_days_range()

# Pagination controls: set to 0 to disable pagination, or specify pages to skip (comma-separated)
# Examples:
#   PAGINATION_ENABLED = True  # process all pages
#   SKIP_PAGES = "1,3"         # skip pages 1 and 3
#   SKIP_PAGES = ""            # don't skip any pages
PAGINATION_ENABLED = True
SKIP_PAGES = ""  # comma-separated page numbers to skip, e.g. "1,3,5"

# CSV skip configuration: skip cases if file number already exists in CSV
SKIP_EXISTING_IN_CSV = True
CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "probate_records.csv")

# PostgreSQL configuration: set to True to save to remote database (Railway)
# Requires DATABASE_URL environment variable to be set
USE_POSTGRESQL = True  # Set to False to use CSV only
SKIP_EXISTING_IN_DB = True  # Skip cases already in PostgreSQL database

DEBUG_ADDRESS = "127.0.0.1:9222"
WAIT_SEC = 30
LIST_WAIT_SEC = 20
KEEP_BROWSER_OPEN = True

# Dedicated folder to avoid mixing with other PDFs
# Use os.path.normpath to fix mixed slashes on Windows
DOWNLOAD_DIR = os.path.normpath(os.path.join(os.path.expanduser("~"), "Downloads", "ny-probate"))
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
_existing_file_numbers_db = None

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


def load_existing_file_numbers_from_db():
    """Load all file numbers from PostgreSQL database into a set for fast lookup."""
    global _existing_file_numbers_db
    if _existing_file_numbers_db is not None:
        return _existing_file_numbers_db
    
    _existing_file_numbers_db = set()
    if not USE_POSTGRESQL or not SKIP_EXISTING_IN_DB:
        return _existing_file_numbers_db
    
    try:
        from db_client import load_existing_file_numbers as db_load
        _existing_file_numbers_db = db_load()
        log(f"[DB] Loaded {len(_existing_file_numbers_db)} existing file numbers from database")
    except Exception as e:
        log(f"[DB] Error loading file numbers from database: {e}")
    
    return _existing_file_numbers_db


def is_file_number_in_csv(file_number: str) -> bool:
    """Check if a file number already exists in the CSV."""
    existing = load_existing_file_numbers()
    return file_number.strip() in existing


def is_file_number_exists(file_number: str) -> bool:
    """Check if a file number exists in CSV or database (depending on config)."""
    file_num = file_number.strip()
    
    # Check CSV if enabled
    if SKIP_EXISTING_IN_CSV and is_file_number_in_csv(file_num):
        return True
    
    # Check database if enabled
    if USE_POSTGRESQL and SKIP_EXISTING_IN_DB:
        db_existing = load_existing_file_numbers_from_db()
        if file_num in db_existing:
            return True
    
    return False


def add_file_number_to_cache(file_number: str) -> None:
    """Add a file number to the cache after processing."""
    global _existing_file_numbers, _existing_file_numbers_db
    file_num = file_number.strip()
    if _existing_file_numbers is not None:
        _existing_file_numbers.add(file_num)
    if _existing_file_numbers_db is not None:
        _existing_file_numbers_db.add(file_num)

def selected_county_name() -> str:
    return COURT_TO_COUNTY.get(COURT_VALUE, "")

# =========================
# Direct download using requests (fallback)
# =========================
def download_with_requests(driver, url: str, file_number: str) -> Optional[str]:
    """
    Download a file using requests with cookies from Selenium session.
    This bypasses Chrome's download mechanism which can fail when attached.
    """
    try:
        # Get cookies from Selenium
        cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        
        # Make request with browser cookies
        headers = {
            'User-Agent': driver.execute_script("return navigator.userAgent"),
            'Referer': driver.current_url,
        }
        
        log(f"[dl-req] Downloading via requests: {url[:80]}...")
        resp = http_requests.get(url, cookies=cookies, headers=headers, timeout=60, stream=True)
        
        if resp.status_code != 200:
            log(f"[dl-req] Failed with status {resp.status_code}")
            return None
        
        # Determine filename
        suffix = DOC_OPTIONS[SELECTED_DOC]["filename"]
        safe_file_number = file_number.replace("/", "-").replace("\\", "-")
        final_path = os.path.join(DOWNLOAD_DIR, f"{safe_file_number}_{suffix}.pdf")
        
        # Write to file
        with open(final_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        
        log(f"[dl-req] Saved to {final_path}")
        return final_path
    except Exception as e:
        log(f"[dl-req] Error: {e}")
        return None

# =========================
# Browser attach
# =========================
def attach_driver(debug_address: str = DEBUG_ADDRESS):
    opts = Options()
    opts.add_experimental_option("debuggerAddress", debug_address)
    # Note: prefs don't apply when attaching to existing Chrome, but we include them anyway
    opts.add_experimental_option("prefs", {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True
    })
    driver = webdriver.Chrome(options=opts)
    wait = WebDriverWait(driver, WAIT_SEC)
    
    # NOTE: Skipping CDP download behavior commands as they may interfere with 
    # normal Chrome downloads when attached to an existing session.
    # Downloads will go to Chrome's default location (usually ~/Downloads)
    # and we monitor both that and DOWNLOAD_DIR.
    log(f"[i] Attached to Chrome. Downloads will be monitored in {DOWNLOAD_DIR} and {DEFAULT_DOWNLOADS}")
    
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
# Also check default Downloads folder as fallback when attached to existing Chrome
DEFAULT_DOWNLOADS = os.path.normpath(os.path.join(os.path.expanduser("~"), "Downloads"))

def wait_for_download_and_rename(file_number: str, timeout: int = 90, start_time: float = None):
    """
    Wait for the *newly triggered* download in DOWNLOAD_DIR, then rename it
    to <file_number>_Probate_Petition.pdf and upload it to Drive.
    Ignores any PDFs that were present before the click.
    Also checks default Downloads folder as fallback.
    
    start_time: If provided, use this as the reference time for detecting new files.
                This should be captured BEFORE clicking the download button.
    """
    global _drive_folder_id_cache
    if start_time is None:
        start_time = time.time()
    
    log(f"[dl] DOWNLOAD_DIR = {DOWNLOAD_DIR}")
    log(f"[dl] DEFAULT_DOWNLOADS = {DEFAULT_DOWNLOADS}")
    log(f"[dl] Start time = {start_time}")

    # Ensure DOWNLOAD_DIR exists
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # Directories to monitor (default Downloads first since that's where Chrome usually saves)
    dirs_to_check = [DEFAULT_DOWNLOADS, DOWNLOAD_DIR]
    # Remove duplicates while preserving order
    dirs_to_check = list(dict.fromkeys(dirs_to_check))
    
    log(f"[dl] Monitoring directories: {dirs_to_check}")

    new_pdf_path = None
    tmp_sizes = {}
    reported = set()
    check_count = 0

    while time.time() - start_time < timeout:
        check_count += 1
        # Check all monitored directories
        for check_dir in dirs_to_check:
            try:
                current_files = os.listdir(check_dir)
            except Exception as e:
                log(f"[dl] Error listing {check_dir}: {e}")
                continue
            
            # Look for files modified AFTER we started waiting
            for name in current_files:
                # Only care about .tmp, .pdf, .crdownload files, OR files named "viewer" (Chrome's PDF viewer download name)
                lower_name = name.lower()
                is_valid_type = (lower_name.endswith('.tmp') or 
                                 lower_name.endswith('.pdf') or 
                                 lower_name.endswith('.crdownload') or
                                 lower_name == 'viewer' or  # Chrome sometimes saves as just "viewer"
                                 lower_name.startswith('viewer'))  # Or viewer.pdf, viewer(1).pdf, etc.
                if not is_valid_type:
                    continue
                
                # Skip files that are already properly named (already processed)
                if '_Voluntary_Admin_Affidavit.pdf' in name or '_Probate_Petition.pdf' in name:
                    continue
                    
                fp = os.path.join(check_dir, name)
                try:
                    current_mtime = os.path.getmtime(fp)
                    
                    # File is relevant if it was modified AFTER we started waiting
                    # This handles Chrome reusing the same .tmp filename
                    is_recent = current_mtime >= start_time - 2  # 2 second buffer for timing
                    
                    if not is_recent:
                        continue
                    
                    if name not in reported:
                        log(f"[dl] Found recent file: {name} in {check_dir} (mtime={current_mtime:.1f}, start={start_time:.1f})")
                        reported.add(name)
                    
                    # Handle .tmp files - wait for size to stabilize
                    if lower_name.endswith('.tmp'):
                        sz = os.path.getsize(fp)
                        key = f"{check_dir}:{name}"
                        prev = tmp_sizes.get(key)
                        now = time.time()
                        
                        if not prev:
                            tmp_sizes[key] = (sz, now)
                            log(f"[dl] Tracking {name}: size={sz}")
                        elif sz != prev[0]:
                            tmp_sizes[key] = (sz, now)
                            log(f"[dl] {name} growing: {prev[0]} -> {sz}")
                        elif (now - prev[1]) > 1.5:
                            # Size stable for 1.5 seconds - file is complete
                            new_pdf_path = fp
                            log(f"[dl] {name} COMPLETE! size={sz}")
                            break
                    
                    # Handle .pdf files OR "viewer" files (Chrome's PDF viewer name) - wait briefly for size to stabilize
                    elif lower_name.endswith('.pdf') or lower_name.startswith('viewer'):
                        sz = os.path.getsize(fp)
                        key = f"{check_dir}:{name}"
                        prev = tmp_sizes.get(key)
                        now = time.time()
                        
                        if not prev:
                            tmp_sizes[key] = (sz, now)
                            log(f"[dl] Tracking PDF/viewer {name}: size={sz}")
                        elif sz != prev[0]:
                            tmp_sizes[key] = (sz, now)
                            log(f"[dl] PDF/viewer {name} growing: {prev[0]} -> {sz}")
                        elif (now - prev[1]) > 1.0:
                            # Size stable for 1 second - PDF is complete
                            new_pdf_path = fp
                            log(f"[dl] PDF/viewer {name} COMPLETE! size={sz}")
                            break
                        
                except Exception as e:
                    if check_count == 1:  # Only log once
                        log(f"[dl] Error checking {name}: {e}")
                    continue
            
            if new_pdf_path:
                break

        if new_pdf_path:
            # Use selected filename suffix
            suffix = DOC_OPTIONS[SELECTED_DOC]["filename"]
            # Sanitize file number: replace / with - to avoid path issues
            safe_file_number = file_number.replace("/", "-").replace("\\", "-")
            # Always save to DOWNLOAD_DIR with .pdf extension
            final_path = os.path.join(DOWNLOAD_DIR, f"{safe_file_number}_{suffix}.pdf")
            
            # Ensure DOWNLOAD_DIR exists
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            
            # Move/rename (handles .tmp -> .pdf conversion automatically)
            source_file = new_pdf_path
            log(f"[dl] Moving '{source_file}' -> '{final_path}'")
            
            try:
                # Use shutil.move which handles cross-drive moves and overwrites
                if os.path.exists(final_path):
                    os.remove(final_path)  # Remove existing file first
                shutil.move(source_file, final_path)
                log(f"[dl] Move successful")
            except Exception as e_move:
                log(f"[dl] Move failed ({e_move}); trying copy+delete")
                try:
                    shutil.copy2(source_file, final_path)
                    os.remove(source_file)
                    log(f"[dl] Copy+delete successful")
                except Exception as e:
                    log(f"⚠️ Could not move downloaded file: {e}")
                    # Keep file where it is but with correct name
                    try:
                        fallback_path = os.path.join(os.path.dirname(source_file), f"{safe_file_number}_{suffix}.pdf")
                        shutil.move(source_file, fallback_path)
                        final_path = fallback_path
                        log(f"[dl] Fallback rename in place: {fallback_path}")
                    except Exception:
                        final_path = source_file  # Last resort: use original

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

    # Prepare CSV row
    CSV_PATH = os.path.join("data", "probate_records.csv")
    os.makedirs("data", exist_ok=True)
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

    # Save to PostgreSQL database if enabled
    if USE_POSTGRESQL:
        try:
            from db_client import save_record_to_db
            db_ok = save_record_to_db(
                county_name=county_name,
                site_file_number=site_file_number,
                llm_data=llm_data
            )
            if db_ok:
                log("[i] Record saved to PostgreSQL database")
            else:
                log("⚠️ Failed to save record to PostgreSQL database")
        except Exception as e:
            log(f"⚠️ Database save error: {e}")

    # Also upsert into CSV as backup
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
        
        # Check if this file number already exists in CSV or database
        if is_file_number_exists(file_num):
            log(f"[{idx}] ⏭️  Skipping {file_num} - already exists")
            return
        
        log(f"[{idx}] Opening file {file_num} in a new tab")

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
        WebDriverWait(driver, 10).until(lambda d: len(d.window_handles) > 1)
        new_tab = [h for h in driver.window_handles if h != root][-1]
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
                        
                        # Try to get the onclick URL or form action for direct download
                        onclick = probate_btn.get_attribute("onclick") or ""
                        form_action = None
                        
                        # Check if there's a form with the download URL
                        try:
                            form = r.find_element(By.TAG_NAME, "form")
                            form_action = form.get_attribute("action")
                        except Exception:
                            pass
                        
                        # Track tabs before click
                        tabs_before = set(driver.window_handles)
                        current_tab = driver.current_window_handle
                        
                        # Capture time BEFORE clicking so we can detect files downloaded after this
                        download_start_time = time.time()
                        
                        # Click the button to trigger download
                        probate_btn.click()
                        target_found = True
                        log(f"📥 {DOC_OPTIONS[SELECTED_DOC]['filename']} download triggered for {file_number}")
                        
                        # Wait a moment for any new tab/popup to open or download to start
                        time.sleep(3)
                        
                        # Check if a new tab opened (PDF viewer)
                        tabs_after = set(driver.window_handles)
                        new_tabs = tabs_after - tabs_before
                        
                        downloaded_path = None
                        
                        if new_tabs:
                            log(f"[dl] New tab detected - checking if it's a PDF viewer")
                            # Switch to new tab to get the PDF URL
                            new_tab = list(new_tabs)[0]
                            driver.switch_to.window(new_tab)
                            time.sleep(1)
                            pdf_url = driver.current_url
                            log(f"[dl] New tab URL: {pdf_url}")
                            
                            # If it's a PDF URL or blob, try to download via requests
                            if 'pdf' in pdf_url.lower() or 'blob:' in pdf_url or 'document' in pdf_url.lower():
                                log(f"[dl] Attempting direct download from PDF URL...")
                                downloaded_path = download_with_requests(driver, pdf_url, file_number)
                            
                            # Close the PDF viewer tab
                            try:
                                driver.close()
                            except Exception:
                                pass
                            driver.switch_to.window(current_tab)
                        
                        # If no new tab or requests failed, wait for Chrome download
                        if not downloaded_path:
                            log("[dl] Waiting for Chrome download...")
                            downloaded_path = wait_for_download_and_rename(file_number, timeout=60, start_time=download_start_time)
                        
                        # Last resort: try form action URL if we have it
                        if not downloaded_path and form_action:
                            log("[dl] Trying form action URL as fallback...")
                            downloaded_path = download_with_requests(driver, form_action, file_number)
                        
                        if downloaded_path:
                            log(f"✅ Successfully saved: {downloaded_path}")
                        else:
                            log(f"⚠️ Could not download file for {file_number}")
                        
                        break
                    except Exception as e:
                        log(f"⚠️ Target document found but error: {e}")
        except Exception as e:
            log(f"⚠️ Could not parse documents table: {e}")

        if not target_found:
            log("❌ No target document for this case.")

        # Close tab and return
        driver.close()
        driver.switch_to.window(root)
        human_sleep(0.4, 0.8)

    except Exception as e:
        log(f"⚠️ Row {idx} failed: {e}")
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
        # XPath: /html/body/div[1]/div[5]/div/div/form/div[3]/div[2]/ul
        ul = driver.find_element(By.XPATH, "/html/body/div[1]/div[5]/div/div/form/div[3]/div[2]/ul")
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
    
    # After scraping all rows, ensure we're back on the root tab and close any extras
    try:
        # Close any extra tabs that might have been left open
        while len(driver.window_handles) > 1:
            extra_tabs = [h for h in driver.window_handles if h != root]
            if extra_tabs:
                driver.switch_to.window(extra_tabs[0])
                driver.close()
        driver.switch_to.window(root)
        log(f"[pagination] Cleaned up tabs, now on root window")
    except Exception as e:
        log(f"[pagination] Tab cleanup warning: {e}")
        try:
            driver.switch_to.window(root)
        except Exception:
            pass


# =========================
# Main
# =========================
def main():
    driver, wait = attach_driver()

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
        driver.find_element(By.ID, "FileSearchSubmit2").click()
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
                    # Ensure we're on the root tab for pagination
                    driver.switch_to.window(root)
                    human_sleep(0.3, 0.5)
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
                    
                    # Ensure we're on the root tab before pagination
                    try:
                        driver.switch_to.window(root)
                        human_sleep(0.2, 0.3)
                    except Exception:
                        pass
                    
                    # Wait for results table to be present before trying to capture current state
                    try:
                        WebDriverWait(driver, 10).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "#NameResultsTable tbody tr"))
                        )
                    except Exception as wait_err:
                        log(f"[pagination] Could not find results table before pagination: {wait_err}")
                        continue
                    
                    # Capture current page content to verify actual navigation
                    try:
                        current_rows = driver.find_elements(By.CSS_SELECTOR, "#NameResultsTable tbody tr")
                        current_file_numbers = []
                        for row in current_rows[:3]:  # Just check first 3 rows for comparison
                            try:
                                # Try button first (like reference script)
                                btn = row.find_element(By.CSS_SELECTOR, "button.ButtonAsLink")
                                file_num = btn.get_attribute("value") or btn.text.strip()
                                current_file_numbers.append(file_num)
                            except Exception:
                                try:
                                    # Fallback to link
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
                            ul = driver.find_element(By.XPATH, "/html/body/div[1]/div[5]/div/div/form/div[3]/div[2]/ul")
                            
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
                                    ul = driver.find_element(By.XPATH, "/html/body/div[1]/div[5]/div/div/form/div[3]/div[2]/ul")
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
                                    # Try button first (like reference script)
                                    btn = row.find_element(By.CSS_SELECTOR, "button.ButtonAsLink")
                                    file_num = btn.get_attribute("value") or btn.text.strip()
                                    new_file_numbers.append(file_num)
                                except Exception:
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