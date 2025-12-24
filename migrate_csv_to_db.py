#!/usr/bin/env python3
"""
Migration Script: CSV to PostgreSQL
Migrates existing probate records from CSV to PostgreSQL database.
"""

import os
import csv
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from database import get_database, ProbateRecord, CRMRecord

CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "probate_records.csv")


def migrate_csv_to_db():
    """Migrate all records from CSV to PostgreSQL."""
    
    if not os.path.exists(CSV_PATH):
        print(f"CSV file not found: {CSV_PATH}")
        return
    
    # Initialize database
    db = get_database()
    session = db.get_session()
    
    migrated = 0
    skipped = 0
    errors = 0
    
    try:
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                file_number = (row.get("File number") or "").strip()
                
                if not file_number:
                    print(f"Skipping row with no file number: {row.get('Decedent name')}")
                    skipped += 1
                    continue
                
                # Check if already exists
                existing = session.query(ProbateRecord)\
                    .filter(ProbateRecord.file_number == file_number)\
                    .first()
                
                if existing:
                    print(f"Skipping {file_number} - already exists")
                    skipped += 1
                    continue
                
                try:
                    record = ProbateRecord(
                        county=(row.get("County") or "").strip(),
                        file_number=file_number,
                        date_of_death=(row.get("Date of death") or "").strip(),
                        decedent_name=(row.get("Decedent name") or "").strip(),
                        decedent_address=(row.get("Decedent address") or "").strip(),
                        executor_name=(row.get("Executor/administrator name") or "").strip(),
                        executor_phone=(row.get("Executor/administrator phone") or "").strip(),
                        executor_address=(row.get("Executor/administrator address") or "").strip(),
                        executor_email=(row.get("Executor/administrator email") or "").strip(),
                    )
                    session.add(record)
                    session.commit()
                    migrated += 1
                    print(f"✓ Migrated: {file_number}")
                    
                except Exception as e:
                    session.rollback()
                    print(f"✗ Error migrating {file_number}: {e}")
                    errors += 1
        
        print("\n" + "=" * 50)
        print(f"Migration complete!")
        print(f"  Migrated: {migrated}")
        print(f"  Skipped:  {skipped}")
        print(f"  Errors:   {errors}")
        print("=" * 50)
        
    finally:
        session.close()


def verify_migration():
    """Verify the migration by comparing counts."""
    
    # Count CSV records
    csv_count = 0
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            csv_count = sum(1 for row in reader if row.get("File number"))
    
    # Count DB records
    db = get_database()
    session = db.get_session()
    db_count = session.query(ProbateRecord).count()
    session.close()
    
    print("\nVerification:")
    print(f"  CSV records:      {csv_count}")
    print(f"  Database records: {db_count}")
    
    if csv_count == db_count:
        print("  ✓ Counts match!")
    else:
        print(f"  ⚠ Difference: {abs(csv_count - db_count)}")


if __name__ == "__main__":
    print("=" * 50)
    print("  CSV to PostgreSQL Migration")
    print("=" * 50)
    print()
    
    # Check for DATABASE_URL
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL environment variable not set!")
        print("Set it in your .env file or environment.")
        exit(1)
    
    print(f"Source: {CSV_PATH}")
    print(f"Target: PostgreSQL database")
    print()
    
    response = input("Continue with migration? (y/n): ")
    if response.lower() == 'y':
        migrate_csv_to_db()
        verify_migration()
    else:
        print("Migration cancelled.")
