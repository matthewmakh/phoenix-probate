# PDF data extractor (non‑OCR)

`pdf_data_extractor.py` currently extracts key fields from NY Surrogate's Court PDFs using embedded text and PDF metadata only (no OCR). This is a temporary simplified build while OCR is migrated to Oracle's API.

What it uses now:
- PyMuPDF to read per-page embedded text
- PDF metadata fields (author, title, keywords) to backfill values
- Regex parsing for common fields (file number, decedent name, case type, etc.)

What it does not use now:
- pytesseract / Tesseract
- OpenCV / Pillow image preprocessing

## Run it

From the project folder:

```zsh
./.venv/bin/python pdf_data_extractor.py /path/to/file.pdf
./.venv/bin/python pdf_data_extractor.py --dir "$HOME/Downloads/ny-probate"
```

Useful flags:
- `--max-pages N`: Limit to first N pages parsed
- `--with-pages`: Include per-page embedded text in the JSON
- `--csv-out`: Path to the CSV the dashboard reads (defaults to `data/probate_records.csv`)

Notes:
- Image-only scans without embedded text will likely have fewer fields populated until OCR is reintroduced.
- The dashboard reads `data/probate_records.csv`; running this script will upsert rows into that file.
