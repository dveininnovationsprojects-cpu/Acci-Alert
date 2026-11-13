"""
AcciAlert - Database & Backend Event Logger
Module: AI Face Recognition & Driver Authorisation (Arsal)

Features:
  - Direct persistence into SQLite `accialert.db` (table: events)
  - Manages enrolled driver records in `drivers` table
  - REST API push to Muniyappan's dashboard (/api/events)
  - Thread-safe and resilient against offline dashboard backend
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import sqlite3
import requests

try:
    from . import config
except ImportError:
    from backend import config


class DatabaseLogger:
    """Logs driver recognition events to SQLite database and Dashboard API."""

    def __init__(self, db_path: Optional[Path] = None, api_url: Optional[str] = None):
        self.db_path = Path(db_path or config.DB_PATH)
        self.api_url = api_url or config.API_BASE_URL
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes required SQLite tables if they do not already exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Drivers table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS drivers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    enrolled_date TEXT NOT NULL,
                    status TEXT DEFAULT 'ACTIVE',
                    face_count INTEGER DEFAULT 0
                )
            """)
            # Events table matching Prasanth's specification
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    driver TEXT NOT NULL,
                    speed REAL DEFAULT 0.0,
                    lat REAL DEFAULT 0.0,
                    lon REAL DEFAULT 0.0,
                    zone TEXT DEFAULT '',
                    details TEXT
                )
            """)
            conn.commit()

    def register_enrolled_driver(self, driver_name: str, face_count: int):
        """Records or updates an enrolled driver in the drivers table."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO drivers (name, enrolled_date, status, face_count)
                VALUES (?, ?, 'ACTIVE', ?)
                ON CONFLICT(name) DO UPDATE SET
                    face_count = excluded.face_count,
                    enrolled_date = excluded.enrolled_date
            """, (driver_name, now, face_count))
            conn.commit()

    def log_auth_event(self,
                       driver: str,
                       is_authorised: bool,
                       confidence_distance: float,
                       similarity_pct: float,
                       extra_details: str = "") -> Dict[str, Any]:
        """
        Logs an authentication event to `events` table and notifies dashboard.
        """
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        event_type = "AUTH_OK" if is_authorised else "UNAUTHORISED"
        severity = "INFO" if is_authorised else "HIGH"
        status_text = "Authorised" if is_authorised else "Rejected"

        details = (f"Driver: {driver} | Status: {status_text} | "
                   f"Distance: {confidence_distance:.2f} | Similarity: {similarity_pct:.1f}%")
        if extra_details:
            details += f" | {extra_details}"

        # 1. Insert into local SQLite database
        event_id = None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO events (timestamp, type, severity, driver, speed, lat, lon, zone, details)
                VALUES (?, ?, ?, ?, 0.0, 0.0, 0.0, '', ?)
            """, (now_iso, event_type, severity, driver, details))
            conn.commit()
            event_id = cursor.lastrowid

        event_payload = {
            "id": event_id,
            "timestamp": now_iso,
            "type": event_type,
            "severity": severity,
            "driver": driver,
            "speed": 0.0,
            "lat": 0.0,
            "lon": 0.0,
            "zone": "",
            "details": details
        }

        # 2. Forward to Dashboard REST API if reachable
        self._notify_dashboard_api(event_payload)

        return event_payload

    def _notify_dashboard_api(self, payload: Dict[str, Any]):
        """Sends event POST request to Muniyappan's dashboard server."""
        try:
            endpoint = f"{self.api_url}/api/events"
            requests.post(endpoint, json=payload, timeout=config.API_TIMEOUT)
        except Exception:
            # Dashboard may not be running yet; fail silently to avoid blocking AI loop
            pass

    def get_recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieves most recent events from database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

