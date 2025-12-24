#!/usr/bin/env python3
"""
Probate Records CRM - Flask Server
Serves the dashboard and provides API for CRM functionality.
"""

import os
import csv
import json
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static')
CORS(app)

CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "probate_records.csv")
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "crm.db")


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the CRM database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    cursor = conn.cursor()
    
    # CRM status and notes for each record (keyed by file_number)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS crm_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_number TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'new',
            follow_up_date TEXT,
            assigned_to TEXT,
            priority TEXT DEFAULT 'normal',
            tags TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Activity/notes log
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_number TEXT NOT NULL,
            activity_type TEXT NOT NULL,
            content TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()


def load_records():
    """Load records from CSV file and merge with CRM data."""
    records = []
    if not os.path.exists(CSV_PATH):
        return records
    
    # Load CRM data
    crm_data = {}
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM crm_records')
        for row in cursor.fetchall():
            crm_data[row['file_number']] = dict(row)
        conn.close()
    except Exception as e:
        print(f"Error loading CRM data: {e}")
    
    try:
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                file_number = (row.get("File number") or "").strip()
                record = {
                    "county": (row.get("County") or "").strip(),
                    "file_number": file_number,
                    "date_of_death": (row.get("Date of death") or "").strip(),
                    "decedent_name": (row.get("Decedent name") or "").strip(),
                    "decedent_address": (row.get("Decedent address") or "").strip(),
                    "executor_name": (row.get("Executor/administrator name") or "").strip(),
                    "executor_phone": (row.get("Executor/administrator phone") or "").strip(),
                    "executor_address": (row.get("Executor/administrator address") or "").strip(),
                    "executor_email": (row.get("Executor/administrator email") or "").strip(),
                    # CRM fields
                    "status": "new",
                    "follow_up_date": None,
                    "assigned_to": None,
                    "priority": "normal",
                    "tags": [],
                }
                
                # Merge CRM data if exists
                if file_number and file_number in crm_data:
                    crm = crm_data[file_number]
                    record["status"] = crm.get("status") or "new"
                    record["follow_up_date"] = crm.get("follow_up_date")
                    record["assigned_to"] = crm.get("assigned_to")
                    record["priority"] = crm.get("priority") or "normal"
                    record["tags"] = json.loads(crm.get("tags") or "[]")
                
                if record["decedent_name"] or record["executor_name"]:
                    records.append(record)
    except Exception as e:
        print(f"Error loading CSV: {e}")
    
    return records


# Initialize database on startup
init_db()


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
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM activities WHERE file_number = ? ORDER BY created_at DESC',
        (file_number,)
    )
    activities = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    record['activities'] = activities
    return jsonify(record)


@app.route('/api/records/<path:file_number>', methods=['PUT'])
def update_record(file_number):
    """Update CRM fields for a record."""
    data = request.json
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if record exists
    cursor.execute('SELECT id FROM crm_records WHERE file_number = ?', (file_number,))
    exists = cursor.fetchone()
    
    if exists:
        cursor.execute('''
            UPDATE crm_records 
            SET status = ?, follow_up_date = ?, assigned_to = ?, priority = ?, tags = ?, updated_at = ?
            WHERE file_number = ?
        ''', (
            data.get('status', 'new'),
            data.get('follow_up_date'),
            data.get('assigned_to'),
            data.get('priority', 'normal'),
            json.dumps(data.get('tags', [])),
            datetime.now().isoformat(),
            file_number
        ))
    else:
        cursor.execute('''
            INSERT INTO crm_records (file_number, status, follow_up_date, assigned_to, priority, tags)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            file_number,
            data.get('status', 'new'),
            data.get('follow_up_date'),
            data.get('assigned_to'),
            data.get('priority', 'normal'),
            json.dumps(data.get('tags', []))
        ))
    
    # Log status change as activity
    if 'status' in data:
        cursor.execute('''
            INSERT INTO activities (file_number, activity_type, content)
            VALUES (?, ?, ?)
        ''', (file_number, 'status_change', f"Status changed to: {data['status']}"))
    
    conn.commit()
    conn.close()
    
    return jsonify({"success": True})


@app.route('/api/records/<path:file_number>/activities', methods=['GET'])
def get_activities(file_number):
    """Get all activities for a record."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM activities WHERE file_number = ? ORDER BY created_at DESC',
        (file_number,)
    )
    activities = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(activities)


@app.route('/api/records/<path:file_number>/activities', methods=['POST'])
def add_activity(file_number):
    """Add a new activity/note to a record."""
    data = request.json
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO activities (file_number, activity_type, content)
        VALUES (?, ?, ?)
    ''', (
        file_number,
        data.get('activity_type', 'note'),
        data.get('content', '')
    ))
    
    activity_id = cursor.lastrowid
    conn.commit()
    
    # Fetch the created activity
    cursor.execute('SELECT * FROM activities WHERE id = ?', (activity_id,))
    activity = dict(cursor.fetchone())
    conn.close()
    
    return jsonify(activity)


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


@app.route('/<path:path>')
def static_files(path):
    """Serve static files."""
    return send_from_directory('static', path)


if __name__ == '__main__':
    print("=" * 50)
    print("  Probate Records CRM")
    print("  http://localhost:5000")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=True)
