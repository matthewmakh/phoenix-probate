#!/usr/bin/env python3
"""
Database Client for Local Scraper
Connects to remote PostgreSQL database on Railway to insert scraped records.
"""

import os
from datetime import datetime
from typing import Optional, Dict, Any
from dotenv import load_dotenv

# Load .env file for local development
load_dotenv()

from database import get_database, ProbateRecord, CRMRecord
from sqlalchemy import text

# Cache for existing file numbers (like the CSV version)
_existing_file_numbers = None


def get_db_session():
    """Get a database session."""
    db = get_database()
    return db.get_session()


def load_existing_file_numbers() -> set:
    """
    Load all file numbers from the database into a set for fast lookup.
    Used to skip already-scraped cases.
    """
    global _existing_file_numbers
    if _existing_file_numbers is not None:
        return _existing_file_numbers
    
    _existing_file_numbers = set()
    
    try:
        session = get_db_session()
        file_numbers = session.query(ProbateRecord.file_number).all()
        _existing_file_numbers = {fn[0] for fn in file_numbers if fn[0]}
        session.close()
        print(f"[DB] Loaded {len(_existing_file_numbers)} existing file numbers from database")
    except Exception as e:
        print(f"[DB] Error loading file numbers: {e}")
    
    return _existing_file_numbers


def is_file_number_in_db(file_number: str) -> bool:
    """Check if a file number already exists in the database."""
    existing = load_existing_file_numbers()
    return file_number.strip() in existing


def add_file_number_to_cache(file_number: str):
    """Add a file number to the local cache after saving."""
    global _existing_file_numbers
    if _existing_file_numbers is None:
        _existing_file_numbers = set()
    _existing_file_numbers.add(file_number.strip())


def _normalize_value(val):
    """Normalize a value for database storage - handle None, numbers, 'null' strings."""
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        return str(int(val)) if isinstance(val, float) and val.is_integer() else str(val)
    val_str = str(val).strip()
    return "" if val_str.lower() == "null" else val_str


# Canonical relationship names mapping
RELATIONSHIP_MAPPING = {
    # Spouse variants
    'spouse': 'Spouse',
    'spause': 'Spouse',  # typo
    'wife': 'Spouse',
    'husband': 'Spouse',
    'surviving spouse': 'Spouse',
    # Children
    'son': 'Son',
    'daughter': 'Daughter',
    'child': 'Child',
    'children': 'Child',
    # Parents
    'mother': 'Mother',
    'father': 'Father',
    'parent': 'Parent',
    # Siblings
    'brother': 'Brother',
    'sister': 'Sister',
    'sibling': 'Sibling',
    # Extended family
    'grandchild': 'Grandchild',
    'grandson': 'Grandchild',
    'granddaughter': 'Grandchild',
    'niece': 'Niece/Nephew',
    'nephew': 'Niece/Nephew',
    'uncle': 'Uncle/Aunt',
    'aunt': 'Uncle/Aunt',
    'cousin': 'Cousin',
    # Legal terms
    'distributee': 'Distributee',
    'distributee of decedent': 'Distributee',
    'issue': 'Distributee',
}


def normalize_relationship(rel):
    """Normalize relationship to canonical form."""
    if not rel:
        return ""
    rel_clean = rel.strip().lower()
    
    # Check direct mapping
    if rel_clean in RELATIONSHIP_MAPPING:
        return RELATIONSHIP_MAPPING[rel_clean]
    
    # Check if any key is contained in the value (for compound values)
    for key, canonical in RELATIONSHIP_MAPPING.items():
        if key in rel_clean:
            return canonical
    
    # Title case if no mapping found
    return rel.strip().title()


def save_record_to_db(
    county_name: str,
    site_file_number: str,
    llm_data: Dict[str, Any]
) -> bool:
    """
    Save or update a probate record in the PostgreSQL database.
    
    Args:
        county_name: The county name (e.g., "Queens", "Bronx")
        site_file_number: The file number from the court website
        llm_data: Dictionary with extracted data from LLM/OCR
        
    Returns:
        True if successful, False otherwise
    """
    try:
        session = get_db_session()
        
        file_number = (site_file_number or llm_data.get("File number") or "").strip()
        
        if not file_number:
            print("[DB] Error: No file number provided")
            return False
        
        # Check if record exists
        existing = session.query(ProbateRecord)\
            .filter(ProbateRecord.file_number == file_number)\
            .first()
        
        if existing:
            # Update existing record
            existing.county = county_name or llm_data.get("County") or existing.county
            existing.date_of_death = llm_data.get("Date of death") or existing.date_of_death
            existing.decedent_name = llm_data.get("Decedent name") or existing.decedent_name
            existing.decedent_address = llm_data.get("Decedent address") or existing.decedent_address
            existing.executor_name = llm_data.get("Executor/administrator name") or existing.executor_name
            existing.executor_phone = llm_data.get("Executor/administrator phone") or existing.executor_phone
            existing.executor_address = llm_data.get("Executor/administrator address") or existing.executor_address
            existing.executor_email = llm_data.get("Executor/administrator email") or existing.executor_email
            # Normalize relationship and estate value
            rel_raw = _normalize_value(llm_data.get("Executor/administrator relationship"))
            rel_val = normalize_relationship(rel_raw) if rel_raw else ""
            est_val = _normalize_value(llm_data.get("Estimated estate value"))
            existing.executor_relationship = rel_val or existing.executor_relationship
            existing.estimated_estate_value = est_val or existing.estimated_estate_value
            existing.updated_at = datetime.utcnow()
            print(f"[DB] Updated record for {file_number}")
        else:
            # Create new record - normalize relationship
            rel_raw = _normalize_value(llm_data.get("Executor/administrator relationship"))
            record = ProbateRecord(
                county=county_name or llm_data.get("County") or "",
                file_number=file_number,
                date_of_death=llm_data.get("Date of death") or "",
                decedent_name=llm_data.get("Decedent name") or "",
                decedent_address=llm_data.get("Decedent address") or "",
                executor_name=llm_data.get("Executor/administrator name") or "",
                executor_phone=llm_data.get("Executor/administrator phone") or "",
                executor_address=llm_data.get("Executor/administrator address") or "",
                executor_email=llm_data.get("Executor/administrator email") or "",
                executor_relationship=normalize_relationship(rel_raw) if rel_raw else "",
                estimated_estate_value=_normalize_value(llm_data.get("Estimated estate value")),
            )
            session.add(record)
            print(f"[DB] Created new record for {file_number}")
        
        session.commit()
        session.close()
        
        # Add to cache
        add_file_number_to_cache(file_number)
        
        return True
        
    except Exception as e:
        print(f"[DB] Error saving record: {e}")
        return False


def get_record_count() -> int:
    """Get total number of records in the database."""
    try:
        session = get_db_session()
        count = session.query(ProbateRecord).count()
        session.close()
        return count
    except Exception as e:
        print(f"[DB] Error getting record count: {e}")
        return 0


def test_connection() -> bool:
    """Test the database connection."""
    try:
        session = get_db_session()
        session.execute(text("SELECT 1"))
        session.close()
        print("[DB] Connection successful!")
        return True
    except Exception as e:
        print(f"[DB] Connection failed: {e}")
        return False


if __name__ == "__main__":
    # Test the database connection
    print("Testing database connection...")
    if test_connection():
        count = get_record_count()
        print(f"Total records in database: {count}")
    else:
        print("Failed to connect to database. Check your DATABASE_URL environment variable.")
