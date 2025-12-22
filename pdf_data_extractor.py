import argparse
import io
import json
import os
import re
import csv
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

# Third-party libs (declared in requirements.txt)
try:
	import fitz  # PyMuPDF
except Exception as e:  # pragma: no cover
	fitz = None  # type: ignore

# Azure OCR for first page
try:
	from azure_ocr import ocr_first_page
except Exception:
	ocr_first_page = None  # type: ignore

# Optional phone number formatter
try:
    import phonenumbers  # type: ignore
except Exception:
    phonenumbers = None  # type: ignore


# ------------------------------
# Data structures
# ------------------------------


@dataclass
class PageResult:
	index: int
	text: str
	ocr_used: bool


@dataclass
class ExtractedData:
	file_number: Optional[str]
	decedent_name: Optional[str]
	decedent_dod: Optional[str]
	county: Optional[str]
	docket: Optional[str]
	case_type: Optional[str]
	first_page_phone: Optional[str]
	first_page_name: Optional[str]
	first_page_email: Optional[str]
	first_page_emails: List[str]
	first_page_address_lines: List[str]
	additional: Dict[str, str]
	pages: List[PageResult]


# ------------------------------
# Low-level helpers
# ------------------------------


def _ensure_deps():
    if fitz is None:
        raise RuntimeError(
            "Missing dependency: PyMuPDF (fitz). Run pip install -r requirements.txt"
        )


def _ocr_first_page_contact(pdf_path: str) -> Dict[str, Optional[str]]:
	# Non-OCR build: not supported
	return {"phone": None, "name": None}


def extract_text_from_pdf(pdf_path: str, force_ocr: bool = False, max_pages: Optional[int] = None) -> List[PageResult]:
	"""Extract text from PDF pages.

	Behavior for this project:
	  - Always use Azure OCR for the FIRST PAGE ONLY (azure-only mode)
	  - Do NOT OCR additional pages; for other pages, optionally extract embedded text
		(but by default we only care about the first page for dashboard fields)
	"""
	if not os.path.exists(pdf_path):
		raise FileNotFoundError(pdf_path)

	results: List[PageResult] = []

	# 1) First page text via Azure OCR (but reuse cached text if present)
	from pathlib import Path
	stem = Path(pdf_path).stem
	cached_text_path = Path("ocr_text") / f"{stem}.txt"
	first_text = ""
	if cached_text_path.exists():
		try:
			first_text = cached_text_path.read_text(encoding="utf-8")
		except Exception:
			first_text = ""
	if not first_text:
		if ocr_first_page is None:
			raise RuntimeError("azure_ocr module not available; ensure azure_ocr.py exists and dependencies are installed.")
		azure = ocr_first_page(pdf_path)
		first_text = azure.get("text", "") or ""
	results.append(PageResult(index=1, text=first_text, ocr_used=True))

	# 2) If caller asked for more than 1 page, we can include embedded text for subsequent pages
	#    but requirement says Azure-only first page, and dashboard uses first page fields.
	if max_pages and max_pages > 1:
		_ensure_deps()
		doc = fitz.open(pdf_path)
		try:
			# Start from page 2 (index 1)
			for i in range(1, min(len(doc), max_pages)):
				page = doc[i]
				text = page.get_text("text") or ""
				results.append(PageResult(index=i + 1, text=text, ocr_used=False))
		finally:
			doc.close()

	return results


# ------------------------------
# Parsing logic tailored to NY probate forms
# ------------------------------


