@echo off
REM Launch a clean Chrome instance for Selenium remote debugging on Windows.
REM Uses a dedicated user-data-dir so it won't collide with your main browser.
REM After launching, verify with: netstat -an | findstr 9222

setlocal enabledelayedexpansion

REM Default Chrome path - try common installation locations
set "CHROME_APP="

REM Check for Chrome in Program Files
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    set "CHROME_APP=C:\Program Files\Google\Chrome\Application\chrome.exe"
) else if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    set "CHROME_APP=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
) else if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" (
    set "CHROME_APP=%LocalAppData%\Google\Chrome\Application\chrome.exe"
)

if "%CHROME_APP%"=="" (
    echo ERROR: Could not find Google Chrome installation.
    echo Please install Chrome or update the CHROME_APP path in this script.
    pause
    exit /b 1
)

set "USER_DIR=%USERPROFILE%\chrome-remote"
set "PORT=9222"

REM Create user data directory if it doesn't exist
if not exist "%USER_DIR%" mkdir "%USER_DIR%"

echo Starting Chrome with remote debugging on port %PORT%...
echo User data directory: %USER_DIR%
echo.
echo After Chrome opens, navigate to:
echo https://websurrogates.nycourts.gov/Home/AuthenticatePage
echo.
echo Log in and solve the captcha, then navigate to File Search.
echo Leave Chrome open and run probate_scraper.py in another terminal.

REM Set download directory and disable PDF viewer
set "DOWNLOAD_DIR=%USERPROFILE%\Downloads"

start "" "%CHROME_APP%" ^
    --remote-debugging-port=%PORT% ^
    --user-data-dir="%USER_DIR%" ^
    --no-first-run ^
    --no-default-browser-check ^
    --disable-features=AutofillServerCommunication ^
    --disable-breakpad ^
    --disable-pdf-viewer ^
    --disable-plugins-discovery

echo Chrome started. Keep this window open or close it - Chrome will continue running.
echo.
echo NOTE: If PDFs still open in browser, go to Chrome Settings ^> Privacy and Security
echo       ^> Site Settings ^> Additional content settings ^> PDF documents
echo       and select "Download PDFs"
