#!/usr/bin/env python3
"""
PostgreSQL Database Models and Connection
For use with Railway PostgreSQL database.
"""

import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, JSON, Index, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session

Base = declarative_base()


class ProbateRecord(Base):
    """Main probate records table - stores the scraped data."""
    __tablename__ = 'probate_records'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    county = Column(String(100), index=True)
    file_number = Column(String(50), unique=True, nullable=False, index=True)
    date_of_death = Column(String(50))
    decedent_name = Column(String(255), index=True)
    decedent_address = Column(Text)
    executor_name = Column(String(255), index=True)
    executor_phone = Column(String(50))
    executor_address = Column(Text)
    executor_email = Column(String(255))
    executor_relationship = Column(String(100))  # e.g., "son", "spouse", "daughter"
    estimated_estate_value = Column(String(50))  # e.g., "$50,000" or "50000"
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Indexes for common queries
    __table_args__ = (
        Index('idx_county_date', 'county', 'date_of_death'),
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'county': self.county or '',
            'file_number': self.file_number or '',
            'date_of_death': self.date_of_death or '',
            'decedent_name': self.decedent_name or '',
            'decedent_address': self.decedent_address or '',
            'executor_name': self.executor_name or '',
            'executor_phone': self.executor_phone or '',
            'executor_address': self.executor_address or '',
            'executor_email': self.executor_email or '',
            'executor_relationship': self.executor_relationship or '',
            'estimated_estate_value': self.estimated_estate_value or '',
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class CRMRecord(Base):
    """CRM status and metadata for each probate record."""
    __tablename__ = 'crm_records'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_number = Column(String(50), unique=True, nullable=False, index=True)
    status = Column(String(50), default='new', index=True)
    follow_up_date = Column(String(50), index=True)
    assigned_to = Column(String(100), index=True)
    priority = Column(String(20), default='normal', index=True)
    tags = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'file_number': self.file_number,
            'status': self.status or 'new',
            'follow_up_date': self.follow_up_date,
            'assigned_to': self.assigned_to,
            'priority': self.priority or 'normal',
            'tags': self.tags or [],
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Activity(Base):
    """Activity log / notes for each record."""
    __tablename__ = 'activities'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_number = Column(String(50), nullable=False, index=True)
    activity_type = Column(String(50), nullable=False)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'file_number': self.file_number,
            'activity_type': self.activity_type,
            'content': self.content,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Database:
    """Database connection manager."""
    
    def __init__(self, database_url=None):
        """
        Initialize database connection.
        
        Args:
            database_url: PostgreSQL connection string. 
                          If not provided, uses DATABASE_URL env var.
        """
        self.database_url = database_url or os.environ.get('DATABASE_URL')
        
        if not self.database_url:
            raise ValueError(
                "DATABASE_URL environment variable is required. "
                "Set it to your PostgreSQL connection string."
            )
        
        # Railway uses postgres:// but SQLAlchemy needs postgresql://
        if self.database_url.startswith('postgres://'):
            self.database_url = self.database_url.replace('postgres://', 'postgresql://', 1)
        
        self.engine = create_engine(
            self.database_url,
            pool_pre_ping=True,  # Reconnect on stale connections
            pool_size=5,
            max_overflow=10
        )
        
        self.Session = scoped_session(sessionmaker(bind=self.engine))
    
    def create_tables(self):
        """Create all tables if they don't exist."""
        Base.metadata.create_all(self.engine)
    
    def get_session(self):
        """Get a new database session."""
        return self.Session()
    
    def close_session(self):
        """Close the current session."""
        self.Session.remove()


# Singleton instance for the app
_db_instance = None


def get_database(database_url=None):
    """Get or create the database singleton."""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(database_url)
        _db_instance.create_tables()
    return _db_instance


def init_database(database_url=None):
    """Initialize the database and create tables."""
    db = get_database(database_url)
    db.create_tables()
    return db