FILE_NO_PAT = re.compile(r"\b(202\d-\d{4})(?:/[A-Z])?\b|\bFile\s*No\.?\s*([\w-]+)\b", re.I)
D_NAME = r"[A-Z][A-Za-z'`\-]+"
DECEDENT_PAT = re.compile(rf"\bDecedent\b[:\s\-]*({D_NAME}(?:\s+{D_NAME}){{1,3}})", re.I)
DECEDENT_PAT2 = re.compile(rf"\b(?:In\s+the\s+Matter\s+of\s+the\s+Estate\s+of|Estate\s+of|Matter\s+of)\b[:\s\-]*({D_NAME}(?:\s+{D_NAME}){{0,3}})", re.I)
DOD_PAT = re.compile(r"\bDate\s+of\s+Death\b[:\s\-]*([0-9]{1,2}[\-/][0-9]{1,2}[\-/][0-9]{2,4}|[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", re.I)
COUNTY_PAT = re.compile(r"\bCounty\b[:\s\-]*([A-Z][A-Za-z\s]+)")
DOCKET_PAT = re.compile(r"\bDocket\s*No\.?\s*([A-Za-z0-9\-\/]+)", re.I)
CASE_PAT = re.compile(r"\b(Voluntary\s+Admin(?:istration)?\s+Affidavit|Administration|Probate|With\s+Will|Without\s+Will)\b", re.I)

# First-page fields
PHONE_PAT = re.compile(r"(?:\+?1[\s\-.]?)?(?:\(\d{3}\)|\d{3})[\s\-.]?\d{3}[\s\-.]?\d{4}(?:\s*(?:ext|x|extension)\s*\.?\s*\d{1,6})?", re.I)
NAME_LABEL_PAT = re.compile(r"\bName\b[:\-]?\s*(.+)", re.I)
NAME_LABEL_PAT2 = re.compile(r"\b(?:Affiant|Applicant|Petitioner|Attorney)[^\n]{0,30}?\bName\b[:\-]?\s*(.+)", re.I)
EMAIL_PAT = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)

# Known counties and some common NYC localities to avoid misclassifying as names
KNOWN_COUNTIES = {"Queens", "Kings", "Bronx", "New York", "Richmond", "Nassau", "Suffolk"}
LOCALITY_BLOCKLIST = {
	# Boroughs / city
	"new york", "queens", "bronx", "kings", "richmond", "brooklyn", "manhattan", "staten island",
	# Queens neighborhoods (common in these forms)
	"middle village", "astoria", "jamaica", "flushing", "elmhurst", "east elmhurst", "ozone park",
	"ridgewood", "forest hills", "rego park", "woodside", "sunnyside", "richmond hill", "briarwood",
	"fresh meadows", "howard beach", "kew gardens", "long island city", "corona", "bayside",
}


def _is_street_line(s: str) -> bool:
	s = s.strip()
	if not s:
		return False
	# Contains a street number and a common street suffix
	if not re.search(r"\b\d{1,6}\b", s):
		return False
	return bool(re.search(r"\b(St|St\.|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Pl|Place|Pkwy|Parkway|Ter|Terrace|Way)\b", s, re.I))


def _is_unit_line(s: str) -> bool:
	return bool(re.search(r"\b(Apt|Apartment|Unit|Suite|Ste|Floor|Fl)\b\s*#?\s*[A-Za-z0-9-]*", s, re.I))


def _is_city_state_zip(s: str) -> bool:
	s = s.strip()
	# Match City, ST 12345 or City ST 12345(-1234)
	return bool(re.search(r"[A-Za-z .\-]+,?\s*[A-Z]{2}\s*\d{5}(?:-\d{4})?\b", s))


def _parse_keywords_meta(keywords: str) -> Dict[str, str]:
	# Expected patterns like: "Carmelo Cinquemani, 2025-2685/A, 2025-2685, 2025-07-24"
	result: Dict[str, str] = {}
	parts = [p.strip() for p in keywords.split(",") if p.strip()]
	if parts:
		# First part often decedent name
		if re.match(rf"^{D_NAME}(?:\s+{D_NAME}){{0,3}}$", parts[0]):
			result["decedent_name"] = parts[0]
	# File numbers
	for p in parts:
		m = re.search(r"\b(202\d-\d{4})(?:/[A-Z])?\b", p)
		if m:
			result.setdefault("file_number", m.group(1))
	# Dates (likely filing date, not DoD)
	for p in parts[::-1]:
		if re.match(r"\d{4}-\d{2}-\d{2}$", p) or re.match(r"\d{4}/\d{2}/\d{2}$", p):
			result["entry_date"] = p
			break
	return result


