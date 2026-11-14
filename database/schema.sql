-- AcciAlert Unified SQLite Database Schema
-- Location: database/accialert.db

-- 1. Drivers Registry (Biometrically Enrolled Pilots)
CREATE TABLE IF NOT EXISTS drivers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    license_number TEXT UNIQUE,
    authorized INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    created_at TEXT NOT NULL
);

-- 2. Audit Trail Events Log
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    driver_name TEXT,
    vehicle_speed REAL DEFAULT 0.0,
    latitude REAL DEFAULT 13.0827,
    longitude REAL DEFAULT 80.2707,
    danger_zone_name TEXT,
    details TEXT
);

-- 3. Danger Zones (Geofencing & Dynamic Speed Governor)
CREATE TABLE IF NOT EXISTS danger_zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    radius_meters REAL NOT NULL,
    speed_limit REAL NOT NULL,
    severity TEXT NOT NULL DEFAULT 'MEDIUM',
    details TEXT,
    created_at TEXT NOT NULL
);

-- 4. Vehicle Commands Log
CREATE TABLE IF NOT EXISTS commands_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    source TEXT DEFAULT 'UI',
    status TEXT DEFAULT 'SENT',
    timestamp TEXT NOT NULL
);

-- 5. Real-Time Telemetry Cache
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    speed REAL DEFAULT 0.0,
    alcohol_adc INTEGER DEFAULT 180,
    obstacle_dist INTEGER DEFAULT 180,
    tilt_deg REAL DEFAULT 2.0,
    lat REAL DEFAULT 13.0827,
    lon REAL DEFAULT 80.2707,
    active_zone TEXT DEFAULT 'NONE',
    auth_state INTEGER DEFAULT 1
);

