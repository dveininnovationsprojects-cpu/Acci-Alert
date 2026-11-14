"""SQLite data-access layer for AcciAlert (file: accialert.db).

Tables: drivers, events, telemetry, danger_zones.

Integration contract (what other modules rely on):
  * log_event() COMMITS before it returns (requirement 9.1: write to DB before
    updating the UI) and NEVER rejects an event for bad field values - an
    accident event must not be lost because of a malformed field. Bad values
    are sanitised instead (see log_event).
  * Every function that takes a connection is serialised by one process-wide
    lock, so a single connection can be shared by the serial thread, Flask
    request threads and the purge thread without "database is locked" errors.
  * Zone writes are validated (zones.validate_zone) and raise ValueError with a
    safe message on bad input.
"""
import functools
import json
import logging
import os
import math
import sqlite3
import threading
import time
from pathlib import Path

try:
    from zones import ZONE_FIELDS, validate_zone  # noqa: F401  (ZONE_FIELDS re-exported)
except ImportError:
    from backend.zones import ZONE_FIELDS, validate_zone  # noqa: F401

log = logging.getLogger("accialert.db")

DEFAULT_DB = Path(os.environ.get("ACCIALERT_DATA_DIR") or Path(__file__).resolve().parent / "data") / "accialert_zones.db"
TELEMETRY_KEEP_SECONDS = 24 * 3600
VALID_SEVERITIES = ("low", "medium", "high", "critical")
MAX_EVENT_ROWS = 5000
_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS drivers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    authorised INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    type      TEXT NOT NULL,
    severity  TEXT NOT NULL,
    driver    TEXT,
    speed     REAL,
    lat       REAL,
    lon       REAL,
    zone_id   TEXT,
    details   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE TABLE IF NOT EXISTS telemetry (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    speed     REAL,
    alcohol   REAL,
    distance  REAL,
    tilt      REAL,
    lat       REAL,
    lon       REAL,
    state     TEXT,
    zone_id   TEXT
);
CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(timestamp);
CREATE TABLE IF NOT EXISTS danger_zones (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    radius_m    REAL NOT NULL CHECK (radius_m > 0),
    speed_limit INTEGER NOT NULL CHECK (speed_limit >= 0),
    severity    TEXT NOT NULL CHECK (severity IN ('low','medium','high','critical')),
    active      INTEGER NOT NULL DEFAULT 1
);
"""


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with _LOCK:
            return fn(*a, **kw)
    return wrapper


def _num(v):
    """Finite float or None (SQLite would store NaN as NULL anyway; be explicit)."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _text(v, maxlen):
    if v is None:
        return None
    return str(v)[:maxlen]


@_locked
def _open(path):
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")  # accident events must survive power loss
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _is_healthy(conn):
    try:
        return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False


