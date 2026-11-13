"""
AcciAlert - SQLite Database Module
Manages persistence for events, danger zones, drivers, and command audit logs.
Author: Muniyappan
"""

import sqlite3
import csv
import io
from datetime import datetime
from config import DATABASE_PATH, INITIAL_DANGER_ZONES, INITIAL_DRIVERS


def get_db_connection():
    """Create and return a configured SQLite connection."""
    conn = sqlite3.connect(DATABASE_PATH, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys and WAL mode for better concurrency
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Initialize database tables and seed initial records if empty."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Events Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            driver_name TEXT,
            vehicle_speed REAL,
            latitude REAL,
            longitude REAL,
            danger_zone_name TEXT,
            details TEXT
        )
    """)

    # Danger Zones Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS danger_zones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            name TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            radius_meters REAL NOT NULL,
            speed_limit REAL NOT NULL,
            severity TEXT NOT NULL DEFAULT 'MEDIUM',
            details TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Drivers Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            license_number TEXT UNIQUE,
            authorized INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Commands Audit Log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS commands_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT NOT NULL,
            source TEXT DEFAULT 'UI',
            status TEXT DEFAULT 'SENT',
            timestamp TEXT NOT NULL
        )
    """)

    conn.commit()

    # Seed Danger Zones if none exist
    cursor.execute("SELECT COUNT(*) as count FROM danger_zones")
    if cursor.fetchone()["count"] == 0:
        now = datetime.now().isoformat()
        for zone in INITIAL_DANGER_ZONES:
            cursor.execute("""
                INSERT INTO danger_zones (code, name, latitude, longitude, radius_meters, speed_limit, severity, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                zone["code"],
                zone["name"],
                zone["latitude"],
                zone["longitude"],
                zone["radius_meters"],
                zone["speed_limit"],
                zone["severity"],
                zone.get("details", ""),
                now
            ))
        conn.commit()

    # Seed Drivers if none exist
    cursor.execute("SELECT COUNT(*) as count FROM drivers")
    if cursor.fetchone()["count"] == 0:
        now = datetime.now().isoformat()
        for driver in INITIAL_DRIVERS:
            cursor.execute("""
                INSERT INTO drivers (name, license_number, authorized, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                driver["name"],
                driver["license_number"],
                driver["authorized"],
                driver.get("notes", ""),
                now
            ))
        conn.commit()

    conn.close()


# ==========================================
# EVENT OPERATIONS
# ==========================================

def add_event(event_type: str, driver_name: str, vehicle_speed: float,
              latitude: float, longitude: float, danger_zone_name: str = None,
              details: str = None, timestamp: str = None) -> int:
    """Insert a new safety or system event."""
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO events (event_type, timestamp, driver_name, vehicle_speed, latitude, longitude, danger_zone_name, details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_type,
        timestamp,
        driver_name,
        round(float(vehicle_speed), 1) if vehicle_speed is not None else 0.0,
        round(float(latitude), 6) if latitude is not None else 0.0,
        round(float(longitude), 6) if longitude is not None else 0.0,
        danger_zone_name or "None",
        details or ""
    ))
    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return event_id


def get_events(limit: int = 100, offset: int = 0, event_type: str = None,
               start_date: str = None, end_date: str = None, search: str = None):
    """Retrieve filtered events list and total count."""
    conn = get_db_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM events WHERE 1=1"
    params = []

    if event_type and event_type.upper() != "ALL":
        query += " AND UPPER(event_type) = UPPER(?)"
        params.append(event_type)

    if start_date:
        query += " AND timestamp >= ?"
        params.append(f"{start_date} 00:00:00" if len(start_date) == 10 else start_date)

    if end_date:
        query += " AND timestamp <= ?"
        params.append(f"{end_date} 23:59:59" if len(end_date) == 10 else end_date)

    if search:
        search_pattern = f"%{search}%"
        query += " AND (event_type LIKE ? OR driver_name LIKE ? OR danger_zone_name LIKE ? OR details LIKE ?)"
        params.extend([search_pattern, search_pattern, search_pattern, search_pattern])

    # Count query
    count_query = query.replace("SELECT *", "SELECT COUNT(*) as count", 1)
    cursor.execute(count_query, params)
    total_count = cursor.fetchone()["count"]

    # Paginated data query
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return {"events": rows, "total": total_count, "limit": limit, "offset": offset}


