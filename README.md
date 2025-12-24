# NY Surrogate Court Probate Scraper (Windows Version)

This script automates searching the NY Surrogate Court site after you manually solve the captcha, then downloads the "Probate Petition" PDF for each case and uploads it to Google Drive.

## What it does
- Attaches to your running Chrome (via remote debugging) so it uses your cookies/profile
- Fills out the search form (court, proceeding, date range)
- Opens each result in a new tab, finds the Probate Petition document, auto-downloads it
- Saves to %USERPROFILE%\Downloads\ny-probate and uploads to Drive under folder Probate_Petitions

## Requirements
- Windows 10/11 with Google Chrome installed
- Python 3.10+ (recommend Python 3.12)
- Packages: selenium, google-api-python-client, google-auth, google-auth-oauthlib (see 
equirements.txt)
- A Google account to authorize Drive access on first run

## One-time setup

### 1) Create a Python virtual environment and install dependencies

Open PowerShell or Command Prompt:

```powershell
cd path\to\phoenix-probate
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Add OpenAI package if using LLM extraction:
```powershell
pip install openai
```

### 2) Set up environment variables

Create a .env file in the project directory with your API keys:

```
# Azure Computer Vision (for OCR)
AZ_ENDPOINT=https://<region>.api.cognitive.microsoft.com
AZ_KEY=your-azure-key

# OpenAI (for LLM extraction)
OPENAI_API_KEY=your-openai-key

# Google Drive (optional)
GOOGLE_OAUTH_CLIENT_ID=your-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
```

### 3) Ensure ChromeDriver compatibility
- This script launches Selenium in **attach mode** to your existing Chrome via DevTools
- If Selenium complains about driver, install webdriver-manager:
  ```powershell
  pip install webdriver-manager
  ```

## How to run

### Method 1: Using the batch scripts

1) **Start Chrome with remote debugging:**
   
   Double-click start_debug_chrome.bat or run:
   ```powershell
   .\start_debug_chrome.bat
   ```

2) **Run the scraper** in a new PowerShell (with venv activated):
   ```powershell
   .venv\Scripts\python probate_scraper.py
   ```

### Method 2: Manual Chrome launch

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:USERPROFILE\chrome-remote"
```

## Streamlit Dashboard

```powershell
.\start_dashboard.bat
```

Or:
```powershell
.venv\Scripts\streamlit run dashboard.py
```

## Configuration

Edit constants in probate_scraper.py:
- COURT_VALUE (Queens = 41)
- DATE_FROM, DATE_TO (MM/DD/YYYY)

## Platform Notes

This branch has been modified for Windows:
- Mac paths replaced with cross-platform paths
- Added Windows batch files (.bat)
- Original macOS version is in the main branch

---

## Railway Deployment (Dashboard + PostgreSQL)

The dashboard can be deployed to Railway with a PostgreSQL database, while keeping the scraper running locally.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         RAILWAY                              │
│  ┌─────────────────────┐    ┌────────────────────────────┐  │
│  │  Dashboard Server   │◄──►│  PostgreSQL Database       │  │
│  │  (dashboard_server  │    │  (probate_records,         │  │
│  │   _pg.py)           │    │   crm_records, activities) │  │
│  └─────────────────────┘    └────────────────────────────┘  │
│            ▲                             ▲                   │
└────────────│─────────────────────────────│───────────────────┘
             │                             │
             │ (view)                      │ (write)
             │                             │
┌────────────│─────────────────────────────│───────────────────┐
│  LOCAL     │                             │                   │
│            │                   ┌─────────┴───────────┐       │
│       Web Browser              │  Scraper            │       │
│                                │  (probate_scraper   │       │
│                                │   .py)              │       │
│                                └─────────────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

### Deploy to Railway

1. **Push to GitHub** (or connect Railway to your repo)

2. **Create a new project on Railway:**
   - Go to https://railway.app
   - Create new project → Deploy from GitHub repo

3. **Add PostgreSQL plugin:**
   - In your Railway project, click "New" → "Database" → "PostgreSQL"
   - Railway will automatically set `DATABASE_URL` environment variable

4. **Configure environment variables:**
   - The `DATABASE_URL` is set automatically when you add PostgreSQL
   - No other env vars needed for the dashboard

5. **Deploy:**
   - Railway will auto-detect the `Procfile` and deploy
   - Dashboard will be available at `your-project.up.railway.app`

### Configure Local Scraper

1. **Get your Railway DATABASE_URL:**
   - Go to Railway dashboard → PostgreSQL service → Variables
   - Copy the `DATABASE_URL` value

2. **Create local `.env` file:**
   ```
   DATABASE_URL=postgresql://user:password@host:port/railway
   
   # Your other local env vars
   AZ_ENDPOINT=...
   AZ_KEY=...
   OPENAI_API_KEY=...
   ```

3. **Migrate existing CSV data (optional):**
   ```powershell
   .venv\Scripts\python migrate_csv_to_db.py
   ```

4. **Run scraper** - it will now save to both CSV (backup) and PostgreSQL:
   ```powershell
   .venv\Scripts\python probate_scraper.py
   ```

### Files for Railway

| File | Purpose |
|------|---------|
| [Procfile](Procfile) | Tells Railway how to start the app |
| [railway.json](railway.json) | Railway deployment configuration |
| [dashboard_server_pg.py](dashboard_server_pg.py) | PostgreSQL-enabled dashboard server |
| [database.py](database.py) | SQLAlchemy models and database connection |
| [db_client.py](db_client.py) | Database client for local scraper |
| [migrate_csv_to_db.py](migrate_csv_to_db.py) | Migration script for existing data |
| [requirements-railway.txt](requirements-railway.txt) | Minimal dependencies for Railway |

### Scraper Configuration

In [probate_scraper.py](probate_scraper.py), you can configure:

```python
# Save to PostgreSQL (Railway database)
USE_POSTGRESQL = True

# Skip cases already in database
SKIP_EXISTING_IN_DB = True

# Also save to local CSV as backup
SKIP_EXISTING_IN_CSV = True
```
