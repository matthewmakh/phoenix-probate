@echo off
REM Start the Streamlit dashboard on Windows

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "APP_FILE=%SCRIPT_DIR%dashboard.py"

REM Check if .venv exists and use it
if exist "%SCRIPT_DIR%.venv\Scripts\streamlit.exe" (
    "%SCRIPT_DIR%.venv\Scripts\streamlit.exe" run "%APP_FILE%"
) else (
    REM Fallback to global streamlit
    streamlit run "%APP_FILE%"
)
