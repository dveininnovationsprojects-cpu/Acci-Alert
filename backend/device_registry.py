"""ESP32 devices and the commands waiting for them.

Identity
  The backend owns the device id (config.DEVICE_ID, or devices added with backend/tools/devices.py).
  The firmware holds no key and no id in its source code. The first time it starts it calls
  POST /api/v1/device/register with its MAC address. This works only while the operator has opened a
  pairing window for that device. The backend then returns the device id and a long random token, once.
  The token is stored on the ESP32 in flash (Preferences) and on the backend only as a SHA-256 hash.

Commands
  The dashboard queues commands (STOP, RESET, ACK, AUTH, LIMIT) with enqueue_command().
  The ESP32 collects them with GET /api/v1/device/commands and confirms each one with
  POST /api/v1/device/commands/ack. An unconfirmed command is offered again after a few seconds,
  a confirmed one never again, and a newer AUTH or LIMIT replaces an older one that is still waiting.
"""

import hashlib
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:
    import config
    import database
except ImportError:  # imported as a package
    from backend import config, database

logger = logging.getLogger("AcciAlert.Devices")

COMMAND_TYPES = ("STOP", "RESET", "ACK", "AUTH", "LIMIT", "ZONES")
REPLACED_BY_NEWER = ("AUTH", "LIMIT", "ZONES")        # only the latest value matters
ACK_RESULTS = ("executed", "rejected", "failed")
_DEVICE_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_MAC_RE = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}")

_FAILED_LIMIT = 10        # wrong registration attempts per address ...
_FAILED_WINDOW_S = 300    # ... in this many seconds, then 429
_failed: Dict[str, List[float]] = {}
_failed_lock = threading.Lock()


def now_text(offset_s: float = 0.0) -> str:
    return (datetime.now() + timedelta(seconds=offset_s)).strftime("%Y-%m-%d %H:%M:%S")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalise_mac(mac: Any) -> Optional[str]:
    if not isinstance(mac, str):
        return None
    text = mac.strip().upper().replace("-", ":")
    return text if _MAC_RE.fullmatch(text) else None


def _connect():
    return closing(database.get_db_connection())


def _retry(fn):
    """Run a short write, retrying if the database is busy for a moment."""
    for attempt in range(4):
        try:
            return fn()
        except sqlite3.OperationalError:
            if attempt == 3:
                raise
            time.sleep(0.2 * (attempt + 1))


# Devices (operator side)

def public_view(row: sqlite3.Row) -> Dict[str, Any]:
    """A device record without its token hash."""
    d = dict(row)
    d.pop("token_hash", None)
    return d


def ensure_default_device() -> None:
    """The configured device id exists from the first start (not yet paired)."""
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM devices LIMIT 1").fetchone() is None:
            conn.execute("INSERT INTO devices (device_id, name, status, created_at) VALUES (?, ?, 'pending', ?)",
                         (config.DEVICE_ID, "AcciAlert car", now_text()))
            conn.commit()
            logger.info("Device %s created. Open a pairing window to connect the ESP32.", config.DEVICE_ID)


def list_devices() -> List[Dict[str, Any]]:
    with _connect() as conn:
        return [public_view(r) for r in conn.execute("SELECT * FROM devices ORDER BY created_at, device_id").fetchall()]


