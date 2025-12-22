"""
Azure OCR helper for first-page-only extraction.

- Uses Azure Computer Vision Read API (v3.2) to OCR PDFs
- Restricts usage to page 1 by only reading the first page from results
- Saves raw JSON into ocr_json/<stem>.json and text into ocr_text/<stem>.txt

Environment variables (via .env):
  AZ_ENDPOINT = https://<region>.api.cognitive.microsoft.com
  AZ_KEY      = <your-key>

Public functions:
  ocr_first_page(pdf_path: str) -> dict with keys {text, json_file, text_file}
"""

from __future__ import annotations

import os
import time
import json
from pathlib import Path
from typing import Dict, Any, Optional, Iterable

import requests
from dotenv import load_dotenv


# Lazy-load env to avoid import-time failure in non-Azure code paths
_env_loaded = False
_READ_URL: Optional[str] = None
_AZ_KEY: Optional[str] = None

# Simple rate limiter for submissions (helps avoid 429)
_last_submit_ts: float = 0.0
# Defaults are conservative to reduce 429s; can be tuned via env
MIN_SUBMIT_INTERVAL: float = float(os.getenv("AZ_OCR_MIN_SUBMIT_INTERVAL", "2.5"))  # seconds between submits
MAX_RETRIES: int = int(os.getenv("AZ_OCR_MAX_RETRIES", "5"))
POLL_INTERVAL: float = float(os.getenv("AZ_OCR_POLL_INTERVAL", "2.5"))  # seconds between polls
OCR_TIMEOUT: float = float(os.getenv("AZ_OCR_TIMEOUT", "240"))  # total seconds per file


def _ensure_env() -> None:
    global _env_loaded, _READ_URL, _AZ_KEY
    if _env_loaded:
        return
    load_dotenv()
    endpoint = os.getenv("AZ_ENDPOINT", "").strip()
    key = os.getenv("AZ_KEY", "").strip()
    if not endpoint or not key:
        raise RuntimeError("Missing AZ_ENDPOINT or AZ_KEY in environment/.env")
    _READ_URL = endpoint.rstrip("/") + "/vision/v3.2/read/analyze"
    _AZ_KEY = key
    _env_loaded = True


def _sleep_rate_limit():
    """Ensure at least MIN_SUBMIT_INTERVAL seconds between submissions."""
    global _last_submit_ts
    now = time.time()
    delta = now - _last_submit_ts
    if delta < MIN_SUBMIT_INTERVAL:
        # add tiny jitter
        time.sleep((MIN_SUBMIT_INTERVAL - delta) + (0.05 + 0.15 * (os.getpid() % 3)))
    _last_submit_ts = time.time()


def _request_with_retries(method: str, url: str, **kwargs) -> requests.Response:
    """Perform an HTTP request with exponential backoff and 429 handling."""
    backoff = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.request(method, url, timeout=kwargs.pop("timeout", 60), **kwargs)
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else backoff
                time.sleep(min(wait, 10.0))
                backoff = min(backoff * 2.0, 10.0)
                continue
            if 500 <= r.status_code < 600:
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt >= MAX_RETRIES:
                raise
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 10.0)
    # Should not reach here
    raise RuntimeError("HTTP request failed after retries")


def _submit_pdf_for_read(pdf_path: Path) -> str:
    _ensure_env()
    assert _READ_URL and _AZ_KEY
    headers = {
        "Ocp-Apim-Subscription-Key": _AZ_KEY,
        "Content-Type": "application/pdf",
    }
    with open(pdf_path, "rb") as f:
        _sleep_rate_limit()
        r = _request_with_retries("POST", _READ_URL, headers=headers, data=f.read(), timeout=60)
    op_loc = r.headers.get("Operation-Location")
    if not op_loc:
        raise RuntimeError("Azure Read: No Operation-Location header in response")
    return op_loc


def _poll_result(op_url: str, poll_interval: float = POLL_INTERVAL, timeout: float = OCR_TIMEOUT) -> Dict[str, Any]:
    _ensure_env()
    assert _AZ_KEY
    headers = {"Ocp-Apim-Subscription-Key": _AZ_KEY}
    t0 = time.time()
    while True:
        r = _request_with_retries("GET", op_url, headers=headers, timeout=30)
        j = r.json()
        st = j.get("status")
        if st in ("succeeded", "failed"):
            return j
        if time.time() - t0 > timeout:
            raise TimeoutError("Azure Read polling timed out")
        time.sleep(poll_interval)


def _extract_first_page_text(ocr_json: Dict[str, Any]) -> str:
    # v3.2 structure: analyzeResult -> readResults -> [ { page, lines: [ {text} ] } ]
    pages = (ocr_json.get("analyzeResult", {}) or {}).get("readResults", [])
    if not pages:
        return ""
    first = pages[0] or {}
    lines = [ln.get("text", "").strip() for ln in first.get("lines", []) if ln and ln.get("text")]
    return "\n".join(lines)


def ocr_first_page(pdf_path: str, out_json_dir: str = "./ocr_json", out_text_dir: str = "./ocr_text") -> Dict[str, Any]:
    """OCR the first page only using Azure Read API and save outputs.

    Returns dict:
      {
        "text": <first page text>,
        "json_file": <path to raw azure json>,
        "text_file": <path to first-page text file>
      }
    """
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    out_json = Path(out_json_dir)
    out_text = Path(out_text_dir)
    out_json.mkdir(exist_ok=True)
    out_text.mkdir(exist_ok=True)

    op = _submit_pdf_for_read(p)
    res = _poll_result(op)

    # Save raw JSON
    json_path = out_json / (p.stem + ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    # Extract first-page-only text and save
    text_first = _extract_first_page_text(res)
    text_path = out_text / (p.stem + ".txt")
    # Write with error handling for invalid UTF-8 characters
    with open(text_path, "w", encoding="utf-8", errors="replace") as f:
        f.write(text_first)

    return {
        "text": text_first,
        "json_file": str(json_path),
        "text_file": str(text_path),
    }


def ocr_sweep(input_dir: str, pattern: str = "*.pdf", limit: Optional[int] = None) -> Iterable[Dict[str, Any]]:
    """Process all PDFs in a directory sequentially with built-in throttling.

    Yields a dict per file with keys: {path, text, json_file, text_file} or {path, error}.
    """
    root = Path(input_dir)
    files = sorted(root.glob(pattern))
    count = 0
    for p in files:
        if limit is not None and count >= limit:
            break
        try:
            res = ocr_first_page(str(p))
            yield {"path": str(p), **res}
        except Exception as e:
            yield {"path": str(p), "error": str(e)}
        count += 1
