"""
LLM extractor: turn first-page OCR text into a 10-field JSON.

Inputs
- raw_text: string of OCR from first page (ocr_text/*.txt)

Outputs
- dict with these keys (values may be null when not found):
  - County
  - File number
  - Date of death  (YYYY-MM-DD when possible)
  - Decedent name
  - Decedent address
  - Executor/administrator name
  - Executor/administrator relationship
  - Executor/administrator phone
  - Executor/administrator address
  - Executor/administrator email

Environment
- .env values (loaded lazily):
  OPENAI_API_KEY: required
  OPENAI_MODEL: optional, default 'gpt-4o-mini'
  OPENAI_BASE_URL: optional (for proxy/self-host)
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv


_env_loaded = False


def _ensure_env() -> None:
	global _env_loaded
	if _env_loaded:
		return
	# Allow .env to override any previously exported variables so the project-level
	# configuration always wins when running in VS Code terminals or shells with stale values.
	load_dotenv(override=True)
	# Do not validate key here to allow import in non-LLM flows; client will error if missing
	_env_loaded = True


def _get_openai_client():
	"""Construct OpenAI client using environment.

	Prefers official OpenAI; honors OPENAI_BASE_URL if set (e.g., proxy or Azure-compatible gateway).
	"""
	_ensure_env()
	from openai import OpenAI

	base_url = os.getenv("OPENAI_BASE_URL") or None
	if base_url:
		return OpenAI(base_url=base_url)
	return OpenAI()


def _default_model() -> str:
	m = (os.getenv("OPENAI_MODEL") or "").strip()
	return m or "gpt-4o-mini"


FIELDS = [
	"County",
	"File number",
	"Date of death",
	"Decedent name",
	"Decedent address",
	"Executor/administrator name",
	"Executor/administrator relationship",
	"Executor/administrator phone",
	"Executor/administrator address",
	"Executor/administrator email",
]


SYSTEM_PROMPT = (
	"You are a strict information extraction engine for New York Surrogate Court probate forms. "
	"Given OCR text from the FIRST PAGE of a probate filing (often a Voluntary Administration Affidavit), "
	"extract only the requested fields. If a field is not present, return null. "
	"\n\nIMPORTANT DISTINCTIONS:\n"
	"- 'Decedent' is the person who died\n"
	"- 'Executor/administrator' (also called 'Vol. Adm.' or 'Voluntary Administrator') is the living person handling the estate\n"
	"- If you see 'Name of Vol. Adm.' or 'Voluntary Administrator', that is the EXECUTOR/ADMINISTRATOR, NOT the decedent\n"
	"- The address next to 'Name and Address' or 'Vol. Adm.' is usually the EXECUTOR/ADMINISTRATOR address\n"
	"- The 'Decedent address' is often NOT shown on page 1 of Voluntary Admin forms - return null if not found\n"
	"\n"
	"Normalize dates to YYYY-MM-DD when possible. Phone numbers should be US formatted like (212) 555-1234 when possible. "
	"Addresses can be returned as a single line if multiple lines are present. Avoid hallucinating; prefer null over guessing. "
	"Output must be a single JSON object with exactly the specified keys."
)


def _build_user_prompt(raw_text: str) -> str:
	guidance = "\n".join(
		[
			"Return JSON with these keys:",
			*[f"- {k}" for k in FIELDS],
			"",
			"Text:",
			raw_text.strip(),
		]
	)
	return guidance


def _coerce_json(s: str) -> Dict[str, Any]:
	"""Try to parse model output into JSON, even if surrounded by extra text or code fences."""
	s = s.strip()
	# Remove Markdown fences
	if s.startswith("```") and s.endswith("```"):
		s = re.sub(r"^```(?:json)?\n|\n```$", "", s, flags=re.IGNORECASE)
	# Find the first {...} block if needed
	if not s.startswith("{"):
		m = re.search(r"\{[\s\S]*\}", s)
		if m:
			s = m.group(0)
	return json.loads(s)


def extract_structured_fields(raw_text: str, *, model: Optional[str] = None, max_retries: int = 3) -> Dict[str, Any]:
	"""Call the LLM to extract the 10-field JSON from first-page OCR text.

	Retries on transient API/parse issues. Missing fields should be null.
	"""
	client = _get_openai_client()
	model = model or _default_model()

	last_err: Optional[Exception] = None
	for attempt in range(1, max_retries + 1):
		try:
			resp = client.chat.completions.create(
				model=model,
				temperature=1.0,
				response_format={"type": "json_object"},
				messages=[
					{"role": "system", "content": SYSTEM_PROMPT},
					{"role": "user", "content": _build_user_prompt(raw_text)},
				],
			)
			content = resp.choices[0].message.content or "{}"
			data = _coerce_json(content)
			# Ensure all keys present; fill missing with None
			for k in FIELDS:
				data.setdefault(k, None)
			# Trim whitespace strings; empty -> None
			for k, v in list(data.items()):
				if isinstance(v, str):
					vv = v.strip()
					data[k] = vv if vv else None
			return data
		except Exception as e:  # parse errors / API errors
			last_err = e
			# brief backoff
			time.sleep(0.8 * attempt)
	# Surface the last error
	raise RuntimeError(f"LLM extraction failed after {max_retries} attempts: {last_err}")


def extract_file(txt_path: str, *, model: Optional[str] = None) -> Dict[str, Any]:
	# Try UTF-8 first, fall back to latin-1 or ignore errors if there are encoding issues
	try:
		with open(txt_path, "r", encoding="utf-8") as f:
			raw = f.read()
	except UnicodeDecodeError:
		# Fallback: try latin-1 encoding or replace invalid characters
		try:
			with open(txt_path, "r", encoding="latin-1") as f:
				raw = f.read()
		except Exception:
			# Last resort: ignore bad characters
			with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
				raw = f.read()
	return extract_structured_fields(raw, model=model)


def _main():
	import argparse
	from pathlib import Path

	ap = argparse.ArgumentParser(description="Extract 10-field JSON from OCR text using OpenAI")
	ap.add_argument("txt", help="Path to OCR .txt file")
	ap.add_argument("--model", help="OpenAI model name (overrides OPENAI_MODEL)")
	ap.add_argument("--out", help="Output JSON path (default: structured_json/<stem>.json)")
	args = ap.parse_args()

	txt = Path(args.txt)
	if not txt.exists():
		raise FileNotFoundError(str(txt))

	model = args.model or None
	result = extract_file(str(txt), model=model)

	out = Path(args.out) if args.out else Path("structured_json") / (txt.stem + ".json")
	out.parent.mkdir(parents=True, exist_ok=True)
	with open(out, "w", encoding="utf-8") as f:
		json.dump(result, f, ensure_ascii=False, indent=2)
	print(str(out))


if __name__ == "__main__":
	_main()

