#!/usr/bin/env python3
"""
Migration script to add executor_relationship and estimated_estate_value columns.
Run this once to update your existing Railway PostgreSQL database.

Usage:
    python migrate_add_new_fields.py
    
Requires DATABASE_URL environment variable to be set.
"""

import os
from dotenv import load_dotenv
load_dotenv()

from database import get_database
from sqlalchemy import text


def run_migration():
    """Add new columns to probate_records table."""
    print("Starting migration...")
    
    db = get_database()
    session = db.get_session()
    
    try:
        # Add executor_relationship column
        print("Adding executor_relationship column...")
        session.execute(text(
            "ALTER TABLE probate_records ADD COLUMN IF NOT EXISTS executor_relationship VARCHAR(100)"
        ))
        
        # Add estimated_estate_value column
        print("Adding estimated_estate_value column...")
        session.execute(text(
            "ALTER TABLE probate_records ADD COLUMN IF NOT EXISTS estimated_estate_value VARCHAR(50)"
        ))
        
        session.commit()
        print("✅ Migration complete! New columns added:")
        print("   - executor_relationship (VARCHAR 100)")
        print("   - estimated_estate_value (VARCHAR 50)")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    run_migration()
