"""
Compat runner that uses the new azure_ocr module to process PDFs.
First-page only OCR, saves raw JSON and first-page text.
"""

import json
from pathlib import Path
from tqdm import tqdm

from azure_ocr import ocr_first_page


PDF_DIR = Path("./pdfs")


def process_pdf(path: Path):
    res = ocr_first_page(str(path))
    return {"path": str(path), **res}


def main():
    # Default sample: process everything in ~/Downloads/ny-probate if present
    dl_dir = Path.home() / "Downloads" / "ny-probate"
    if dl_dir.exists():
        pdfs = sorted(dl_dir.glob("*.pdf"))
    else:
        pdfs = sorted(PDF_DIR.glob("*.pdf"))

    if not pdfs:
        print("No PDFs found. Place files in ~/Downloads/ny-probate or ./pdfs")
        return
    results = []
    for p in tqdm(pdfs):
        try:
            r = process_pdf(p)
            results.append(r)
        except Exception as e:
            results.append({"path": str(p), "error": str(e)})
    with open("summary.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("done. raw json in ./ocr_json, text in ./ocr_text")


if __name__ == "__main__":
    main()