def parse_fields(pages: List[PageResult], meta: Optional[Dict[str, str]] = None) -> ExtractedData:
	# Merge page text with selected metadata (helps when pages are image-only)
	meta_lines: List[str] = []
	if meta:
		for k, v in meta.items():
			if v:
				meta_lines.append(f"{k}: {v}")
	all_text = "\n".join(meta_lines + [p.text for p in pages])

	def search_first(pat: re.Pattern) -> Optional[str]:
		m = pat.search(all_text)
		if not m:
			return None
		# Return the first non-None group
		for g in m.groups() or []:
			if g:
				return g.strip()
		# If there are no groups, return the full match
		return m.group(0).strip() if m else None

	def _is_plausible_name(name: Optional[str]) -> bool:
		if not name:
			return False
		s = re.sub(r"\s+", " ", name).strip()
		if not s:
			return False
		bad_words = {
			"voluntary", "administration", "affidavit", "article", "will", "decedent", "estate", "county",
			"street", "address", "telephone", "tel", "phone", "fax", "email", "city", "state", "zip",
			"apt", "suite", "ste", "floor", "fl"
		}
		# Additional common noise terms on the contact block
		bad_words.update({"permanent", "mailing"})
		low = s.lower()
		# Avoid common locality names mistaken as person names
		if low in LOCALITY_BLOCKLIST:
			return False
		if any(w in low for w in bad_words):
			return False
		# 1 to 4 tokens, each fairly name-like
		toks = s.replace(",", " ").split()
		if len(toks) < 1 or len(toks) > 5:
			return False
		# Require at least 2 alphabetic characters in total
		if sum(ch.isalpha() for ch in s) < 3:
			return False
		# Avoid lines that look like addresses (contain a number followed by a street suffix)
		if re.search(r"\b\d{1,5}\b", s) and re.search(r"\b(St|St\.|Street|Ave|Avenue|Blvd|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Pl|Place)\b", s, re.I):
			return False
		return True

	def _looks_like_person_name(s: str) -> bool:
		s = s.strip()
		# Require at least two name-like tokens
		m = re.match(r"^\s*([A-Z][a-zA-Z'`\-]+|[A-Z]\.)\s+([A-Z][a-zA-Z'`\-]+|[A-Z]\.)(?:\s+(Jr\.|Sr\.|II|III|IV|[A-Z][a-zA-Z'`\-]+|[A-Z]\.)){0,2}\s*$", s)
		return bool(m)

	file_number = search_first(FILE_NO_PAT)
	decedent_name_candidates: List[str] = []
	for pat in (DECEDENT_PAT, DECEDENT_PAT2):
		m = pat.search(all_text)
		if m:
			decedent_name_candidates.append(m.group(1).strip())
	decedent_dod = search_first(DOD_PAT)
	county = search_first(COUNTY_PAT)
	docket = search_first(DOCKET_PAT)
	case_type = search_first(CASE_PAT)

	# First page specific parsing for phone and name
	first_page_phone: Optional[str] = None
	first_page_name: Optional[str] = None
	first_page_emails: List[str] = []
	first_page_email: Optional[str] = None
	first_page_address_lines: List[str] = []
	if pages:
		first_text = pages[0].text
		# Phone
		m_phone = PHONE_PAT.search(first_text)
		if m_phone:
			first_page_phone = m_phone.group(0).strip()

		# Emails (collect all on first page)
		first_page_emails = list(dict.fromkeys(EMAIL_PAT.findall(first_text)))  # preserve order and dedupe
		if first_page_emails:
			first_page_email = first_page_emails[0]

		# Name from labeled fields on first page
		m_label = NAME_LABEL_PAT.search(first_text) or NAME_LABEL_PAT2.search(first_text)
		if m_label:
			cand = m_label.group(1).strip()
			# Trim after typical separators and common labels
			cand = re.split(
				r"\s{2,}|\t|\s+-\s+|:|\(|\)|,\s*(?:Tel|Phone|Fax|Email)\b|\b(Street|Address|City|State|Zip|Apt|Suite|Ste|Fl|Floor|Optional)\b",
				cand,
				flags=re.I,
			)[0].strip()
			# Extract a strong name pattern from the candidate
			name_match = re.search(r"\b([A-Z][a-zA-Z'`\-]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-zA-Z'`\-]+){0,3})\b", cand)
			nm = name_match.group(1) if name_match else None
			if nm and _is_plausible_name(nm) and _looks_like_person_name(nm):
				first_page_name = name_match.group(1)
		# If still no name, try line near phone
		if not first_page_name and m_phone:
			lines = [ln.strip() for ln in first_text.splitlines()]
			# Find line index containing the phone match
			phone_span = m_phone.span()
			phone_line_idx = 0
			acc = 0
			for idx, ln in enumerate(lines):
				acc += len(ln) + 1  # +1 for newline
				if acc >= phone_span[0]:
					phone_line_idx = idx
					break
			# Look around the phone line to find a plausible name (up to 5 lines above)
			label_words = re.compile(r"(Name|Attorney|Affiant|Petitioner|Applicant|Street|Address|Telephone|Tel|Phone|Fax|Email|City|State|Zip|Apt|Suite|Ste|Fl|Floor)", re.I)
			# Score candidates in a window around the phone line
			stop_tokens = {"new", "york", "surrogate", "court", "queens", "bronx", "kings", "richmond", "nassau", "suffolk", "manhattan", "county", "state"}
			def score_candidate(s: str) -> int:
				if not _is_plausible_name(s):
					return -999
				toks = [t for t in re.split(r"\s+", s) if t]
				score = 0
				for t in toks:
					tl = t.strip(",.;:()[]{}").lower()
					if tl in stop_tokens:
						score -= 3
						continue
					if any(ch.isdigit() for ch in t):
						score -= 5
						continue
					if re.match(r"^[A-Z][a-z'`\-]+$", t):
						score += 3
					elif re.match(r"^[A-Z]\.$", t):
						score += 2
					elif re.match(r"^[A-Z]+$", t):
						score += 1
				# small penalty for long strings
				score -= max(0, len(" ".join(toks)) - 30) // 10
				return score

			best_name = None
			best_score = -999
			window_start = max(0, phone_line_idx - 6)
			window_end = min(len(lines), phone_line_idx + 3)
			for j in range(window_start, window_end):
				ln = lines[j]
				if label_words.search(ln):
					continue
				if ":" in ln or "(" in ln or ")" in ln:
					continue
				if re.search(r"\b\d{1,5}\b", ln) and re.search(r"\b(St|St\.|Street|Ave|Avenue|Blvd|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Pl|Place)\b", ln, re.I):
					continue
				# Extract head segment and test
				ln_head = re.split(r",|\s{2,}|\t|\s+-\s+", ln)[0].strip()
				m = re.search(r"\b([A-Z][a-zA-Z'`\-]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-zA-Z'`\-]+){0,3})\b", ln_head)
				cand = m.group(1) if m else ln_head
				# Drop locality-like strings
				if cand.strip().lower() in LOCALITY_BLOCKLIST:
					continue
				sc = score_candidate(cand)
				if _looks_like_person_name(cand) and sc > best_score:
					best_score = sc
					best_name = cand
			if best_name and best_score > 0:
				first_page_name = best_name

		# If still not found, try to use the Address label as an anchor and look above it
		if not first_page_name:
			lines = [ln.strip() for ln in first_text.splitlines()]
			addr_pat = re.compile(r"\b(Street\s+Address|Mailing\s+Address|Address)\b", re.I)
			label_words = re.compile(r"(Name|Attorney|Affiant|Petitioner|Applicant|Telephone|Tel|Phone|Fax|Email|City|State|Zip|Apt|Suite|Ste|Fl|Floor)", re.I)
			for idx, ln in enumerate(lines):
				if addr_pat.search(ln):
					for j in range(max(0, idx - 3), idx):
						candidate = re.split(r",|\s{2,}|\t|\s+-\s+|:|\(|\)", lines[j])[0].strip()
						if label_words.search(candidate):
							continue
						if _is_plausible_name(candidate) and _looks_like_person_name(candidate):
							first_page_name = candidate
							break
				if first_page_name:
					break

		# As a last text-based fallback, if we have a phone line, search a few lines below too
		if not first_page_name and m_phone:
			lines = [ln.strip() for ln in first_text.splitlines()]
			# locate phone line index again
			phone_span = m_phone.span()
			phone_line_idx = 0
			acc = 0
			for idx, ln in enumerate(lines):
				acc += len(ln) + 1
				if acc >= phone_span[0]:
					phone_line_idx = idx
					break

		# Address lines: try label anchor first, then street pattern near contact block
		lines_all = [ln.strip() for ln in first_text.splitlines()]
		addr_label = re.compile(r"\b(Street\s+Address|Mailing\s+Address|Address)\b", re.I)
		stop_labels = re.compile(r"\b(Name|Attorney|Affiant|Petitioner|Applicant|Telephone|Tel|Phone|Fax|Email|E-mail|City|State|Zip)\b", re.I)
		captured: List[str] = []
		# 1) Label-based capture
		for idx, ln in enumerate(lines_all):
			if addr_label.search(ln):
				# Remove the label prefix if value on same line
				tail = re.split(r"(?i)\b(?:Street\s+Address|Mailing\s+Address|Address)\b\s*[:\-]?\s*", ln)[-1].strip()
				if tail and (_is_street_line(tail) or _is_unit_line(tail) or _is_city_state_zip(tail)):
					captured.append(tail)
				# Then capture following lines while they look like address and not labels
				for j in range(idx + 1, min(len(lines_all), idx + 6)):
					nxt = lines_all[j]
					if not nxt or stop_labels.search(nxt):
						break
					if _is_street_line(nxt) or _is_unit_line(nxt) or _is_city_state_zip(nxt):
						captured.append(nxt)
					else:
						# Stop if we hit unrelated text
						if captured:
							break
				if captured:
					break
		# 2) If nothing captured, search for a street-looking line near the phone line
		if not captured:
			# Determine a window around phone line if available
			window = (0, len(lines_all))
			if m_phone:
				# Find phone line index
				phone_span = m_phone.span()
				acc = 0
				phone_idx = 0
				for ii, ln in enumerate(lines_all):
					acc += len(ln) + 1
					if acc >= phone_span[0]:
						phone_idx = ii
						break
				window = (max(0, phone_idx - 8), min(len(lines_all), phone_idx + 8))
			# Search for first street line within window
			street_idx = None
			for ii in range(window[0], window[1]):
				if _is_street_line(lines_all[ii]):
					street_idx = ii
					break
			if street_idx is not None:
				# Optionally include preceding unit line
				if street_idx - 1 >= 0 and _is_unit_line(lines_all[street_idx - 1]):
					captured.append(lines_all[street_idx - 1])
				captured.append(lines_all[street_idx])
				# Include up to two following lines if unit or city/state/zip
				for j in range(street_idx + 1, min(len(lines_all), street_idx + 4)):
					nxt = lines_all[j]
					if stop_labels.search(nxt):
						break
					if _is_unit_line(nxt) or _is_city_state_zip(nxt):
						captured.append(nxt)
					else:
						# Stop on unrelated text
						break
		# Clean and store captured address lines
		first_page_address_lines = [ln for ln in (l.strip(" ,;:-" ) for l in captured) if ln]

	# Additional fields
	additional: Dict[str, str] = {}
	if meta:
		for k in ("author", "title", "producer", "creationDate", "modDate", "keywords", "subject"):
			val = meta.get(k)
			if val:
				additional[k] = val

		# Use keywords to backfill fields when possible
		kw = meta.get("keywords")
		if kw:
			parsed_kw = _parse_keywords_meta(kw)
			if parsed_kw.get("decedent_name"):
				decedent_name_candidates.append(parsed_kw["decedent_name"])
			if not file_number and parsed_kw.get("file_number"):
				file_number = parsed_kw["file_number"]
			if parsed_kw.get("entry_date"):
				additional.setdefault("entry_date", parsed_kw["entry_date"])

	# Choose the best plausible decedent name
	decedent_name = None
	for cand in decedent_name_candidates:
		if _is_plausible_name(cand):
			decedent_name = cand
			break

	# Fallback: if still None and meta keywords contain a name, use it
	if not decedent_name and meta and meta.get("keywords"):
		kw_name = _parse_keywords_meta(meta["keywords"]).get("decedent_name")
		if _is_plausible_name(kw_name):
			decedent_name = kw_name

	# County fallback by scanning for known county tokens if not found or looks noisy
	if not county:
		# Prefer first page for county cues
		first_text = pages[0].text if pages else ""
		m = re.search(r"\b(Queens|Kings|Bronx|New York|Richmond|Nassau|Suffolk)\b", first_text, re.I)
		if m:
			val = m.group(1)
			# Normalize capitalization
			county = next((c for c in KNOWN_COUNTIES if c.lower() == val.lower()), val)

	return ExtractedData(
		file_number=file_number,
		decedent_name=decedent_name,
		decedent_dod=decedent_dod,
		county=county,
		docket=docket,
		case_type=case_type,
		first_page_phone=first_page_phone,
		first_page_name=first_page_name,
		first_page_email=first_page_email,
		first_page_emails=first_page_emails,
		first_page_address_lines=first_page_address_lines,
		additional=additional,
		pages=pages,
	)


