#!/usr/bin/env python3
"""
Backfill executor_relationship and estimated_estate_value for existing records.

This script:
1. Gets all records from the database that are missing relationship/estate value
2. Finds matching OCR text files
3. Re-runs LLM extraction to get the new fields
4. Updates the database records

Usage:
    python backfill_new_fields.py
    python backfill_new_fields.py --limit 10  # Process only 10 records
    python backfill_new_fields.py --dry-run   # Don't update DB, just show what would happen
"""

import os
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

from dotenv import load_dotenv
load_dotenv()

from database import get_database, ProbateRecord
from llm_extractor import extract_file


OCR_TEXT_DIR = Path(__file__).parent / "ocr_text"


def find_ocr_file(file_number: str) -> Optional[Path]:
    """Find the OCR text file for a given file number."""
    # Clean file number for filename matching
    clean_fn = file_number.replace("/", "-").replace("\\", "-").replace(".", "-").strip()
    
    # Also try without the -A suffix variations
    base_fn = clean_fn.split("-A")[0] if "-A" in clean_fn else clean_fn
    
    # Try different patterns
    patterns = [
        f"{clean_fn}_*.txt",
        f"{clean_fn}.txt",
        f"{clean_fn}-A_*.txt",
        f"{base_fn}_*.txt",
        f"{base_fn}-A_*.txt",
        f"{base_fn}-A*.txt",
    ]
    
    for pattern in patterns:
        matches = list(OCR_TEXT_DIR.glob(pattern))
        if matches:
            return matches[0]
    
    # Try a more aggressive search - just the year-number part
    import re
    m = re.search(r'(\d{4})-?(\d{3,4})', clean_fn)
    if m:
        year_num = f"{m.group(1)}-{m.group(2)}"
        fuzzy_matches = list(OCR_TEXT_DIR.glob(f"{year_num}*.txt"))
        if fuzzy_matches:
            return fuzzy_matches[0]
    
    return None


def needs_backfill(record: ProbateRecord) -> bool:
    """Check if a record needs backfilling."""
    # Backfill if relationship is missing/empty
    rel = getattr(record, 'executor_relationship', None)
    val = getattr(record, 'estimated_estate_value', None)
    
    return (not rel or rel.strip() == '') or (not val or val.strip() == '')


def backfill_record(session, record: ProbateRecord, dry_run: bool = False) -> bool:
    """Backfill a single record with new fields from OCR."""
    file_number = record.file_number
    
    # Find OCR file
    ocr_path = find_ocr_file(file_number)
    if not ocr_path:
        print(f"  ⚠️  No OCR file found for {file_number}")
        return False
    
    print(f"  📄 Found OCR: {ocr_path.name}")
    
    try:
        # Re-run LLM extraction
        data = extract_file(str(ocr_path))
        
        relationship = data.get("Executor/administrator relationship") or ""
        estate_value = data.get("Estimated estate value") or ""
        
        print(f"     Relationship: {relationship or '(none found)'}")
        print(f"     Estate Value: {estate_value or '(none found)'}")
        
        if dry_run:
            print(f"     [DRY RUN] Would update record")
            return True
        
        # Update record
        record.executor_relationship = relationship
        record.estimated_estate_value = estate_value
        record.updated_at = datetime.utcnow()
        
        session.commit()
        print(f"     ✅ Updated!")
        return True
        
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Backfill relationship and estate value fields")
    parser.add_argument("--limit", type=int, help="Max records to process")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually update DB")
    parser.add_argument("--all", action="store_true", help="Process ALL records, not just missing ones")
    args = parser.parse_args()
    
    print("🔄 Starting backfill for executor_relationship and estimated_estate_value...")
    print(f"   OCR directory: {OCR_TEXT_DIR}")
    print()
    
    db = get_database()
    session = db.get_session()
    
    try:
        # Get records
        query = session.query(ProbateRecord)
        
        if not args.all:
            # Only get records that need backfilling
            query = query.filter(
                (ProbateRecord.executor_relationship == None) | 
                (ProbateRecord.executor_relationship == '') |
                (ProbateRecord.estimated_estate_value == None) |
                (ProbateRecord.estimated_estate_value == '')
            )
        
        records = query.all()
        total = len(records)
        
        if args.limit:
            records = records[:args.limit]
        
        print(f"📊 Found {total} records needing backfill")
        if args.limit:
            print(f"   Processing first {args.limit}")
        print()
        
        success = 0
        failed = 0
        skipped = 0
        
        for i, record in enumerate(records, 1):
            print(f"[{i}/{len(records)}] {record.file_number} - {record.decedent_name}")
            
            result = backfill_record(session, record, dry_run=args.dry_run)
            
            if result:
                success += 1
            else:
                failed += 1
            
            print()
        
        print("=" * 50)
        print(f"✅ Complete!")
        print(f"   Success: {success}")
        print(f"   Failed:  {failed}")
        print(f"   Skipped: {skipped}")
        
    finally:
        session.close()


if __name__ == "__main__":
    main()
