#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phoenix Probate — Control Panel
================================

A single, friendly window that runs the whole probate workflow so a
non-technical person never has to touch the terminal or edit any code:

  1. Open Chrome and log in to the NY Surrogate Court site (solve the captcha).
  2. Pick the county / document type / date range.
  3. Start scraping — watch the live log.
  4. Browse the collected records and download the spreadsheet.

This file is the app the launcher opens. It deliberately avoids importing
the scraper (and therefore Selenium / Google libraries) at module load time
so that the Setup and Records screens keep working even before those heavy
dependencies finish installing.
"""

from __future__ import annotations

import os
import sys
import socket
import subprocess
from datetime import date, datetime
from pathlib import Path

import streamlit as st

if getattr(sys, "frozen", False):
    # Running inside a PyInstaller bundle: keep data next to the executable.
    APP_DIR = Path(os.environ.get("PB_APP_DIR") or Path(sys.executable).resolve().parent)
else:
    APP_DIR = Path(__file__).resolve().parent
ENV_PATH = APP_DIR / ".env"
CSV_PATH = Path(os.environ.get("PB_CSV_PATH", str(APP_DIR / "data" / "probate_records.csv")))
LOG_DIR = APP_DIR / "logs"
SCRAPER = APP_DIR / "probate_scraper.py"
AUTH_URL = "https://websurrogates.nycourts.gov/Home/AuthenticatePage"

# County name -> the court value the site expects (mirrors probate_scraper.py)
COUNTY_OPTIONS = {
    "Queens": "41",
    "Nassau": "30",
    "Kings (Brooklyn)": "24",
    "New York (Manhattan)": "31",
    "Bronx": "3",
}

# Friendly label -> the SELECTED_DOC switch the scraper understands
DOC_OPTIONS = {
    "Voluntary Admin Affidavit": "VOLUNTARY",
    "Probate Petition": "PROBATE",
}

DEBUG_HOST = "127.0.0.1"
DEBUG_PORT = 9222


# ---------------------------------------------------------------------------
# Environment / .env helpers
# ---------------------------------------------------------------------------
def load_env() -> dict:
    """Read .env into a dict (and into os.environ) without extra dependencies."""
    values: dict[str, str] = {}
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            values[key] = val
            # Make the keys visible to the scraper subprocess we spawn later.
            # Assign directly (not setdefault) so re-saving a key takes effect now.
            if val:
                os.environ[key] = val
    return values


def save_env(updates: dict) -> None:
    """Merge `updates` into .env, preserving anything already there."""
    current = load_env()
    current.update({k: v for k, v in updates.items() if v is not None})
    lines = [
        "# Phoenix Probate configuration — keep this file private (it holds API keys).",
        "",
        "# Azure Computer Vision (OCR)",
        f"AZ_ENDPOINT={current.get('AZ_ENDPOINT', '')}",
        f"AZ_KEY={current.get('AZ_KEY', '')}",
        "",
        "# OpenAI (field extraction)",
        f"OPENAI_API_KEY={current.get('OPENAI_API_KEY', '')}",
        f"OPENAI_MODEL={current.get('OPENAI_MODEL', 'gpt-4o-mini')}",
    ]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    load_env()


def keys_status(env: dict) -> dict:
    def ok(name: str) -> bool:
        v = (env.get(name) or os.environ.get(name) or "").strip()
        return bool(v) and not v.startswith("<")

    return {
        "Azure OCR": ok("AZ_ENDPOINT") and ok("AZ_KEY"),
        "OpenAI": ok("OPENAI_API_KEY"),
    }


# ---------------------------------------------------------------------------
# Chrome helpers
# ---------------------------------------------------------------------------
def find_chrome() -> str | None:
    override = os.environ.get("PB_CHROME_PATH", "").strip()
    if override and Path(override).exists():
        return override
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def debug_chrome_is_up(host: str = DEBUG_HOST, port: int = DEBUG_PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def launch_debug_chrome() -> tuple[bool, str]:
    chrome = find_chrome()
    if not chrome:
        return False, (
            "Google Chrome was not found. Install it from google.com/chrome, "
            "then try again."
        )
    user_dir = os.path.expanduser("~/chrome-remote")
    os.makedirs(user_dir, exist_ok=True)
    try:
        subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={DEBUG_PORT}",
                f"--user-data-dir={user_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                AUTH_URL,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "Chrome is opening. Log in and solve the captcha, then click into File Search."
    except Exception as e:  # pragma: no cover - environment dependent
        return False, f"Could not launch Chrome: {e}"


# ---------------------------------------------------------------------------
# Scraper subprocess helpers
# ---------------------------------------------------------------------------
def scraper_running() -> bool:
    proc = st.session_state.get("scraper_proc")
    return proc is not None and proc.poll() is None


def start_scraper(court_value: str, selected_doc: str, date_from: str, date_to: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"scrape_{stamp}.log"

    env = os.environ.copy()
    env.update(
        {
            "PB_COURT_VALUE": court_value,
            "PB_SELECTED_DOC": selected_doc,
            "PB_DATE_FROM": date_from,
            "PB_DATE_TO": date_to,
            "PYTHONUNBUFFERED": "1",
        }
    )

    # In a normal install sys.executable is the venv's Python, so we run the
    # scraper script directly. In a frozen bundle the executable is the app
    # itself, which understands the "scrape" subcommand (see dist_tools/entry.py).
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "scrape"]
    else:
        cmd = [sys.executable, str(SCRAPER)]

    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(APP_DIR),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    st.session_state["scraper_proc"] = proc
    st.session_state["scraper_log"] = str(log_path)
    st.session_state["scraper_logfile"] = log_file


def stop_scraper() -> None:
    proc = st.session_state.get("scraper_proc")
    if proc and proc.poll() is None:
        proc.terminate()


def tail(path: str | None, max_lines: int = 500) -> str:
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-max_lines:])
    except FileNotFoundError:
        return ""


# ---------------------------------------------------------------------------
# UI sections
# ---------------------------------------------------------------------------
def render_sidebar(env: dict) -> None:
    st.sidebar.header("Setup status")
    for label, ok in keys_status(env).items():
        st.sidebar.write(("✅ " if ok else "❌ ") + f"{label} key")
    st.sidebar.write(("✅ " if find_chrome() else "❌ ") + "Google Chrome found")

    with st.sidebar.expander("API keys", expanded=not all(keys_status(env).values())):
        st.caption(
            "These can be pre-filled for you. Fill them in only if a box above shows ❌."
        )
        az_endpoint = st.text_input("Azure endpoint", value=env.get("AZ_ENDPOINT", ""))
        az_key = st.text_input("Azure key", value=env.get("AZ_KEY", ""), type="password")
        openai_key = st.text_input(
            "OpenAI key", value=env.get("OPENAI_API_KEY", ""), type="password"
        )
        if st.button("Save keys"):
            save_env(
                {
                    "AZ_ENDPOINT": az_endpoint.strip(),
                    "AZ_KEY": az_key.strip(),
                    "OPENAI_API_KEY": openai_key.strip(),
                }
            )
            st.success("Saved. Reloading…")
            st.rerun()


def render_run_tab(env: dict) -> None:
    st.subheader("Step 1 — Open Chrome and log in")
    st.write(
        "Click below to open a dedicated Chrome window. **Log in and solve the "
        "captcha**, then click into **File Search**. Leave that window open."
    )
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("🌐 Open Chrome & log in", use_container_width=True):
            ok, msg = launch_debug_chrome()
            (st.success if ok else st.error)(msg)
    with c2:
        if debug_chrome_is_up():
            st.success("Chrome is connected and ready.")
        else:
            st.info("Waiting for the login Chrome window… (open it with the button)")

    st.divider()
    st.subheader("Step 2 — Choose what to collect")
    col1, col2 = st.columns(2)
    with col1:
        county = st.selectbox("County", list(COUNTY_OPTIONS.keys()))
        doc = st.selectbox("Document type", list(DOC_OPTIONS.keys()))
    with col2:
        d_from = st.date_input("Filing date from", value=date(2025, 11, 1))
        d_to = st.date_input("Filing date to", value=date(2025, 11, 30))

    st.divider()
    st.subheader("Step 3 — Run")

    missing_keys = [k for k, ok in keys_status(env).items() if not ok]
    if missing_keys:
        st.warning(
            "Missing API key(s): "
            + ", ".join(missing_keys)
            + ". Add them in the sidebar before running."
        )

    run_col, stop_col = st.columns([1, 1])
    with run_col:
        disabled = scraper_running() or bool(missing_keys)
        if st.button("▶️ Start scraping", type="primary", disabled=disabled, use_container_width=True):
            if not debug_chrome_is_up():
                st.error("Open Chrome and finish logging in first (Step 1).")
            else:
                start_scraper(
                    COUNTY_OPTIONS[county],
                    DOC_OPTIONS[doc],
                    d_from.strftime("%m/%d/%Y"),
                    d_to.strftime("%m/%d/%Y"),
                )
                st.rerun()
    with stop_col:
        if st.button("⏹ Stop", disabled=not scraper_running(), use_container_width=True):
            stop_scraper()
            st.rerun()

    if scraper_running():
        st.info("Scraper is running. Keep the Chrome window open. Live output below:")
    elif st.session_state.get("scraper_log"):
        st.success("Scraper finished. Output below — see the **Records** tab for results.")

    _render_log()


def _render_log() -> None:
    text = tail(st.session_state.get("scraper_log"))
    st.code(text or "(no output yet)", language="text")


# Auto-refresh the log every 2 seconds while the app is open (Streamlit >= 1.37).
if hasattr(st, "fragment"):
    _render_log = st.fragment(run_every=2.0)(_render_log)  # type: ignore[assignment]


def render_records_tab() -> None:
    import pandas as pd

    if not CSV_PATH.exists():
        st.info("No records yet. Run the scraper to collect some.")
        return

    df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    st.caption(f"Data file: {CSV_PATH}")

    f1, f2 = st.columns([1, 2])
    with f1:
        counties = ["All"] + sorted(c for c in df.get("County", pd.Series([], dtype=str)).unique() if c)
        choice = st.selectbox("County", counties)
    with f2:
        query = st.text_input("Search name (decedent or executor)")

    view = df
    if choice != "All":
        view = view[view["County"] == choice]
    if query:
        q = query.strip().lower()
        name_cols = [c for c in ["Decedent name", "Executor/administrator name"] if c in view.columns]
        if name_cols:
            mask = False
            for c in name_cols:
                mask = mask | view[c].str.lower().str.contains(q, na=False)
            view = view[mask]

    m1, m2 = st.columns(2)
    m1.metric("Total records", len(df))
    m2.metric("Showing", len(view))

    st.dataframe(view, use_container_width=True, height=460)
    st.download_button(
        "⬇️ Download spreadsheet (CSV)",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="probate_records.csv",
        mime="text/csv",
    )

    if "County" in df.columns and not df.empty:
        st.subheader("Records by county")
        st.bar_chart(df["County"].replace("", "Unknown").value_counts())


def render_help_tab() -> None:
    st.markdown(
        f"""
