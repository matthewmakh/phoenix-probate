# Phoenix Probate — Setup (for the person receiving this)

A simple tool that collects New York Surrogate Court probate leads into a
spreadsheet. You don't need to know any code.

## What you need
- A Mac
- **Google Chrome** installed (free: https://www.google.com/chrome)
- An internet connection

## One-time install
1. If you received a **`.dmg`**, double-click it, then drag the **Phoenix
   Probate** folder onto your **Applications** (or Desktop). If you received a
   **`.zip`**, double-click to unzip it.
2. Open the Phoenix Probate folder and double-click **`Launch Phoenix
   Probate.command`**.
3. **First time only:** macOS may say it's *“from an unidentified developer.”*
   - Right-click (or Control-click) the launcher → **Open** → **Open**.
   - This only happens the first time.
4. A Terminal window opens and sets things up (a few minutes the first time).
   Then your web browser opens the app automatically. **Leave the Terminal
   window open** while you work.

> If the browser doesn't open by itself, go to **http://localhost:8501**.

## Using it
The app walks you through three steps:

1. **Open Chrome & log in** — click the button. A Chrome window opens to the NY
   Surrogate Court login. **Sign in, solve the captcha, then click into File
   Search.** Leave that Chrome window open.
2. **Choose** the county, document type, and date range.
3. **Start scraping** — watch the live progress. Each case is downloaded, read,
   and added to your spreadsheet.

Open the **Records** tab any time to see results and download the spreadsheet.

## Tips
- Re-running is safe — cases already collected are skipped.
- Downloaded PDFs are in your **Downloads → ny-probate** folder.
- To stop, click **Stop** in the app, or just close the Terminal window.
- If the app says a key is missing, ask the person who sent it to you for the
  Azure and OpenAI keys, then paste them into the sidebar and click **Save**.
- If a case's PDF opens in its own tab instead of downloading: in that Chrome
  window, open a new tab, go to `chrome://settings/content/pdfDocuments`, and
  turn on **"Download PDFs instead of automatically opening them in Chrome."**
  One-time fix — it stays on after that.
