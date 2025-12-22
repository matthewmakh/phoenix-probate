"""
Batch backfill from OCR text files to structured 10-field JSON using OpenAI.

Skips files that already exist in a CSV by matching either file number or decedent name
(to avoid re-sending to OpenAI and to not worry about clearing old Azure-parsed files).

Default locations:
- Input dir:  ./ocr_text/*.txt
- Output dir: ./structured_json/<stem>.json
- CSV check:  ./data/probate_records.csv (if present)

Examples:
	python3 batch_llm_backfill.py --pattern "2025-3729_*.txt" --limit 1
	python3 batch_llm_backfill.py --overwrite --pattern "*.txt"
	python3 batch_llm_backfill.py --input-dir ./ocr_text --output-dir ./structured_json
	python3 batch_llm_backfill.py --csv-check-path ./data/probate_records.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Optional, Tuple, Set
import csv
import re

from dotenv import load_dotenv

from llm_extractor import extract_file, FIELDS as LLM_FIELDS


def iter_txt_files(root: Path, pattern: str) -> Iterable[Path]:
	return sorted(root.glob(pattern))


# --- Simple patterns (kept in sync with pdf_data_extractor.py) ---
FILE_NO_PAT = re.compile(r"\b(202\d-\d{4})(?:/[A-Z])?\b|\bFile\s*No\.?\s*([\w-]+)\b", re.I)
D_NAME = r"[A-Z][A-Za-z'`\-]+"
DECEDENT_PAT = re.compile(rf"\bDecedent\b[:\s\-]*({D_NAME}(?:\s+{D_NAME}){{1,3}})", re.I)
DECEDENT_PAT2 = re.compile(rf"\b(?:In\s+the\s+Matter\s+of\s+the\s+Estate\s+of|Estate\s+of|Matter\s+of)\b[:\s\-]*({D_NAME}(?:\s+{D_NAME}){{0,3}})", re.I)


def _load_csv_keys(csv_path: Path) -> Tuple[Set[str], Set[str]]:
	"""Return (file_numbers, decedent_names) from an existing CSV.
	Compares case-insensitively; empty values are ignored.
	"""
	fns: Set[str] = set()
	names: Set[str] = set()
	if not csv_path.exists():
		return fns, names
	try:
		with open(csv_path, "r", encoding="utf-8", newline="") as f:
			rdr = csv.DictReader(f)
			# normalize header map to lowercase for flexible lookup
			headers = [h.strip() for h in (rdr.fieldnames or [])]
			header_lut = {h.lower(): h for h in headers}
			# possible column variants
			fn_keys = [
				header_lut.get("file number"),
				header_lut.get("file_number"),
				header_lut.get("filenumber"),
			]
			name_keys = [
				header_lut.get("decedent name"),
				header_lut.get("decedent_name"),
				header_lut.get("name"),
			]
			for row in rdr:
				# file number
				fn_val = ""
				for k in fn_keys:
					if k and row.get(k):
						fn_val = row.get(k, "").strip()
						if fn_val:
							break
				if fn_val:
					fns.add(fn_val.lower())
				# name
				nm_val = ""
				for k in name_keys:
					if k and row.get(k):
						nm_val = row.get(k, "").strip()
						if nm_val:
							break
				if nm_val:
					names.add(nm_val.lower())
	except Exception:
		# Non-fatal; just return empty sets if unreadable
		pass
	return fns, names


def _derive_keys_from_txt(txt_path: Path) -> Tuple[Optional[str], Optional[str]]:
	"""Best-effort extraction of (file_number, decedent_name) from the OCR text and/or filename.
	Avoids LLM; uses lightweight regexes.
	"""
	# 1) Try filename first: prefix like 2025-3729_...
	m = re.search(r"\b(202\d-\d{4})\b", txt_path.stem)
	file_no = m.group(1) if m else None
	# 2) Parse the text for stronger hints
	try:
		text = txt_path.read_text(encoding="utf-8")
	except Exception:
		text = ""
	if not file_no:
		m2 = FILE_NO_PAT.search(text)
		if m2:
			file_no = next((g for g in m2.groups() if g), None)
	# Decedent name
	decedent_name: Optional[str] = None
	for pat in (DECEDENT_PAT, DECEDENT_PAT2):
		m = pat.search(text)
		if m:
			decedent_name = m.group(1).strip()
			break
	return file_no, decedent_name


def main(argv=None) -> int:
	ap = argparse.ArgumentParser(description="Backfill structured JSON from OCR text using OpenAI")
	ap.add_argument("--input-dir", default="./ocr_text", help="Directory with OCR .txt files (default: ./ocr_text)")
	ap.add_argument("--output-dir", default="./structured_json", help="Directory to write JSON (default: ./structured_json)")
	ap.add_argument("--pattern", default="*.txt", help="Glob pattern to select files (default: *.txt)")
	ap.add_argument("--limit", type=int, default=None, help="Process at most N files")
	ap.add_argument("--overwrite", action="store_true", help="Overwrite existing JSON files")
	ap.add_argument("--model", help="Override OPENAI_MODEL for this run")
	ap.add_argument(
		"--csv-check-path",
		default="./data/probate_records.csv",
		help="CSV to use for skip checks (default: ./data/probate_records.csv)",
	)
	ap.add_argument(
		"--csv-out",
		default="./data/probate_records.csv",
		help="CSV to upsert 10-field records into (default: ./data/probate_records.csv)",
	)
	ap.add_argument(
		"--no-csv-skip",
		action="store_true",
		help="Disable skipping based on CSV contents",
	)
	args = ap.parse_args(argv)

	# Ensure .env can override any stale shell vars (e.g., OPENAI_API_KEY)
	load_dotenv(override=True)

	in_dir = Path(args.input_dir)
	out_dir = Path(args.output_dir)
	out_dir.mkdir(parents=True, exist_ok=True)

	# Load CSV keys once for skip checks
	csv_fns: Set[str] = set()
	csv_names: Set[str] = set()
	csv_path = Path(args.csv_check_path)
	if not args.no_csv_skip:
		csv_fns, csv_names = _load_csv_keys(csv_path)

	files = iter_txt_files(in_dir, args.pattern)
	if args.limit is not None:
		files = files[: args.limit]

	processed = 0
	skipped = 0
	failed = 0
	csv_out = Path(args.csv_out)

	def _ensure_dir_for(path: Path) -> None:
		d = path.parent
		if not d.exists():
			d.mkdir(parents=True, exist_ok=True)

	def _norm_str(v: Optional[str]) -> str:
		return (v or "").strip()

	def _row_key_10(row: dict) -> tuple:
		# Prefer File number, else Decedent name. Case-insensitive.
		fn = _norm_str(row.get("File number"))
		nm = _norm_str(row.get("Decedent name"))
		if fn:
			return ("fn", fn.lower())
		return ("nm", nm.lower())

	def _write_10field_csv_row(csv_path: Path, record: dict) -> None:
		# Prepare normalized 10-field row based on LLM output
		row = {k: _norm_str(record.get(k)) for k in LLM_FIELDS}
		_ensure_dir_for(csv_path)

		existing: list[dict] = []
		if csv_path.exists():
			with open(csv_path, "r", encoding="utf-8", newline="") as f:
				rdr = csv.DictReader(f)
				for r in rdr:
					existing.append(dict(r))

		key_new = _row_key_10(row)
		replaced = False
		for i, r in enumerate(existing):
			if _row_key_10(r) == key_new:
				# replace with new values (preserve only known columns)
				existing[i] = {k: row.get(k, "") for k in LLM_FIELDS}
				replaced = True
				break
		if not replaced:
			existing.append({k: row.get(k, "") for k in LLM_FIELDS})

		with open(csv_path, "w", encoding="utf-8", newline="") as f:
			w = csv.DictWriter(f, fieldnames=LLM_FIELDS)
			w.writeheader()
			for r in existing:
				w.writerow({k: _norm_str(r.get(k)) for k in LLM_FIELDS})
	for txt in files:
		out = out_dir / (txt.stem + ".json")
		if out.exists() and not args.overwrite:
			skipped += 1
			continue

		# Skip if CSV contains either same file number or exact decedent name
		if not args.no_csv_skip and (csv_fns or csv_names):
			cand_fn, cand_name = _derive_keys_from_txt(txt)
			if cand_fn and cand_fn.lower() in csv_fns:
				print(f"SKIP {txt.name}: file number {cand_fn} already present in CSV ({csv_path})")
				skipped += 1
				continue
			if cand_name and cand_name.lower() in csv_names:
				print(f"SKIP {txt.name}: decedent name '{cand_name}' already present in CSV ({csv_path})")
				skipped += 1
				continue
		try:
			data = extract_file(str(txt), model=args.model or None)
			with open(out, "w", encoding="utf-8") as f:
				json.dump(data, f, ensure_ascii=False, indent=2)
			# Upsert into 10-field CSV immediately so progress is durable
			_write_10field_csv_row(csv_out, data)
			print(f"OK  {txt.name} -> {out.name}")
			processed += 1
		except Exception as e:
			print(f"ERR {txt.name}: {e}")
			failed += 1

	print(f"\nDone. processed={processed} skipped={skipped} failed={failed}")
	return 0 if failed == 0 else 1


if __name__ == "__main__":
	sys.exit(main())