def _repair(path):
    """The file is damaged: keep it as <name>.corrupt-<time>, start a new file and copy back
    every table that can still be read (zones, events, drivers). Telemetry is only a rolling
    24 hour log, so it is simply started again."""
    path = Path(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    kept = path.with_name(f"{path.name}.corrupt-{stamp}")
    for suffix in ("", "-wal", "-shm"):
        old = Path(str(path) + suffix)
        if old.exists():
            old.replace(Path(str(kept) + suffix))
    log.warning("Zone database was damaged. Kept it as %s and created a new one.", kept.name)

    conn = _open(path)
    conn.executescript(SCHEMA)
    conn.commit()
    try:
        conn.execute("ATTACH DATABASE ? AS damaged", (f"file:{kept}?mode=ro",))
    except sqlite3.DatabaseError:
        try:
            conn.execute("ATTACH DATABASE ? AS damaged", (str(kept),))
        except sqlite3.DatabaseError:
            return conn
    for table in ("danger_zones", "drivers", "events"):
        try:
            conn.execute(f"INSERT OR IGNORE INTO main.{table} SELECT * FROM damaged.{table}")
            conn.commit()
        except sqlite3.DatabaseError as err:
            log.warning("Could not recover table %s: %s", table, err)
            conn.rollback()
    try:
        conn.execute("DETACH DATABASE damaged")
    except sqlite3.DatabaseError:
        pass
    return conn


def connect(path=DEFAULT_DB):
    """Open a connection (creates file + schema if needed). A damaged file is repaired automatically."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = _open(path)
        healthy = _is_healthy(conn)
    except sqlite3.DatabaseError:
        healthy = False
    if not healthy:
        try:
            conn.close()
        except Exception:
            pass
        return _repair(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


@_locked
def zone_status(conn, since_ts):
    """For the dashboard counter: zones entered since since_ts, and when the current zone was entered."""
    entered = conn.execute("SELECT COUNT(*) FROM events WHERE type='ZONE_ENTER' AND timestamp>=?",
                           (since_ts,)).fetchone()[0]
    last = conn.execute("SELECT type, timestamp FROM events WHERE type IN ('ZONE_ENTER','ZONE_EXIT') "
                        "ORDER BY timestamp DESC, id DESC LIMIT 1").fetchone()
    inside_since = last["timestamp"] if last and last["type"] == "ZONE_ENTER" else None
    return {"entered_today": entered, "inside_since": inside_since}


# ---------------------------------------------------------------- drivers
@_locked
def add_driver(conn, name, authorised=True):
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 64):
        raise ValueError("driver name must be 1-64 characters")
    conn.execute(
        "INSERT OR REPLACE INTO drivers(name, authorised, created_at) VALUES (?,?,?)",
        (name.strip(), int(bool(authorised)), time.time()),
    )
    conn.commit()


@_locked
def list_drivers(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM drivers ORDER BY name")]


# ----------------------------------------------------------------- events
@_locked
def log_event(conn, type, severity, driver=None, speed=None, lat=None, lon=None,
              zone_id=None, details=None, timestamp=None):
    """Insert an event and COMMIT before returning. Returns the new event id.

    Never rejects for bad field values (an ACCIDENT must not be dropped):
    unknown severity -> 'high' (original kept in details), non-finite numbers
    -> NULL, long text truncated, dict/list details -> JSON."""
    type = _text(type, 64) or "UNKNOWN"
    note = None
    if severity not in VALID_SEVERITIES:
        note = f"[original severity: {severity!r}]"
        severity = "high"
    if isinstance(details, (dict, list)):
        try:
            details = json.dumps(details)
        except (TypeError, ValueError):
            details = str(details)
    details = _text(details, 2000)
    if note:
        details = f"{note} {details or ''}".strip()[:2000]
    ts = _num(timestamp)
    cur = conn.execute(
        "INSERT INTO events(timestamp,type,severity,driver,speed,lat,lon,zone_id,details)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (ts if ts is not None else time.time(), type, severity, _text(driver, 64),
         _num(speed), _num(lat), _num(lon), _text(zone_id, 32), details),
    )
    conn.commit()
    return cur.lastrowid


@_locked
def query_events(conn, type=None, date_from=None, date_to=None, limit=1000):
    """Filter by type and/or time range (epoch seconds). Newest first."""
    sql, args = "SELECT * FROM events WHERE 1=1", []
    if type:
        sql += " AND type = ?"
        args.append(str(type)[:64])
    df, dt = _num(date_from), _num(date_to)
    if df is not None:
        sql += " AND timestamp >= ?"
        args.append(df)
    if dt is not None:
        sql += " AND timestamp <= ?"
        args.append(dt)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 1000
    sql += " ORDER BY timestamp DESC, id DESC LIMIT ?"
    args.append(max(1, min(limit, MAX_EVENT_ROWS)))
    return [dict(r) for r in conn.execute(sql, args)]


EVENT_CSV_COLUMNS = ("id", "timestamp", "type", "severity", "driver", "speed",
                     "lat", "lon", "zone_id", "details")
_CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(v):
    """Stop spreadsheet formula injection: text starting with = + - @ gets a ' prefix."""
    if isinstance(v, str) and v.startswith(_CSV_DANGEROUS):
        return "'" + v
    return v


def events_to_csv(rows):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EVENT_CSV_COLUMNS)
    for r in rows:
        w.writerow([_csv_safe(r.get(c)) for c in EVENT_CSV_COLUMNS])
    return buf.getvalue()


