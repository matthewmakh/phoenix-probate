#!/usr/bin/env python3
"""
Probate Records CRM - Flask Server (PostgreSQL Version)
Serves the dashboard and provides API for CRM functionality.
Designed for deployment on Railway with PostgreSQL.
"""

import os
import json
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from sqlalchemy.orm import Session
from sqlalchemy import text

from database import (
    get_database, 
    ProbateRecord, 
    CRMRecord, 
    Activity
)

app = Flask(__name__, static_folder='static')
CORS(app)

# Initialize database on startup
db = None


def get_db_session() -> Session:
    """Get a database session."""
    global db
    if db is None:
        db = get_database()
    return db.get_session()


def load_records():
    """Load all probate records merged with CRM data."""
    session = get_db_session()
    try:
        # Get all probate records
        probate_records = session.query(ProbateRecord).all()
        
        # Get all CRM records as a dict keyed by file_number
        crm_records = session.query(CRMRecord).all()
        crm_data = {crm.file_number: crm.to_dict() for crm in crm_records}
        
        records = []
        for pr in probate_records:
            record = {
                "id": pr.id,
                "county": pr.county or "",
                "file_number": pr.file_number or "",
                "date_of_death": pr.date_of_death or "",
                "decedent_name": pr.decedent_name or "",
                "decedent_address": pr.decedent_address or "",
                "executor_name": pr.executor_name or "",
                "executor_phone": pr.executor_phone or "",
                "executor_address": pr.executor_address or "",
                "executor_email": pr.executor_email or "",
                # CRM fields with defaults
                "status": "new",
                "follow_up_date": None,
                "assigned_to": None,
                "priority": "normal",
                "tags": [],
            }
            
            # Merge CRM data if exists
            if pr.file_number and pr.file_number in crm_data:
                crm = crm_data[pr.file_number]
                record["status"] = crm.get("status") or "new"
                record["follow_up_date"] = crm.get("follow_up_date")
                record["assigned_to"] = crm.get("assigned_to")
                record["priority"] = crm.get("priority") or "normal"
                record["tags"] = crm.get("tags") or []
            
            if record["decedent_name"] or record["executor_name"]:
                records.append(record)
        
        return records
    finally:
        session.close()


@app.route('/')
def index():
    """Serve the main dashboard page."""
    return send_from_directory('static', 'index.html')


@app.route('/api/records')
def get_records():
    """Return all records as JSON."""
    records = load_records()
    return jsonify(records)


@app.route('/api/records/<path:file_number>', methods=['GET'])
def get_record(file_number):
    """Get a single record with its activities."""
    records = load_records()
    record = next((r for r in records if r['file_number'] == file_number), None)
    
    if not record:
        return jsonify({"error": "Record not found"}), 404
    
    # Get activities
    session = get_db_session()
    try:
        activities = session.query(Activity)\
            .filter(Activity.file_number == file_number)\
            .order_by(Activity.created_at.desc())\
            .all()
        record['activities'] = [a.to_dict() for a in activities]
    finally:
        session.close()
    
    return jsonify(record)


@app.route('/api/records/<path:file_number>', methods=['PUT'])
def update_record(file_number):
    """Update CRM fields for a record."""
    data = request.json
    
    session = get_db_session()
    try:
        # Check if CRM record exists
        crm_record = session.query(CRMRecord)\
            .filter(CRMRecord.file_number == file_number)\
            .first()
        
        if crm_record:
            crm_record.status = data.get('status', 'new')
            crm_record.follow_up_date = data.get('follow_up_date')
            crm_record.assigned_to = data.get('assigned_to')
            crm_record.priority = data.get('priority', 'normal')
            crm_record.tags = data.get('tags', [])
            crm_record.updated_at = datetime.utcnow()
        else:
            crm_record = CRMRecord(
                file_number=file_number,
                status=data.get('status', 'new'),
                follow_up_date=data.get('follow_up_date'),
                assigned_to=data.get('assigned_to'),
                priority=data.get('priority', 'normal'),
                tags=data.get('tags', [])
            )
            session.add(crm_record)
        
        # Log status change as activity
        if 'status' in data:
            activity = Activity(
                file_number=file_number,
                activity_type='status_change',
                content=f"Status changed to: {data['status']}"
            )
            session.add(activity)
        
        session.commit()
    finally:
        session.close()
    
    return jsonify({"success": True})


@app.route('/api/records/<path:file_number>/activities', methods=['GET'])
def get_activities(file_number):
    """Get all activities for a record."""
    session = get_db_session()
    try:
        activities = session.query(Activity)\
            .filter(Activity.file_number == file_number)\
            .order_by(Activity.created_at.desc())\
            .all()
        return jsonify([a.to_dict() for a in activities])
    finally:
        session.close()


@app.route('/api/records/<path:file_number>/activities', methods=['POST'])
def add_activity(file_number):
    """Add a new activity/note to a record."""
    data = request.json
    
    session = get_db_session()
    try:
        activity = Activity(
            file_number=file_number,
            activity_type=data.get('activity_type', 'note'),
            content=data.get('content', '')
        )
        session.add(activity)
        session.commit()
        
        # Refresh to get the generated ID
        session.refresh(activity)
        result = activity.to_dict()
    finally:
        session.close()
    
    return jsonify(result)


@app.route('/api/stats')
def get_stats():
    """Return summary statistics."""
    records = load_records()
    
    counties = {}
    statuses = {}
    priorities = {}
    
    for r in records:
        county = r.get("county") or "Unknown"
        counties[county] = counties.get(county, 0) + 1
        
        status = r.get("status") or "new"
        statuses[status] = statuses.get(status, 0) + 1
        
        priority = r.get("priority") or "normal"
        priorities[priority] = priorities.get(priority, 0) + 1
    
    # Count follow-ups due today or overdue
    today = datetime.now().date().isoformat()
    follow_ups_due = sum(1 for r in records if r.get("follow_up_date") and r["follow_up_date"] <= today)
    
    return jsonify({
        "total": len(records),
        "counties": counties,
        "statuses": statuses,
        "priorities": priorities,
        "follow_ups_due": follow_ups_due
    })


@app.route('/api/health')
def health_check():
    """Health check endpoint for Railway."""
    try:
        session = get_db_session()
        session.execute(text("SELECT 1"))
        session.close()
        return jsonify({"status": "healthy", "database": "connected"})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


@app.route('/<path:path>')
def static_files(path):
    """Serve static files."""
    return send_from_directory('static', path)


if __name__ == '__main__':
    # Get port from environment (Railway sets this) or default to 5000
    port = int(os.environ.get('PORT', 5000))
    
    print("=" * 50)
    print("  Probate Records CRM (PostgreSQL)")
    print(f"  http://localhost:{port}")
    print("=" * 50)
    
    # In production (Railway), debug should be False
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    
    app.run(host='0.0.0.0', port=port, debug=debug)
