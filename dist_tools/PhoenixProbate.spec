# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for a fully standalone "Phoenix Probate.app".

Build (on a Mac):  bash dist_tools/make_app.sh
This is the ADVANCED path. The double-click launcher + DMG (dist_tools/make_dmg.sh)
is the recommended, well-tested way to ship the app.
"""
import os
from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = os.path.abspath(os.getcwd())

datas, binaries, hiddenimports = [], [], []

# Pull in everything these packages need (data files, submodules, binaries).
for pkg in [
    "streamlit", "selenium", "pandas", "numpy", "altair", "pyarrow",
    "openai", "fitz", "dateutil", "dotenv", "requests", "PIL",
    "google", "googleapiclient", "google_auth_oauthlib",
]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# Streamlit reads package metadata at runtime; make sure it's present.
for meta in [
    "streamlit", "pandas", "numpy", "altair", "pyarrow", "openai", "selenium",
    "requests", "PyMuPDF", "python-dateutil", "protobuf",
    "google-api-python-client", "google-auth", "google-auth-oauthlib",
]:
    try:
        datas += copy_metadata(meta)
    except Exception:
        pass

# Our own source + starting data so the panel and scraper can find them.
for f in [
    "control_panel.py", "probate_scraper.py", "azure_ocr.py", "llm_extractor.py",
    "pdf_data_extractor.py", "batch_llm_backfill.py", "requirements.txt", ".env.example",
]:
    p = os.path.join(ROOT, f)
    if os.path.exists(p):
        datas.append((p, "."))
if os.path.exists(os.path.join(ROOT, ".env")):
    datas.append((os.path.join(ROOT, ".env"), "."))
if os.path.isdir(os.path.join(ROOT, "data")):
    datas.append((os.path.join(ROOT, "data"), "data"))

hiddenimports += ["streamlit.runtime.scriptrunner.magic_funcs"]

a = Analysis(
    ["dist_tools/entry.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PhoenixProbate",
    console=True,
)

coll = COLLECT(exe, a.binaries, a.datas, name="PhoenixProbate")

app = BUNDLE(
    coll,
    name="Phoenix Probate.app",
    icon=None,
    bundle_identifier="com.phoenixrealty.probate",
)