### How to use this tool

1. **Open Chrome & log in** (Step 1 on the Run tab). A separate Chrome window
   opens to the NY Surrogate Court login. Sign in, solve the captcha, then click
   into **File Search**. Keep that window open.
2. **Choose** the county, document type, and date range.
3. **Start scraping.** Each matching case is opened, its PDF downloaded, read
   with OCR, and the key details are added to your spreadsheet.
4. **Records tab** shows everything collected and lets you download the CSV.

**Notes**
- Downloaded PDFs are saved to `~/Downloads/ny-probate`.
- A case already in the spreadsheet is skipped, so re-running is safe.
- If Step 1 says Chrome isn't connected, make sure you opened it with the
  button here (not a normal Chrome window) and that you didn't close it.
- The login page is here if you need it directly: {AUTH_URL}
"""
    )


def main() -> None:
    st.set_page_config(page_title="Phoenix Probate", page_icon="⚖️", layout="wide")
    env = load_env()
    st.title("⚖️ Phoenix Probate")
    st.caption("Collect NY Surrogate Court probate leads — no code, no terminal.")

    render_sidebar(env)
    run_tab, records_tab, help_tab = st.tabs(["▶️ Run", "📊 Records", "❓ Help"])
    with run_tab:
        render_run_tab(env)
    with records_tab:
        render_records_tab()
    with help_tab:
        render_help_tab()


def _running_under_streamlit() -> bool:
    try:
        from streamlit.runtime import exists

        return exists()
    except Exception:
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx

            return get_script_run_ctx() is not None
        except Exception:
            return False


if _running_under_streamlit():
    main()
elif __name__ == "__main__":
    # Allow `python control_panel.py` to just work by re-launching under Streamlit.
    os.execvp(
        sys.executable,
        [sys.executable, "-m", "streamlit", "run", str(Path(__file__).resolve())],
    )
