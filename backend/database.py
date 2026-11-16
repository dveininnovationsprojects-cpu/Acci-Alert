"""Dashboard database: events, danger zones, drivers and the command log.
"""

import sqlite3
import csv
import io
from contextlib import closing
import time
from datetime import datetime
from config import DATABASE_PATH, INITIAL_DANGER_ZONES, INITIAL_DRIVERS


def get_db_connection():
    """Create and return a configured SQLite connection."""
    conn = sqlite3.connect(DATABASE_PATH, timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys and WAL mode for better concurrency
    if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
        for attempt in range(5):  # stored in the file; retry if another connection is busy right now
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                break
            except sqlite3.OperationalError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Severity of each event type, used when the caller does not give one.
_SEVERITY_BY_TYPE = {
    "ACCIDENT": "CRITICAL", "CAR CRASHED": "CRITICAL", "ALCOHOL DETECTED": "CRITICAL", "OBSTACLE DETECTED": "CRITICAL",
    "UNAUTHORISED DRIVER": "CRITICAL", "DANGER ZONE SPEEDING": "CRITICAL",
    "OVERSPEED_IN_ZONE": "HIGH", "EMERGENCY STOP": "HIGH", "UNAUTHORISED": "HIGH",
    "OVERSPEED": "WARNING", "DANGER ZONE": "MEDIUM", "ZONE_ENTER": "MEDIUM",
    "ZONE_EXIT": "INFO", "AUTH_OK": "INFO", "DRIVER_ENROLLED": "INFO", "DRIVER_DELETED": "MEDIUM", "LOGS_CLEARED": "INFO",
}


def severity_for(event_type) -> str:
    """Default severity for an event type (INFO when unknown)."""
    return _SEVERITY_BY_TYPE.get(str(event_type or "").upper(), "INFO")


def init_db():
    """Initialize database tables and seed initial records if empty."""
    with closing(get_db_connection()) as conn:
        _init_db(conn)


def _init_db(conn):
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
            details TEXT,
            severity TEXT NOT NULL DEFAULT 'INFO'
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

    # Driver details added for live face enrolment (older databases are upgraded in place)
    existing_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(drivers)").fetchall()}
    for column, definition in [
        ("face_samples", "INTEGER NOT NULL DEFAULT 0"),    # face signatures saved for this driver
        ("photos_saved", "INTEGER NOT NULL DEFAULT 0"),    # photos saved in backend/dataset/<name>/
        ("photo_folder", "TEXT"),
        ("last_enrolled_at", "TEXT"),
    ]:
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE drivers ADD COLUMN {column} {definition}")

    # One row per enrolment, so the full history of a driver can be shown
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS driver_enrolments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            driver_name TEXT NOT NULL,
            license_number TEXT,
            frames_used INTEGER NOT NULL,
            face_samples INTEGER NOT NULL,
            photos_saved INTEGER NOT NULL,
            photo_folder TEXT,
            enrolled_at TEXT NOT NULL
        )
    """)

    # ESP32 Wi-Fi telemetry (one row per accepted POST /api/v1/telemetry)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS device_telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            device_timestamp TEXT NOT NULL,
            received_at TEXT NOT NULL,
            alcohol_adc INTEGER NOT NULL,
            distance_cm REAL NOT NULL,
            accel_x REAL NOT NULL, accel_y REAL NOT NULL, accel_z REAL NOT NULL,
            gyro_x REAL NOT NULL, gyro_y REAL NOT NULL, gyro_z REAL NOT NULL,
            speed_rpm REAL NOT NULL,
            ir_pulse_count INTEGER NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            gps_fix INTEGER NOT NULL,
            gps_satellites INTEGER NOT NULL,
            alert_alcohol INTEGER NOT NULL,
            alert_overspeed INTEGER NOT NULL,
            alert_accident INTEGER NOT NULL,
            alert_obstacle INTEGER NOT NULL,
            buzzer INTEGER NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_telemetry_dev ON device_telemetry(device_id, id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_telemetry_rx ON device_telemetry(received_at)")

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

    # ESP32 devices (registered on the backend; the token is stored only as a hash) and the commands waiting for them
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            mac TEXT,
            token_hash TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            pairing_until TEXT,
            created_at TEXT NOT NULL,
            claimed_at TEXT,
            last_seen TEXT,
            last_ip TEXT,
            firmware TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS device_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            type TEXT NOT NULL,
            value TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            delivered_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            acked_at TEXT,
            result TEXT
        )
    """)

    # SMS alerts that were sent (or tried). The limits on how many texts go out are counted from this table.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sms_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sms_log_time ON sms_log(timestamp)")

    # Indexes: the event table is filtered and sorted all the time and only ever grows
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_device_commands_open ON device_commands(device_id, status)")
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_events_driver ON events(driver_name)",
        "CREATE INDEX IF NOT EXISTS idx_commands_timestamp ON commands_log(timestamp)",
    ):
        cursor.execute(statement)

    # Events get a severity column (older databases: add it and fill it from the event type)
    if "severity" not in [r[1] for r in cursor.execute("PRAGMA table_info(events)").fetchall()]:
        cursor.execute("ALTER TABLE events ADD COLUMN severity TEXT NOT NULL DEFAULT 'INFO'")
        for event_type in [r[0] for r in cursor.execute("SELECT DISTINCT event_type FROM events").fetchall()]:
            cursor.execute("UPDATE events SET severity = ? WHERE event_type = ?", (severity_for(event_type), event_type))
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity)")

    # The server used the spelling UNAUTHORIZED while the dashboard filter and alerts use UNAUTHORISED,
    # so the filter missed those events. Old rows are renamed once.
    cursor.execute("UPDATE events SET event_type = 'UNAUTHORISED DRIVER' WHERE event_type = 'UNAUTHORIZED DRIVER'")

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


