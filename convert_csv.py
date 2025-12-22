#!/usr/bin/env python3
"""
Convert existing probate_records.csv to new format with simplified columns.
Creates a backup before converting.
"""

import csv
import os
import shutil
from datetime import datetime

# Paths
CSV_PATH = os.path.join("data", "probate_records.csv")
BACKUP_PATH = os.path.join("data", f"probate_records_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")

# New column structure
NEW_COLUMNS = [
    "County",
    "File number",
    "Date of death",
    "Decedent name",
    "Decedent address",
    "Executor/administrator name",
    "Executor/administrator phone",
    "Executor/administrator address",
    "Executor/administrator email",
]

def convert_csv():
    if not os.path.exists(CSV_PATH):
        print(f"Error: {CSV_PATH} not found")
        return
    
    # Create backup
    shutil.copy2(CSV_PATH, BACKUP_PATH)
    print(f"✓ Backup created: {BACKUP_PATH}")
    
    # Read old format
    old_rows = []
    with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        old_rows = list(reader)
    
    print(f"✓ Read {len(old_rows)} rows from old format")
    
    # Convert to new format
    new_rows = []
    for old_row in old_rows:
        # Combine address fields
        address = old_row.get("first_page_address_lines", "")
        
        new_row = {
            "County": old_row.get("county", ""),
            "File number": old_row.get("file_number", ""),
            "Date of death": old_row.get("decedent_dod", ""),
            "Decedent name": old_row.get("decedent_name", ""),
            "Decedent address": address,
            "Executor/administrator name": old_row.get("first_page_name", ""),
            "Executor/administrator phone": old_row.get("first_page_phone", ""),
            "Executor/administrator address": address,  # Using same address
            "Executor/administrator email": old_row.get("first_page_email", ""),
        }
        new_rows.append(new_row)
    
    # Write new format
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=NEW_COLUMNS)
        writer.writeheader()
        writer.writerows(new_rows)
    
    print(f"✓ Converted {len(new_rows)} rows to new format")
    print(f"✓ Updated: {CSV_PATH}")
    
    # Show sample
    print("\nSample of first row:")
    if new_rows:
        for col, val in new_rows[0].items():
            print(f"  {col}: {val}")

if __name__ == "__main__":
    convert_csv()