def _normalize_phone_chars(s: str) -> str:
	"""Normalize common OCR confusions in phone numbers."""
	if not s:
		return s
	subs = {
		'O': '0', 'o': '0', 'Q': '0',
		'I': '1', 'l': '1', '¡': '1', '|': '1',
		'S': '5', 's': '5',
		'B': '8',
		'Z': '2',
		'—': '-', '–': '-', '−': '-', '—': '-',
	}
	out = ''.join(subs.get(ch, ch) for ch in s)
	# Remove weird spaces
	out = re.sub(r"\s+", " ", out)
	return out.strip()


def _format_us_phone(candidate: str) -> Optional[str]:
	"""Format a candidate phone string into US format if possible.
	Uses phonenumbers when available; otherwise, formats 10-digit strings.
	"""
	if not candidate:
		return None
	c = _normalize_phone_chars(candidate)
	digits = re.sub(r"\D", "", c)
	# If it starts with 1 and length 11, drop the 1
	if len(digits) == 11 and digits.startswith('1'):
		digits = digits[1:]
	if phonenumbers is not None:
		try:
			num = phonenumbers.parse(c, "US")
			if phonenumbers.is_possible_number(num) and phonenumbers.is_valid_number(num):
				return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.NATIONAL)
		except Exception:
			# Fallback to digits-only parsing
			try:
				num = phonenumbers.parse(digits, "US")
				if phonenumbers.is_possible_number(num) and phonenumbers.is_valid_number(num):
					return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.NATIONAL)
			except Exception:
				pass
	# Simple formatter for 10 digits
	if len(digits) == 10:
		return f"({digits[0:3]}) {digits[3:6]}-{digits[6:10]}"
	return None


