"""Tests for the server's telemetry handling, the zone engine hook and database self-repair."""

import sqlite3
import time

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db as zone_db  # noqa: E402


# ---------------------------------------------------------------- telemetry -> zone engine
def test_entering_a_zone_logs_events_and_saves_telemetry(monkeypatch):
    from backend import app as app_module
    zone_db = app_module.zone_db   # the app's own copy of the module

    monkeypatch.setattr(app_module.socketio, "emit", lambda *a, **k: None)
    monkeypatch.setattr(app_module.state, "last_telemetry_saved", 0.0)
    monkeypatch.setattr(app_module.zone_engine, "set_zones", app_module.zone_engine.set_zones)
    app_module.zone_engine.set_zones(zone_db.list_zones(app_module.zone_conn))

    zone = zone_db.list_zones(app_module.zone_conn)[0]
    packet = {"spd": zone["speed_limit"] + 20, "lat": zone["lat"], "lon": zone["lon"], "fix": 1, "zone": zone["id"]}
    app_module.process_telemetry_packet(packet, source="TEST")

    types = [e["type"] for e in zone_db.query_events(app_module.zone_conn)]
    assert "ZONE_ENTER" in types
    assert "OVERSPEED_IN_ZONE" in types
    shown = [e["event_type"] for e in app_module.database.get_events(limit=50)["events"]]
    assert "ZONE_ENTER" in shown
    assert app_module.zone_conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0] >= 1


def test_telemetry_is_saved_about_once_per_second(monkeypatch):
    from backend import app as app_module
    zone_db = app_module.zone_db   # the app's own copy of the module

    monkeypatch.setattr(app_module.socketio, "emit", lambda *a, **k: None)
    monkeypatch.setattr(app_module.state, "mode", "serial")   # stop the simulator thread adding its own rows
    time.sleep(1.2)                                           # let a running simulator step finish
    monkeypatch.setattr(app_module.state, "last_telemetry_saved", 0.0)
    before = app_module.zone_conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    for _ in range(5):
        app_module.process_telemetry_packet({"spd": 30, "lat": 13.1, "lon": 80.3, "fix": 1}, source="TEST")
    after = app_module.zone_conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    assert after - before == 1


def test_zone_status_endpoint(monkeypatch):
    from backend import app as app_module
    zone_db = app_module.zone_db   # the app's own copy of the module

    now = time.time()
    zone_db.log_event(app_module.zone_conn, "ZONE_ENTER", "high", driver="T", zone_id="Z01", timestamp=now)
    data = app_module.app.test_client().get("/zm/api/zone-status").get_json()
    assert data["entered_today"] >= 1
    assert data["inside_since"] is not None


# ---------------------------------------------------------------- damaged database is repaired
def make_database(path, telemetry_rows=4000):
    conn = zone_db.connect(path)
    zone_db.log_event(conn, "ZONE_ENTER", "high", driver="Arun", zone_id="Z01", details="kept")
    for i in range(telemetry_rows):
        zone_db.log_telemetry(conn, speed=i % 90, lat=13.0, lon=80.0, state="RUNNING")
    zones_before = len(zone_db.list_zones(conn))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return zones_before


def test_damaged_database_is_repaired_and_keeps_zones_and_events(tmp_path):
    path = tmp_path / "zones.db"
    zones_before = make_database(path)

    data = bytearray(path.read_bytes())
    for offset in range(len(data) // 2, len(data) // 2 + 4096 * 3, 7):   # scribble over some pages
        data[offset] = (data[offset] + 91) % 256
    path.write_bytes(bytes(data))
    check = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            check.execute("PRAGMA integrity_check").fetchall()
            check.execute("SELECT * FROM telemetry").fetchall()
    finally:
        check.close()

    conn = zone_db.connect(path)
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert len(zone_db.list_zones(conn)) == zones_before
    assert any(e["details"] == "kept" for e in zone_db.query_events(conn))
    assert list(tmp_path.glob("zones.db.corrupt-*"))   # the damaged file is kept, not deleted
    zone_db.log_telemetry(conn, speed=10)               # and the new file works


def test_healthy_database_is_left_alone(tmp_path):
    path = tmp_path / "zones.db"
    make_database(path, telemetry_rows=10)
    zone_db.connect(path).close()
    assert not list(tmp_path.glob("zones.db.corrupt-*"))


# ---------------------------------------------------------------- driver details in the database
def test_driver_table_is_upgraded_and_history_is_kept():
    from backend import app as app_module
    db = app_module.database
    conn = db.get_db_connection()
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(drivers)")}
    conn.close()
    assert {"face_samples", "photos_saved", "photo_folder", "last_enrolled_at"} <= columns

    db.save_driver_enrolment("DbTest", "DL-7777", 25, 25, 25, "backend/dataset/DbTest")
    db.save_driver_enrolment("DbTest", "DL-7777", 20, 45, 20, "backend/dataset/DbTest")
    row = next(d for d in db.get_driver_records() if d["name"] == "DbTest")
    assert (row["license_number"], row["face_samples"], row["photos_saved"]) == ("DL-7777", 45, 45)
    history = db.get_driver_history("DbTest")
    assert [h["frames_used"] for h in history] == [20, 25]          # newest first


def test_sync_fills_in_drivers_enrolled_before_the_columns_existed():
    from backend import app as app_module
    db = app_module.database
    db.sync_driver_samples({"SyncOnly": 10})
    row = next(d for d in db.get_driver_records() if d["name"] == "SyncOnly")
    assert row["face_samples"] == 10 and row["last_enrolled_at"]
    assert len(db.get_driver_history("SyncOnly")) == 1
    db.sync_driver_samples({"SyncOnly": 10})                         # running it again adds nothing
    assert len(db.get_driver_history("SyncOnly")) == 1