def export_events_csv(event_type: str = None, start_date: str = None, end_date: str = None) -> str:
    """Generate CSV string for exported events."""
    data = get_events(limit=10000, offset=0, event_type=event_type, start_date=start_date, end_date=end_date)
    events = data["events"]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Event ID",
        "Event Type",
        "Date & Time",
        "Driver Name",
        "Vehicle Speed (km/h)",
        "GPS Latitude",
        "GPS Longitude",
        "Danger Zone Name",
        "Event Details"
    ])

    for ev in events:
        writer.writerow([
            ev["id"],
            ev["event_type"],
            ev["timestamp"],
            ev["driver_name"],
            ev["vehicle_speed"],
            ev["latitude"],
            ev["longitude"],
            ev["danger_zone_name"],
            ev["details"]
        ])

    return output.getvalue()


# ==========================================
# DANGER ZONE OPERATIONS
# ==========================================

def get_all_zones():
    """Retrieve all danger zones."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM danger_zones ORDER BY code ASC, id ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def get_zone_by_id(zone_id: int):
    """Retrieve a single danger zone by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM danger_zones WHERE id = ?", (zone_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def add_zone(code: str, name: str, latitude: float, longitude: float,
             radius_meters: float, speed_limit: float, severity: str = "MEDIUM",
             details: str = "") -> int:
    """Add a new danger zone."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        INSERT INTO danger_zones (code, name, latitude, longitude, radius_meters, speed_limit, severity, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        code.strip().upper(),
        name.strip(),
        float(latitude),
        float(longitude),
        float(radius_meters),
        float(speed_limit),
        severity.strip().upper(),
        (details or "").strip(),
        now
    ))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


def update_zone(zone_id: int, code: str, name: str, latitude: float, longitude: float,
                radius_meters: float, speed_limit: float, severity: str = "MEDIUM",
                details: str = "") -> bool:
    """Update an existing danger zone."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE danger_zones
        SET code = ?, name = ?, latitude = ?, longitude = ?, radius_meters = ?, speed_limit = ?, severity = ?, details = ?
        WHERE id = ?
    """, (
        code.strip().upper(),
        name.strip(),
        float(latitude),
        float(longitude),
        float(radius_meters),
        float(speed_limit),
        severity.strip().upper(),
        (details or "").strip(),
        zone_id
    ))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


def delete_zone(zone_id: int) -> bool:
    """Delete a danger zone by ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM danger_zones WHERE id = ?", (zone_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ==========================================
# DRIVER OPERATIONS
# ==========================================

def get_all_drivers():
    """Retrieve all drivers."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM drivers ORDER BY id ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def add_or_update_driver(name: str, license_number: str, authorized: int = 1, notes: str = ""):
    """Add a new driver or update existing driver's auth status by license number."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("SELECT id FROM drivers WHERE license_number = ?", (license_number,))
    existing = cursor.fetchone()

    if existing:
        cursor.execute("""
            UPDATE drivers
            SET name = ?, authorized = ?, notes = ?
            WHERE id = ?
        """, (name, authorized, notes, existing["id"]))
        driver_id = existing["id"]
    else:
        cursor.execute("""
            INSERT INTO drivers (name, license_number, authorized, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (name, license_number, authorized, notes, now))
        driver_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return driver_id


# ==========================================
# COMMAND AUDIT LOG
# ==========================================

def log_command(command: str, source: str = "UI", status: str = "SENT"):
    """Record an executed command into audit log."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        INSERT INTO commands_log (command, source, status, timestamp)
        VALUES (?, ?, ?, ?)
    """, (command, source, status, now))
    conn.commit()
    conn.close()