# -------------------------------------------------------------- telemetry
@_locked
def log_telemetry(conn, speed=None, alcohol=None, distance=None, tilt=None,
                  lat=None, lon=None, state=None, zone_id=None, timestamp=None):
    """Store one sample (caller should call this at ~1 Hz, not 5 Hz)."""
    ts = _num(timestamp)
    conn.execute(
        "INSERT INTO telemetry(timestamp,speed,alcohol,distance,tilt,lat,lon,state,zone_id)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (ts if ts is not None else time.time(), _num(speed), _num(alcohol), _num(distance),
         _num(tilt), _num(lat), _num(lon), _text(state, 32), _text(zone_id, 32)),
    )
    conn.commit()


@_locked
def recent_telemetry(conn, seconds=60, now=None):
    """Last N seconds, oldest first (for the 60 s charts)."""
    now = now if now is not None else time.time()
    rows = conn.execute(
        "SELECT * FROM telemetry WHERE timestamp >= ? ORDER BY timestamp ASC", (now - seconds,)
    )
    return [dict(r) for r in rows]


@_locked
def purge_telemetry(conn, now=None):
    """Delete telemetry older than 24 h. Returns number of rows removed."""
    now = now if now is not None else time.time()
    cur = conn.execute("DELETE FROM telemetry WHERE timestamp < ?", (now - TELEMETRY_KEEP_SECONDS,))
    conn.commit()
    return cur.rowcount


def start_purge_scheduler(path=DEFAULT_DB, interval_s=3600):
    """Purge old telemetry in a background thread (own connection).
    Runs once immediately, then every interval_s. Returns a stop() function."""
    stop_evt = threading.Event()

    def loop():
        conn = connect(path)
        try:
            while True:
                try:
                    purge_telemetry(conn)
                except sqlite3.Error:
                    log.exception("telemetry purge failed; will retry")
                if stop_evt.wait(interval_s):
                    break
        finally:
            conn.close()

    threading.Thread(target=loop, name="telemetry-purge", daemon=True).start()
    return stop_evt.set


# ------------------------------------------------------------------ zones
@_locked
def upsert_zone(conn, zone):
    """Validate and insert/replace a zone. Raises ValueError on bad input."""
    z = validate_zone(zone)
    conn.execute(
        "INSERT OR REPLACE INTO danger_zones(id,name,type,lat,lon,radius_m,speed_limit,severity,active)"
        " VALUES (:id,:name,:type,:lat,:lon,:radius_m,:speed_limit,:severity,:active)", z)
    conn.commit()
    return z


@_locked
def get_zone(conn, zone_id):
    r = conn.execute("SELECT * FROM danger_zones WHERE id=?", (str(zone_id)[:32],)).fetchone()
    return dict(r) if r else None


@_locked
def list_zones(conn, active_only=False):
    sql = "SELECT * FROM danger_zones" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
    return [dict(r) for r in conn.execute(sql)]


@_locked
def delete_zone(conn, zone_id):
    cur = conn.execute("DELETE FROM danger_zones WHERE id=?", (str(zone_id)[:32],))
    conn.commit()
    return cur.rowcount > 0


@_locked
def set_zone_active(conn, zone_id, active):
    cur = conn.execute("UPDATE danger_zones SET active=? WHERE id=?",
                       (int(bool(active)), str(zone_id)[:32]))
    conn.commit()
    return cur.rowcount > 0


@_locked
def seed_zones_from_json(conn, path, overwrite=False):
    """Load zones.json into the DB. Skips ids that already exist unless overwrite.
    Invalid zones are skipped (logged), so one bad entry cannot stop startup."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    n = 0
    for z in data.get("zones", []):
        try:
            zid = validate_zone(z)["id"]
        except ValueError as e:
            log.warning("zones.json entry skipped: %s", e)
            continue
        if overwrite or get_zone(conn, zid) is None:
            upsert_zone(conn, z)
            n += 1
    return n


@_locked
def export_zones_to_json(conn, path):
    Path(path).write_text(json.dumps({"zones": list_zones(conn)}, indent=2), encoding="utf-8")
