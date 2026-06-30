#!/usr/bin/env python3
"""
PyInstaller entry point for a fully standalone "Phoenix Probate.app".

Subcommands:
  (no args)      launch the Streamlit control panel
  scrape         run the probate scraper (the panel calls this as a subprocess)

When frozen, user data (config + spreadsheet + downloads) is kept in a writable
folder under ~/Library/Application Support so nothing is written inside the .app
bundle itself.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def _prepare_workspace() -> Path:
    """Create a writable workspace and seed it from the bundle on first run."""
    work = Path.home() / "Library" / "Application Support" / "Phoenix Probate"
    (work / "data").mkdir(parents=True, exist_ok=True)
    (work / "downloads").mkdir(parents=True, exist_ok=True)
    bundle = _bundle_dir()

    env_dst = work / ".env"
    if not env_dst.exists():
        for name in (".env", ".env.example"):
            src = bundle / name
            if src.exists():
                shutil.copy2(src, work / name)
                break

    csv_name = "probate_records.csv"
    csv_dst = work / "data" / csv_name
    if not csv_dst.exists():
        src = bundle / "data" / csv_name
        if src.exists():
            shutil.copy2(src, csv_dst)

    os.environ.setdefault("PB_APP_DIR", str(work))
    os.environ.setdefault("PB_CSV_PATH", str(csv_dst))
    os.environ.setdefault("PB_DOWNLOAD_DIR", str(work / "downloads"))
    return work


def run_scraper() -> int:
    sys.path.insert(0, str(_bundle_dir()))
    import probate_scraper

    probate_scraper.main()
    return 0


def run_panel() -> int:
    target = str(_bundle_dir() / "control_panel.py")
    sys.argv = [
        "streamlit",
        "run",
        target,
        "--server.headless=false",
        "--server.port=8501",
        "--browser.gatherUsageStats=false",
    ]
    from streamlit.web import cli as stcli

    return stcli.main()


def main() -> int:
    if getattr(sys, "frozen", False):
        _prepare_workspace()
    if len(sys.argv) > 1 and sys.argv[1] == "scrape":
        return run_scraper()
    return run_panel()


if __name__ == "__main__":
    sys.exit(main())
