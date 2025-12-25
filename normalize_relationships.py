#!/usr/bin/env python3
"""Normalize existing relationship values in the database."""
from dotenv import load_dotenv
load_dotenv()

from database import get_database, ProbateRecord
from db_client import normalize_relationship

def main():
    db = get_database()
    session = db.Session()
    
    # Get all records with relationships
    records = session.query(ProbateRecord).filter(
        ProbateRecord.executor_relationship.isnot(None),
        ProbateRecord.executor_relationship != ''
    ).all()
    
    print(f"Found {len(records)} records with relationships")
    
    updated = 0
    changes = {}
    
    for r in records:
        old_val = r.executor_relationship
        new_val = normalize_relationship(old_val)
        
        if old_val != new_val:
            r.executor_relationship = new_val
            updated += 1
            
            # Track changes for summary
            key = f"{old_val} -> {new_val}"
            changes[key] = changes.get(key, 0) + 1
    
    if updated > 0:
        session.commit()
        print(f"\n✅ Updated {updated} records")
        print("\nChanges made:")
        for change, count in sorted(changes.items(), key=lambda x: -x[1]):
            print(f"  {change}: {count}")
    else:
        print("\n✓ All relationships already normalized")
    
    session.close()

if __name__ == "__main__":
    main()