def get_device(device_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        return public_view(row) if row else None


def add_device(device_id: str, name: str = "") -> None:
    if not _DEVICE_ID_RE.fullmatch(device_id or ""):
        raise ValueError("device id must be 1-64 characters: letters, digits, _ . -")
    with _connect() as conn:
        try:
            conn.execute("INSERT INTO devices (device_id, name, status, created_at) VALUES (?, ?, 'pending', ?)",
                         (device_id, name[:100], now_text()))
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(f"device {device_id} already exists")


def open_pairing(device_id: str, mac: Optional[str] = None, minutes: Optional[int] = None) -> str:
    """Let the next ESP32 (with this MAC, or any MAC when none is given) claim an unpaired device.
    Returns the time the window closes."""
    minutes = minutes or config.DEVICE_PAIRING_MINUTES
    clean_mac = None
    if mac:
        clean_mac = normalise_mac(mac)
        if clean_mac is None:
            raise ValueError("MAC address must look like AA:BB:CC:DD:EE:FF")
    until = now_text(minutes * 60)
    with _connect() as conn:
        row = conn.execute("SELECT status FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        if row is None:
            raise ValueError(f"device {device_id} does not exist")
        if row["status"] != "pending":
            raise ValueError(f"device {device_id} is already paired. Run 'reset' first to pair it again.")
        conn.execute("UPDATE devices SET pairing_until = ?, mac = ? WHERE device_id = ?", (until, clean_mac, device_id))
        conn.commit()
    return until


def reset_device(device_id: str) -> None:
    """Forget the token. The ESP32 can no longer connect until it is paired again."""
    _set(device_id, "UPDATE devices SET status='pending', token_hash=NULL, mac=NULL, pairing_until=NULL, claimed_at=NULL WHERE device_id=?")
    with _connect() as conn:
        conn.execute("UPDATE device_commands SET status='expired' WHERE device_id=? AND status IN ('pending','delivered')", (device_id,))
        conn.commit()


def set_enabled(device_id: str, enabled: bool) -> None:
    if enabled:
        _set(device_id, "UPDATE devices SET status = CASE WHEN token_hash IS NULL THEN 'pending' ELSE 'active' END WHERE device_id=? AND status='disabled'")
    else:
        _set(device_id, "UPDATE devices SET status='disabled' WHERE device_id=?")


def remove_device(device_id: str) -> None:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
        conn.execute("DELETE FROM device_commands WHERE device_id = ?", (device_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"device {device_id} does not exist")


def _set(device_id: str, sql: str) -> None:
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM devices WHERE device_id = ?", (device_id,)).fetchone() is None:
            raise ValueError(f"device {device_id} does not exist")
        conn.execute(sql, (device_id,))
        conn.commit()


def unpaired_devices() -> List[str]:
    with _connect() as conn:
        return [r["device_id"] for r in conn.execute("SELECT device_id FROM devices WHERE status='pending'").fetchall()]


# Registration (ESP32 side, no token yet)

def _too_many_failures(ip: str) -> bool:
    now = time.time()
    with _failed_lock:
        recent = [t for t in _failed.get(ip, []) if now - t < _FAILED_WINDOW_S]
        _failed[ip] = recent
        return len(recent) >= _FAILED_LIMIT


def _note_failure(ip: str) -> None:
    with _failed_lock:
        _failed.setdefault(ip, []).append(time.time())


def clear_failures() -> None:
    with _failed_lock:
        _failed.clear()


def register(mac: Any, firmware: Any, client_ip: str) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[int, str, str]]]:
    """Claim an unpaired device. Returns ({device_id, token}, None) or (None, (http_status, code, message))."""
    if _too_many_failures(client_ip):
        return None, (429, "too_many_attempts", "Too many failed attempts. Wait a few minutes.")
    clean_mac = normalise_mac(mac)
    if clean_mac is None:
        return None, (422, "invalid_mac", "mac must look like AA:BB:CC:DD:EE:FF.")
    fw = str(firmware or "")[:32]

    with _connect() as conn:
        taken = conn.execute("SELECT device_id FROM devices WHERE mac = ? AND status IN ('active','disabled')",
                             (clean_mac,)).fetchone()
        if taken:
            _note_failure(client_ip)
            return None, (409, "already_registered",
                          "This ESP32 is already registered. Ask the operator to reset the device to pair it again.")
        row = conn.execute(
            "SELECT device_id FROM devices WHERE status='pending' AND pairing_until IS NOT NULL AND pairing_until > ? "
            "AND (mac IS NULL OR mac = ?) ORDER BY (mac IS NULL), created_at LIMIT 1", (now_text(), clean_mac)).fetchone()
        if row is None:
            _note_failure(client_ip)
            return None, (403, "pairing_closed", "No device is waiting to be paired. Ask the operator to open a pairing window.")
        token = secrets.token_urlsafe(32)
        cur = conn.execute(
            "UPDATE devices SET status='active', token_hash=?, mac=?, firmware=?, claimed_at=?, pairing_until=NULL, last_ip=? "
            "WHERE device_id=? AND status='pending'", (hash_token(token), clean_mac, fw, now_text(), client_ip, row["device_id"]))
        conn.commit()
        if cur.rowcount != 1:      # another ESP32 claimed it a moment earlier
            return None, (403, "pairing_closed", "No device is waiting to be paired. Ask the operator to open a pairing window.")
    logger.warning("Device %s paired with MAC %s from %s", row["device_id"], clean_mac, client_ip)
    return {"device_id": row["device_id"], "token": token}, None


# Authentication (every later request)

def authenticate(token: str) -> Optional[Dict[str, Any]]:
    """The active device that owns this token, or None."""
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM devices WHERE token_hash = ? AND status = 'active'", (hash_token(token),)).fetchone()
        return public_view(row) if row else None


def touch(device_id: str, ip: str) -> None:
    try:
        with _connect() as conn:
            conn.execute("UPDATE devices SET last_seen=?, last_ip=? WHERE device_id=?", (now_text(), ip, device_id))
            conn.commit()
    except sqlite3.OperationalError:
        pass    # last_seen is informational only


# Commands

def _encode(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value)


def _decode(text: Optional[str]) -> Any:
    return None if text is None else json.loads(text)


def enqueue_command(command_type: str, value: Any = None, device_id: Optional[str] = None) -> List[int]:
    """Queue a command for one device, or for every active device. Returns the new command ids."""
    ctype = str(command_type or "").upper()
    if ctype not in COMMAND_TYPES:
        return []
    if ctype == "AUTH":
        value = 1 if value in (1, True, "1", "true", "True") else 0
    elif ctype == "LIMIT":
        try:
            value = float(value)
        except (TypeError, ValueError):
            return []
        if not (0 <= value <= 300):
            return []
        value = int(value) if value == int(value) else value
    elif ctype == "ZONES":
        zones = value.get("zones") if isinstance(value, dict) else None
        if not isinstance(zones, list) or len(zones) > config.DEVICE_MAX_ZONES:
            return []
    else:
        value = None

    def write():
        ids = []
        with _connect() as conn:
            if device_id:
                targets = [device_id]
            else:
                targets = [r["device_id"] for r in conn.execute("SELECT device_id FROM devices WHERE status='active'").fetchall()]
            for target in targets:
                if ctype in REPLACED_BY_NEWER:
                    conn.execute("UPDATE device_commands SET status='superseded' WHERE device_id=? AND type=? AND status IN ('pending','delivered')",
                                 (target, ctype))
                cur = conn.execute(
                    "INSERT INTO device_commands (device_id, type, value, status, created_at, expires_at) VALUES (?, ?, ?, 'pending', ?, ?)",
                    (target, ctype, _encode(value), now_text(), now_text(config.DEVICE_COMMAND_TTL_S)))
                ids.append(cur.lastrowid)
            conn.commit()
        return ids
    return _retry(write)


def fetch_commands(device_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Commands for the ESP32 to run now (oldest first). A command is marked as delivered, and offered
    again only if it is not acknowledged within DEVICE_COMMAND_REDELIVER_S seconds."""
    def work():
        out = []
        with _connect() as conn:
            now = now_text()
            conn.execute("UPDATE device_commands SET status='expired' WHERE device_id=? AND status IN ('pending','delivered') AND expires_at < ?",
                         (device_id, now))
            conn.execute("UPDATE device_commands SET status='failed', result='no acknowledgement' WHERE device_id=? AND status='delivered' AND attempts >= ?",
                         (device_id, config.DEVICE_COMMAND_MAX_ATTEMPTS))
            retry_before = now_text(-config.DEVICE_COMMAND_REDELIVER_S)
            rows = conn.execute(
                "SELECT * FROM device_commands WHERE device_id=? AND (status='pending' OR (status='delivered' AND delivered_at <= ?)) "
                "ORDER BY id LIMIT ?", (device_id, retry_before, max(1, min(50, limit)))).fetchall()
            for r in rows:
                conn.execute("UPDATE device_commands SET status='delivered', delivered_at=?, attempts=attempts+1 WHERE id=?", (now, r["id"]))
                out.append({"id": r["id"], "type": r["type"], "value": _decode(r["value"]),
                            "created_at": r["created_at"], "expires_at": r["expires_at"], "attempt": r["attempts"] + 1})
            conn.commit()
        return out
    return _retry(work)


def pending_count(device_id: str) -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM device_commands WHERE device_id=? AND status IN ('pending','delivered') AND expires_at >= ?",
                            (device_id, now_text())).fetchone()[0]


def acknowledge(device_id: str, acks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Record the ESP32's answers. Repeating an acknowledgement is harmless.
    Returns {"acknowledged": [...ids], "already_done": [...ids], "unknown": [...ids]}."""
    done, again, unknown = [], [], []

    def work():
        with _connect() as conn:
            for a in acks:
                cid, result = a["id"], a["result"]
                row = conn.execute("SELECT status FROM device_commands WHERE id=? AND device_id=?", (cid, device_id)).fetchone()
                if row is None:
                    unknown.append(cid)
                elif row["status"] in ("acked", "failed"):
                    again.append(cid)
                else:
                    conn.execute("UPDATE device_commands SET status=?, acked_at=?, result=? WHERE id=?",
                                 ("acked" if result == "executed" else "failed", now_text(),
                                  (result + (": " + a["detail"] if a.get("detail") else ""))[:200], cid))
                    done.append(cid)
            conn.commit()
    _retry(work)
    return {"acknowledged": done, "already_done": again, "unknown": unknown}


def purge_old_commands(days: int = 7) -> None:
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM device_commands WHERE created_at < ?", (now_text(-days * 86400),))
            conn.commit()
    except sqlite3.OperationalError:
        pass


def list_commands(device_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    with _connect() as conn:
        if device_id:
            rows = conn.execute("SELECT * FROM device_commands WHERE device_id=? ORDER BY id DESC LIMIT ?", (device_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM device_commands ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
