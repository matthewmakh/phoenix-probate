#!/usr/bin/env python3
"""
FAST Backfill - executor_relationship and estimated_estate_value for existing records.

Uses parallel processing for maximum speed.

Usage:
    python backfill_new_fields_fast.py
    python backfill_new_fields_fast.py --limit 50
    python backfill_new_fields_fast.py --workers 8
"""

import os
import sys
import argparse
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from dotenv import load_dotenv
load_dotenv()

from database import get_database, ProbateRecord
from llm_extractor import extract_file

OCR_TEXT_DIR = Path(__file__).parent / "ocr_text"

# Thread-safe counter
class Counter:
    def __init__(self):
        self.value = 0
        self.lock = threading.Lock()
    
    def increment(self):
        with self.lock:
            self.value += 1
            return self.value


def find_ocr_file(file_number: str) -> Optional[Path]:
    """Find the OCR text file for a given file number."""
    clean_fn = file_number.replace("/", "-").replace("\\", "-").replace(".", "-").strip()
    base_fn = clean_fn.split("-A")[0] if "-A" in clean_fn else clean_fn
    
    patterns = [
        f"{clean_fn}_*.txt",
        f"{clean_fn}-A_*.txt",
        f"{base_fn}_*.txt",
        f"{base_fn}-A_*.txt",
    ]
    
    for pattern in patterns:
        matches = list(OCR_TEXT_DIR.glob(pattern))
        if matches:
            return matches[0]
    
    m = re.search(r'(\d{4})-?(\d{3,4})', clean_fn)
    if m:
        year_num = f"{m.group(1)}-{m.group(2)}"
        fuzzy_matches = list(OCR_TEXT_DIR.glob(f"{year_num}*.txt"))
        if fuzzy_matches:
            return fuzzy_matches[0]
    
    return None


def process_single_record(record_data: Tuple[int, str, str]) -> Dict[str, Any]:
    """Process a single record - runs in thread pool."""
    record_id, file_number, decedent_name = record_data
    
    ocr_path = find_ocr_file(file_number)
    if not ocr_path:
        return {
            "id": record_id,
            "file_number": file_number,
            "status": "no_ocr",
            "relationship": None,
            "estate_value": None,
        }
    
    try:
        # LLM call - timeout is now built into the OpenAI client (30 sec)
        data = extract_file(str(ocr_path))
        
        return {
            "id": record_id,
            "file_number": file_number,
            "status": "success",
            "relationship": data.get("Executor/administrator relationship") or "",
            "estate_value": data.get("Estimated estate value") or "",
        }
    except Exception as e:
        return {
            "id": record_id,
            "file_number": file_number,
            "status": "error",
            "error": str(e),
            "relationship": None,
            "estate_value": None,
        }


def main():
    parser = argparse.ArgumentParser(description="Fast parallel backfill")
    parser.add_argument("--limit", type=int, help="Max records to process")
    parser.add_argument("--workers", type=int, default=5, help="Number of parallel workers (default: 5)")
    parser.add_argument("--all", action="store_true", help="Process ALL records, not just missing ones")
    args = parser.parse_args()
    
    print("🚀 FAST Backfill - Parallel Processing")
    print(f"   Workers: {args.workers}")
    print(f"   OCR dir: {OCR_TEXT_DIR}")
    print()
    
    db = get_database()
    session = db.get_session()
    
    try:
        # Get records needing backfill (skip ones already filled)
        query = session.query(ProbateRecord.id, ProbateRecord.file_number, ProbateRecord.decedent_name)
        
        if not args.all:
            # Only process records where BOTH fields are empty/null
            query = query.filter(
                ((ProbateRecord.executor_relationship == None) | (ProbateRecord.executor_relationship == '')) &
                ((ProbateRecord.estimated_estate_value == None) | (ProbateRecord.estimated_estate_value == ''))
            )
        
        records = query.all()
        total = len(records)
        
        if args.limit:
            records = records[:args.limit]
        
        print(f"📊 Found {total} records, processing {len(records)}")
        print()
        
        # Convert to tuples for thread safety
        record_tuples = [(r.id, r.file_number, r.decedent_name) for r in records]
        
        results = []
        success_count = Counter()
        pending_updates = []  # Buffer for batch commits
        
        print("⏳ Processing with LLM extraction...")
        
        # Process in parallel
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(process_single_record, r): r for r in record_tuples}
            
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                
                count = success_count.increment()
                status_icon = "✅" if result["status"] == "success" else "⚠️" if result["status"] == "no_ocr" else "❌"
                
                rel_val = result.get("relationship", "") or ""
                est_val = result.get("estate_value", "") or ""
                rel_display = str(rel_val)[:30] if rel_val else "-"
                val_display = str(est_val)[:15] if est_val else "-"
                
                print(f"[{count}/{len(records)}] {status_icon} {result['file_number']}: rel={rel_display}, val={val_display}")
                
                # Batch commit every 10 successful records
                if result["status"] == "success":
                    pending_updates.append(result)
                    if len(pending_updates) >= 10:
                        for r in pending_updates:
                            record = session.query(ProbateRecord).filter(ProbateRecord.id == r["id"]).first()
                            if record:
                                record.executor_relationship = r["relationship"]
                                record.estimated_estate_value = r["estate_value"]
                                record.updated_at = datetime.now(timezone.utc)
                        session.commit()
                        print(f"   💾 Saved {len(pending_updates)} records to DB")
                        pending_updates = []
        
        # Commit any remaining updates
        if pending_updates:
            print()
            print("💾 Saving remaining records...")
            for r in pending_updates:
                record = session.query(ProbateRecord).filter(ProbateRecord.id == r["id"]).first()
                if record:
                    record.executor_relationship = r["relationship"]
                    record.estimated_estate_value = r["estate_value"]
                    record.updated_at = datetime.now(timezone.utc)
            session.commit()
            print(f"   💾 Saved {len(pending_updates)} records to DB")
        
        # Summary
        print()
        print("=" * 50)
        success = len([r for r in results if r["status"] == "success"])
        no_ocr = len([r for r in results if r["status"] == "no_ocr"])
        errors = len([r for r in results if r["status"] == "error"])
        
        print(f"✅ Complete!")
        print(f"   Success:  {success}")
        print(f"   No OCR:   {no_ocr}")
        print(f"   Errors:   {errors}")
        
    finally:
        session.close()


if __name__ == "__main__":
    main()
