@echo off
REM Start the HTML Dashboard for Probate Records
cd /d "%~dp0"

echo.
echo ==========================================
echo   Probate Records Dashboard
echo ==========================================
echo.

REM Check if virtual environment exists
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Install flask and flask-cors if needed
pip show flask >nul 2>&1
if errorlevel 1 (
    echo Installing Flask...
    pip install flask flask-cors
)

echo Starting server at http://localhost:5000
echo Press Ctrl+C to stop the server
echo.

python dashboard_server.py