def _crop(img: Any, box: tuple) -> Any:
	# Non-OCR build: not used
	return img


def _preprocess_variants(pil_img: Any) -> List[Any]:
	# Non-OCR build: not used
	return []


def _super_pass_handwritten_phone(pdf_path: str) -> Optional[str]:
    # Non-OCR build: not supported
    return None


def _get_pdf_metadata(pdf_path: str) -> Dict[str, str]:
	meta: Dict[str, str] = {}
	try:
		doc = fitz.open(pdf_path)
		try:
			raw = doc.metadata or {}
			# Normalize keys to lower snake-ish
			for k, v in raw.items():
				if not v:
					continue
				key = k.replace(" ", "").replace("-", "").lower()
				meta[key] = str(v)
			# Convenience aliases
			if "author" not in meta and raw.get("author"):
				meta["author"] = str(raw.get("author"))
			if "title" not in meta and raw.get("title"):
				meta["title"] = str(raw.get("title"))
		finally:
			doc.close()
	except Exception:
		pass
	return meta


def extract_pdf_data(pdf_path: str, force_ocr: bool = False, max_pages: Optional[int] = None) -> ExtractedData:
	# Azure-first-page-only extraction feeding the parser for dashboard fields
	# max_pages defaults to 1 to align with requirement
	if max_pages is None:
		max_pages = 1
	pages = extract_text_from_pdf(pdf_path, force_ocr=force_ocr, max_pages=max_pages)
	meta = _get_pdf_metadata(pdf_path)
	data = parse_fields(pages, meta=meta)
	return data