# Event operations

def add_event(event_type: str, driver_name: str, vehicle_speed: float = 0.0,
              latitude: float = 13.0827, longitude: float = 80.2707, danger_zone_name: str = None,
              details: str = None, timestamp: str = None, severity: str = None) -> int:
    """Insert a new safety or system event. Severity defaults from the event type."""
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    severity = str(severity or severity_for(event_type)).strip().upper()

    with closing(get_db_connection()) as conn:
        cursor = conn.execute("""
            INSERT INTO events (event_type, timestamp, driver_name, vehicle_speed, latitude, longitude, danger_zone_name, details, severity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_type,
            timestamp,
            driver_name,
            round(float(vehicle_speed), 1) if vehicle_speed is not None else 0.0,
            round(float(latitude), 6) if latitude is not None else 0.0,
            round(float(longitude), 6) if longitude is not None else 0.0,
            danger_zone_name or "None",
            details or "",
            severity
        ))
        conn.commit()
        return cursor.lastrowid


def get_events(limit: int = 100, offset: int = 0, event_type: str = None,
               start_date: str = None, end_date: str = None, search: str = None,
               severity: str = None, event_types=None):
    """Retrieve filtered events list and total count.
    event_types: optional list/tuple of types to include (for example only the face-login events)."""
    query = "SELECT * FROM events WHERE 1=1"
    params = []

    if event_type and event_type.upper() != "ALL":
        query += " AND UPPER(event_type) = UPPER(?)"
        params.append(event_type)

    if event_types:
        query += " AND event_type IN (%s)" % ", ".join("?" * len(event_types))
        params.extend(event_types)

    if severity and severity.upper() != "ALL":
        query += " AND UPPER(severity) = UPPER(?)"
        params.append(severity)

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

    with closing(get_db_connection()) as conn:
        count_query = query.replace("SELECT *", "SELECT COUNT(*) as count", 1)
        total_count = conn.execute(count_query, params).fetchone()["count"]
        rows = [dict(row) for row in conn.execute(query + " ORDER BY id DESC LIMIT ? OFFSET ?",
                                                  params + [limit, offset]).fetchall()]

    return {"events": rows, "total": total_count, "limit": limit, "offset": offset}


# Face-login events: AUTH_OK, UNAUTHORISED, and the UNAUTHORISED DRIVER rows whose details start with "Match"
# (the same type is also used for the telemetry alert "car is running unauthorised").
_FACE_EVENT_SQL = ("(event_type IN ('AUTH_OK', 'UNAUTHORISED') "
                   "OR (event_type = 'UNAUTHORISED DRIVER' AND details LIKE 'Match%'))")


def get_face_events(limit: int = 100):
    """Newest face-login events first."""
    with closing(get_db_connection()) as conn:
        rows = conn.execute(f"SELECT * FROM events WHERE {_FACE_EVENT_SQL} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def count_face_events() -> dict:
    """{"authorised": n, "rejected": n, "last_timestamp": text or None} for the face-login audit trail."""
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            f"SELECT COALESCE(SUM(event_type = 'AUTH_OK'), 0) AS ok, "
            f"COALESCE(SUM(event_type != 'AUTH_OK'), 0) AS bad, MAX(timestamp) AS last "
            f"FROM events WHERE {_FACE_EVENT_SQL}").fetchone()
    return {"authorised": row["ok"], "rejected": row["bad"], "last_timestamp": row["last"]}


def clear_events() -> int:
    """Delete all events. Returns how many were deleted."""
    with closing(get_db_connection()) as conn:
        cursor = conn.execute("DELETE FROM events")
        conn.commit()
        return cursor.rowcount


def export_events_csv(event_type: str = None, start_date: str = None, end_date: str = None, search: str = None,
                      severity: str = None) -> str:
    """Generate CSV string for exported events."""
    data = get_events(limit=10000, offset=0, event_type=event_type, start_date=start_date, end_date=end_date,
                      search=search, severity=severity)
    events = data["events"]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Event ID",
        "Event Type",
        "Severity",
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
            ev["severity"],
            ev["timestamp"],
            ev["driver_name"],
            ev["vehicle_speed"],
            ev["latitude"],
            ev["longitude"],
            ev["danger_zone_name"],
            ev["details"]
        ])

    return output.getvalue()


# Danger zone operations

def get_all_zones():
    """Retrieve all danger zones."""
    with closing(get_db_connection()) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM danger_zones ORDER BY code ASC, id ASC").fetchall()]


def add_zone(code: str, name: str, latitude: float, longitude: float,
             radius_meters: float, speed_limit: float, severity: str = "MEDIUM",
             details: str = "") -> int:
    """Add a new danger zone."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(get_db_connection()) as conn:
        cursor = conn.execute("""
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
        conn.commit()
        return cursor.lastrowid


def delete_zone(zone_id: int) -> bool:
    """Delete a danger zone by ID."""
    with closing(get_db_connection()) as conn:
        cursor = conn.execute("DELETE FROM danger_zones WHERE id = ?", (zone_id,))
        conn.commit()
        return cursor.rowcount > 0


# Driver operations

def get_all_drivers():
    """Retrieve all drivers."""
    with closing(get_db_connection()) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM drivers ORDER BY id ASC").fetchall()]


def add_or_update_driver(name: str, license_number: str = None, authorized: int = 1, notes: str = ""):
    """Add a new driver or update existing driver's auth status by license number or name."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(get_db_connection()) as conn:
        if license_number:
            existing = conn.execute("SELECT id, license_number FROM drivers WHERE license_number = ? OR name = ?",
                                    (license_number, name)).fetchone()
        else:
            existing = conn.execute("SELECT id, license_number FROM drivers WHERE name = ?", (name,)).fetchone()

        if existing:
            final_license = license_number if license_number else existing["license_number"]
            conn.execute("""
                UPDATE drivers
                SET name = ?, license_number = ?, authorized = ?, notes = ?
                WHERE id = ?
            """, (name, final_license, authorized, notes, existing["id"]))
            driver_id = existing["id"]
        else:
            if not license_number:
                import time
                license_number = f"TN-09-{datetime.now().year}-{int(time.time()) % 10000:04d}"
            cursor = conn.execute("""
                INSERT INTO drivers (name, license_number, authorized, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (name, license_number, authorized, notes, now))
            driver_id = cursor.lastrowid

        conn.commit()
        return driver_id


# Command audit log

def log_command(command: str, source: str = "UI", status: str = "SENT"):
    """Record an executed command into audit log."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(get_db_connection()) as conn:
        conn.execute("""
            INSERT INTO commands_log (command, source, status, timestamp)
            VALUES (?, ?, ?, ?)
        """, (command, source, status, now))
        conn.commit()


# Compatibility Aliases for Rest APIs & Test Suites


def get_recent_events(limit: int = 100, offset: int = 0, event_type: str = None):
    res = get_events(limit=limit, offset=offset, event_type=event_type)
    return res["events"]


def get_events_count(event_type: str = None) -> int:
    res = get_events(limit=1, offset=0, event_type=event_type)
    return res["total"]


# Driver details (name, license, face samples, photos, enrolment history)

def save_driver_enrolment(name: str, license_number: str, frames_used: int, face_samples: int,
                          photos_saved: int, photo_folder: str):
    """Store a finished enrolment: updates (or creates) the driver and adds a history row."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT id FROM drivers WHERE name = ?", (name,)).fetchone()
        note = f"Enrolled from live camera, {face_samples} face samples"
        if row:
            conn.execute("""
                UPDATE drivers
                SET license_number = COALESCE(?, license_number), authorized = 1, notes = ?,
                    face_samples = ?, photos_saved = photos_saved + ?, photo_folder = ?, last_enrolled_at = ?
                WHERE id = ?
            """, (license_number, note, face_samples, photos_saved, photo_folder, now, row["id"]))
        else:
            conn.execute("""
                INSERT INTO drivers (name, license_number, authorized, notes, created_at,
                                     face_samples, photos_saved, photo_folder, last_enrolled_at)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
            """, (name, license_number, note, now, face_samples, photos_saved, photo_folder, now))
        conn.execute("""
            INSERT INTO driver_enrolments (driver_name, license_number, frames_used, face_samples,
                                           photos_saved, photo_folder, enrolled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, license_number, frames_used, face_samples, photos_saved, photo_folder, now))
        conn.commit()
    finally:
        conn.close()


def get_driver_records():
    """Driver rows, most recently enrolled first."""
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT * FROM drivers ORDER BY COALESCE(last_enrolled_at, created_at) DESC, id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_driver(name: str) -> bool:
    """Remove a driver and the enrolment history. The event log is kept. True if a driver row was removed."""
    conn = get_db_connection()
    try:
        removed = conn.execute("DELETE FROM drivers WHERE name = ?", (name,)).rowcount
        conn.execute("DELETE FROM driver_enrolments WHERE driver_name = ?", (name,))
        conn.commit()
        return bool(removed)
    finally:
        conn.close()


def get_driver_history(name: str, limit: int = 100):
    """Enrolment history of one driver, newest first."""
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT * FROM driver_enrolments WHERE driver_name = ? ORDER BY id DESC LIMIT ?",
                            (name, int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def sync_driver_samples(sample_counts):
    """Make the database agree with the saved face signatures at server start.
    sample_counts: {driver name: number of face signatures in the gallery file}.
    Drivers enrolled before these columns existed get their sample count and date filled in."""
    conn = get_db_connection()
    try:
        for name, count in sample_counts.items():
            row = conn.execute("SELECT id, face_samples, last_enrolled_at FROM drivers WHERE name = ?", (name,)).fetchone()
            event = conn.execute("SELECT MAX(timestamp) FROM events WHERE event_type = 'DRIVER_ENROLLED' AND driver_name = ?",
                                 (name,)).fetchone()[0]
            when = event or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if row is None:
                conn.execute("""INSERT INTO drivers (name, authorized, notes, created_at, face_samples, last_enrolled_at)
                                VALUES (?, 1, 'Enrolled from live camera', ?, ?, ?)""", (name, when, count, when))
            else:
                conn.execute("UPDATE drivers SET face_samples = ?, last_enrolled_at = COALESCE(last_enrolled_at, ?) WHERE id = ?",
                             (count, when, row["id"]))
            has_history = conn.execute("SELECT 1 FROM driver_enrolments WHERE driver_name = ? LIMIT 1", (name,)).fetchone()
            if not has_history and count:
                license_number = conn.execute("SELECT license_number FROM drivers WHERE name = ?", (name,)).fetchone()[0]
                conn.execute("""INSERT INTO driver_enrolments (driver_name, license_number, frames_used, face_samples,
                                                               photos_saved, photo_folder, enrolled_at)
                                VALUES (?, ?, ?, ?, 0, NULL, ?)""", (name, license_number, count, count, when))
        conn.commit()
    finally:
        conn.close()



# ESP32 Wi-Fi telemetry storage

_DEVICE_COLUMNS = (
    "device_id", "device_timestamp", "received_at", "alcohol_adc", "distance_cm",
    "accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z",
    "speed_rpm", "ir_pulse_count", "latitude", "longitude", "gps_fix", "gps_satellites",
    "alert_alcohol", "alert_overspeed", "alert_accident", "alert_obstacle", "buzzer",
)
_last_device_purge = 0.0


def add_device_telemetry(row: dict, retries: int = 3) -> int:
    """Insert one validated reading (keys = _DEVICE_COLUMNS). Retries if the database is briefly busy."""
    import time
    sql = "INSERT INTO device_telemetry (%s) VALUES (%s)" % (
        ", ".join(_DEVICE_COLUMNS), ", ".join("?" * len(_DEVICE_COLUMNS)))
    values = [row[c] for c in _DEVICE_COLUMNS]
    for attempt in range(retries + 1):
        conn = get_db_connection()
        try:
            cur = conn.execute(sql, values)
            conn.commit()
            return cur.lastrowid
        except sqlite3.OperationalError:
            if attempt == retries:
                raise
            time.sleep(0.2 * (attempt + 1))
        finally:
            conn.close()


def get_device_telemetry(limit: int = 50, device_id: str = None):
    """Newest readings first."""
    conn = get_db_connection()
    try:
        if device_id:
            cur = conn.execute("SELECT * FROM device_telemetry WHERE device_id = ? ORDER BY id DESC LIMIT ?",
                               (device_id, limit))
        else:
            cur = conn.execute("SELECT * FROM device_telemetry ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def purge_device_telemetry(hours: int, min_interval_s: float = 3600.0) -> int:
    """Delete readings older than `hours`. Runs at most once per min_interval_s. Returns rows deleted."""
    import time
    from datetime import timedelta
    global _last_device_purge
    now = time.time()
    if now - _last_device_purge < min_interval_s:
        return 0
    _last_device_purge = now
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    try:
        cur = conn.execute("DELETE FROM device_telemetry WHERE received_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# SMS log
def add_sms_log(alert_type: str, recipient: str, status: str, detail: str = "") -> int:
    with closing(get_db_connection()) as conn:
        cur = conn.execute("INSERT INTO sms_log (timestamp, alert_type, recipient, status, detail) VALUES (?, ?, ?, ?, ?)",
                           (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), alert_type, recipient, status, detail[:300]))
        conn.commit()
        return cur.lastrowid


def update_sms_log(log_id: int, status: str, detail: str = "") -> None:
    with closing(get_db_connection()) as conn:
        conn.execute("UPDATE sms_log SET status = ?, detail = ? WHERE id = ?", (status, detail[:300], log_id))
        conn.commit()


def count_sms_since(seconds: float, alert_type: str = None) -> int:
    """Messages queued or sent in the last `seconds` seconds (failed ones do not count)."""
    from datetime import timedelta
    since = (datetime.now() - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
    sql = "SELECT COUNT(*) FROM sms_log WHERE timestamp >= ? AND status IN ('queued', 'sent')"
    params = [since]
    if alert_type:
        sql += " AND alert_type = ?"
        params.append(alert_type)
    with closing(get_db_connection()) as conn:
        return conn.execute(sql, params).fetchone()[0]


def get_sms_log(limit: int = 20):
    with closing(get_db_connection()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM sms_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