# ------------------------------
# CLI
# ------------------------------


def _main():
	parser = argparse.ArgumentParser(description="Extract key fields from NY probate PDFs (with OCR fallback)")
	# Default directory if nothing is provided
	DEFAULT_DIR = "/Users/matthewmakh/Downloads/ny-probate"
	group = parser.add_mutually_exclusive_group(required=False)
	group.add_argument("pdf", nargs="?", help="Path to a single PDF file")
	group.add_argument("--dir", dest="dir", help=f"Directory containing PDF files to process (default: {DEFAULT_DIR})")
	parser.add_argument("--force-ocr", action="store_true", help="Force OCR on every page")
	parser.add_argument("--max-pages", type=int, default=None, help="Only process the first N pages")
	parser.add_argument("--with-pages", action="store_true", help="Include per-page text in output")
	parser.add_argument("--out", help="Output file for aggregated results (JSONL). Default: print to stdout for single file.")
	parser.add_argument(
		"--csv-out",
		default=os.path.join("data", "probate_records.csv"),
		help="Path to CSV 'sheet' to upsert rows into (default: data/probate_records.csv)",
	)
	parser.add_argument(
		"--no-csv",
		action="store_true",
		help="Disable CSV writing (emit only JSON if --out or stdout)",
	)
	args = parser.parse_args()

	# ------------------------------
	# CSV helpers
	# ------------------------------

	# Keep CSV lean with only the fields requested for the dashboard
	CSV_COLUMNS = [
		"run_at",              # timestamp for record freshness
		"source_file",         # helpful for tracing
		"county",              # set from site selection when available
		"file_number",         # prefer site value
		"decedent_name",
		"decedent_dod",
		"first_page_phone",
		"first_page_address_lines",
		"first_page_email",    # single email column
	]

	def _ensure_dir_for(path: str) -> None:
		d = os.path.dirname(path)
		if d and not os.path.exists(d):
			os.makedirs(d, exist_ok=True)

	def _row_key(row: Dict[str, Any]) -> tuple:
		# Prefer file_number + docket; fall back to (file_number,) or (source_file,)
		fn = (row.get("file_number") or "").strip() or None
		dk = (row.get("docket") or "").strip() or None
		src = (row.get("source_file") or "").strip() or None
		if fn and dk:
			return ("fn+dk", fn, dk)
		if fn:
			return ("fn", fn)
		return ("src", src or "")

	def _write_csv_row(csv_path: str, row: Dict[str, Any]) -> None:
		_ensure_dir_for(csv_path)
		# Normalize columns and stringify lists
		row_norm: Dict[str, Any] = {k: row.get(k) for k in CSV_COLUMNS}
		# Join list-like fields
		if isinstance(row.get("first_page_address_lines"), list):
			row_norm["first_page_address_lines"] = " | ".join(row.get("first_page_address_lines") or [])
		# Upsert by key
		existing: List[Dict[str, str]] = []
		if os.path.exists(csv_path):
			with open(csv_path, "r", encoding="utf-8", newline="") as f:
				rdr = csv.DictReader(f)
				existing = [dict(r) for r in rdr]
			# Ensure columns superset
			if rdr.fieldnames:
				for col in CSV_COLUMNS:
					if col not in rdr.fieldnames:
						# Add missing column with empty values
						for r in existing:
							r[col] = ""

		key_new = _row_key(row_norm)
		replaced = False
		for i, r in enumerate(existing):
			if _row_key(r) == key_new:
				# Replace row
				existing[i] = {**r, **{k: ("" if row_norm.get(k) is None else str(row_norm.get(k))) for k in CSV_COLUMNS}}
				replaced = True
				break
		if not replaced:
			existing.append({k: ("" if row_norm.get(k) is None else str(row_norm.get(k))) for k in CSV_COLUMNS})

		# Write back
		with open(csv_path, "w", encoding="utf-8", newline="") as f:
			w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
			w.writeheader()
			for r in existing:
				w.writerow({k: ("" if r.get(k) is None else str(r.get(k))) for k in CSV_COLUMNS})

	# ------------------------------
	# Emitters
	# ------------------------------

	def emit(record: Dict[str, Any]):
		if not args.with_pages:
			record.pop("pages", None)
		if args.out:
			with open(args.out, "a", encoding="utf-8") as f:
				f.write(json.dumps(record) + "\n")
		else:
			print(json.dumps(record, indent=2))

	def emit_csv(record: Dict[str, Any]):
		if args.no_csv:
			return
		# Build a flat CSV row
		row: Dict[str, Any] = {}
		row["run_at"] = datetime.now(tz=timezone.utc).isoformat()
		row["source_file"] = record.get("source_file")
		row["county"] = record.get("county")
		row["file_number"] = record.get("file_number")
		row["decedent_name"] = record.get("decedent_name")
		row["decedent_dod"] = record.get("decedent_dod")
		# Normalize phone formatting
		phone = record.get("first_page_phone")
		row["first_page_phone"] = _format_us_phone(phone) if phone else None
		row["first_page_email"] = record.get("first_page_email")
		row["first_page_address_lines"] = record.get("first_page_address_lines")
		_write_csv_row(args.csv_out, row)

	# If no explicit file/dir provided, use the default directory
	if not args.pdf and not args.dir:
		args.dir = DEFAULT_DIR

	if args.pdf:
		data = extract_pdf_data(args.pdf, force_ocr=args.force_ocr, max_pages=args.max_pages)
		rec = asdict(data)
		rec["source_file"] = args.pdf
		emit(rec)
		emit_csv(rec)
		return

	# Directory mode
	if args.dir:
		if not os.path.exists(args.dir):
			raise FileNotFoundError(f"Directory not found: {args.dir}")
		if args.out:
			# Truncate existing file
			open(args.out, "w").close()
		# Ensure CSV exists with header
		if not args.no_csv:
			_ensure_dir_for(args.csv_out)
			if not os.path.exists(args.csv_out):
				with open(args.csv_out, "w", encoding="utf-8", newline="") as f:
					w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
					w.writeheader()
		for root, _, files in os.walk(args.dir):
			for fn in files:
				if not fn.lower().endswith(".pdf"):
					continue
				path = os.path.join(root, fn)
				try:
					data = extract_pdf_data(path, force_ocr=args.force_ocr, max_pages=args.max_pages)
					rec = asdict(data)
					rec["source_file"] = path
					emit(rec)
					emit_csv(rec)
				except Exception as e:
					err = {"source_file": path, "error": str(e)}
					emit(err)


if __name__ == "__main__":
	_main()

