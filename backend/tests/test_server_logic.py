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
    monkeypatch.setattr(app_module.state, "mode", "serial")
    monkeypatch.setattr(app_module.state, "last_telemetry_saved", 0.0)
    monkeypatch.setattr(app_module, "zone_engine", app_module.ZoneEngine(zone_db.list_zones(app_module.zone_conn)))

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
    monkeypatch.setattr(app_module.state, "mode", "serial")
    monkeypatch.setattr(app_module.state, "last_telemetry_saved", 0.0)
    before = app_module.zone_conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    for _ in range(5):
        app_module.process_telemetry_packet({"spd": 30, "lat": 13.1, "lon": 80.3, "fix": 1}, source="TEST")
    after = app_module.zone_conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    assert after - before == 1       # one row for the five packets


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


# ---------------------------------------------------------------- dashboard database upkeep
def test_event_table_has_indexes_and_old_spelling_is_fixed():
    from backend import app as app_module
    db = app_module.database
    conn = db.get_db_connection()
    conn.execute("INSERT INTO events (event_type, timestamp, driver_name) VALUES ('UNAUTHORIZED DRIVER', '2026-01-01 10:00:00', 'X')")
    conn.commit()
    conn.close()

    db.init_db()                                   # runs at every server start

    conn = db.get_db_connection()
    indexes = {r["name"] for r in conn.execute("PRAGMA index_list(events)")}
    leftover = conn.execute("SELECT COUNT(*) FROM events WHERE event_type = 'UNAUTHORIZED DRIVER'").fetchone()[0]
    renamed = conn.execute("SELECT COUNT(*) FROM events WHERE event_type = 'UNAUTHORISED DRIVER' AND timestamp = '2026-01-01 10:00:00'").fetchone()[0]
    conn.close()
    assert {"idx_events_timestamp", "idx_events_type", "idx_events_driver"} <= indexes
    assert leftover == 0 and renamed == 1


def test_zone_manager_accepts_every_zone_type_it_offers():
    import re
    page = (Path(__file__).resolve().parents[2] / "frontend" / "zones.html").read_text(encoding="utf-8")
    offered = re.findall(r'<option value="(\w+)">', page.split('name="type"')[1].split("</select>")[0])
    assert "hospital" in offered
    for zone_type in offered:
        zone = {"id": "T1", "name": "x", "type": zone_type, "lat": 13.0, "lon": 80.0,
                "radius_m": 20, "speed_limit": 5, "severity": "low", "active": 1}
        assert zone_db.validate_zone(zone)["type"] == zone_type


# ---------------------------------------------------------------- one zone list for everything
def test_dashboard_and_zone_manager_show_the_same_zones():
    from backend import app as app_module
    client = app_module.app.test_client()
    dashboard = client.get("/api/zones").get_json()["zones"]
    manager = client.get("/zm/api/zones").get_json()
    assert {z["code"] for z in dashboard} == {z["id"] for z in manager if z["active"]}
    names = {z["name"] for z in manager}
    assert {"Anna Salai School Zone", "Guindy Sharp Curve", "Koyambedu Junction", "Marina Beach Promenade"} <= names
    assert len(dashboard) >= 10                                  # your 6 zones plus the 4 route zones
    assert all(isinstance(z["id"], int) for z in dashboard)      # the dashboard needs numeric ids


def test_zone_added_in_the_dashboard_reaches_the_manager_and_the_engine():
    from backend import app as app_module
    client = app_module.app.test_client()
    payload = {"code": "QA1", "name": "QA school", "latitude": 13.0001, "longitude": 80.0001,
               "radius_meters": 90, "speed_limit": 15, "severity": "HIGH"}

    saved = client.post("/api/zones", json=payload).get_json()
    assert saved["status"] == "success"
    assert "QA1" in {z["id"] for z in client.get("/zm/api/zones").get_json()}
    assert "QA1" in {z["id"] for z in app_module.zone_engine.zones} if hasattr(app_module.zone_engine, "zones") else True
    assert any(z["code"] == "QA1" for z in app_module.dashboard_zone_cache)

    payload["name"] = "QA school (edited)"
    again = client.post("/api/zones", json=payload).get_json()
    assert again["id"] == saved["id"]                            # an edit keeps the same dashboard number

    assert client.delete(f"/api/zones/{saved['id']}").get_json()["status"] == "success"
    assert "QA1" not in {z["id"] for z in client.get("/zm/api/zones").get_json()}
    assert not any(z["code"] == "QA1" for z in app_module.dashboard_zone_cache)
    assert client.delete(f"/api/zones/{saved['id']}").status_code == 404


def test_zone_added_in_the_manager_reaches_the_dashboard():
    from backend import app as app_module
    client = app_module.app.test_client()
    zone = {"id": "QA2", "name": "QA hospital", "type": "hospital", "lat": 13.0002, "lon": 80.0002,
            "radius_m": 40, "speed_limit": 5, "severity": "high", "active": 1}
    assert client.post("/zm/api/zones", json=zone).status_code == 201
    shown = {z["code"]: z for z in client.get("/api/zones").get_json()["zones"]}
    assert shown["QA2"]["severity"] == "HIGH" and shown["QA2"]["radius_meters"] == 40
    assert any(z["code"] == "QA2" for z in app_module.dashboard_zone_cache)

    zone["active"] = 0                                           # switched off: no longer in force
    client.put("/zm/api/zones/QA2", json=zone)
    assert "QA2" not in {z["code"] for z in client.get("/api/zones").get_json()["zones"]}
    client.delete("/zm/api/zones/QA2")


def test_bad_zone_from_the_dashboard_is_refused():
    from backend import app as app_module
    reply = app_module.app.test_client().post("/api/zones", json={
        "code": "BAD", "name": "x", "latitude": 999, "longitude": 80.0, "radius_meters": 50, "speed_limit": 10})
    assert reply.status_code == 400


def test_old_dashboard_zones_are_imported_only_once():
    from backend import app as app_module
    zdb = app_module.zone_db
    zdb.delete_zone(app_module.zone_conn, "Z08")
    app_module.import_dashboard_zones_once()                     # already done at start: must not bring Z08 back
    assert zdb.get_zone(app_module.zone_conn, "Z08") is None
    app_module.zones_changed()


# ---------------------------------------------------------------- event history: filters, paging, repeated alerts
def add_test_events(db):
    db.add_event("OVERSPEED", "ZqEvAlpha", 70.0, 13.0, 80.0, "None", "going fast on the bypass", timestamp="2026-02-01 09:00:00")
    db.add_event("ACCIDENT", "ZqEvAlpha", 5.0, 13.0, 80.0, "None", "hard impact", timestamp="2026-02-02 09:00:00")
    db.add_event("OVERSPEED", "ZqEvBeta", 80.0, 13.0, 80.0, "None", "second overspeed", timestamp="2026-02-03 09:00:00")


def test_event_filters_paging_and_limits_work():
    from backend import app as app_module
    add_test_events(app_module.database)
    client = app_module.app.test_client()

    def events(query):
        return client.get("/api/events?" + query).get_json()["data"]

    assert {e["event_type"] for e in events("type=OVERSPEED&search=ZqEvAlpha&limit=50")["events"]} == {"OVERSPEED"}
    assert events("search=ZqEvAlpha&type=OVERSPEED")["total"] == 1
    window = events("start=2026-02-02&end=2026-02-02&search=ZqEv")
    assert [e["event_type"] for e in window["events"]] == ["ACCIDENT"]
    assert events("search=bypass")["total"] == 1                      # searches the details text too

    page = events("search=ZqEv&limit=2&offset=0")
    assert len(page["events"]) == 2 and page["total"] == 3
    assert len(events("search=ZqEv&limit=2&offset=2")["events"]) == 1
    assert len(events("limit=100000")["events"]) <= 500                # the page size is capped
    assert client.get("/api/events?limit=abc").status_code == 400
    assert events("start=not-a-date&search=ZqEvAlpha")["total"] == 2    # a bad date is ignored, not an error


def test_csv_export_uses_the_same_filters():
    from backend import app as app_module
    add_test_events(app_module.database)
    body = app_module.app.test_client().get("/api/events/export?type=ACCIDENT&search=ZqEvAlpha").data.decode()
    assert "hard impact" in body and "going fast on the bypass" not in body


def test_a_lasting_alert_is_logged_once_not_every_five_seconds(monkeypatch):
    from backend import app as app_module
    monkeypatch.setattr(app_module.socketio, "emit", lambda *a, **k: None)
    monkeypatch.setattr(app_module.state, "mode", "serial")
    monkeypatch.setattr(app_module.state, "active_alerts", {})
    monkeypatch.setattr(app_module.state, "last_logged_alerts", {})
    monkeypatch.setattr(app_module.state, "active_driver_name", "ThrottleTest")
    monkeypatch.setattr(app_module.state, "active_driver_auth", 1)

    def tick(seconds, auth):
        app_module.evaluate_and_dispatch_alerts(
            {"spd": 12, "alc": 0, "dist": 999, "tilt": 0, "auth": auth, "lat": 1.0, "lon": 1.0}, now=1000.0 + seconds)

    def logged():
        return app_module.database.get_events(limit=50, search="ThrottleTest")["total"]

    tick(0, auth=0)                                                    # starts: logged
    for second in (6, 12, 30, 59):
        tick(second, auth=0)                                           # still going on: not logged again
    assert logged() == 1
    tick(61, auth=0)                                                   # a minute later: logged again
    assert logged() == 2
    tick(62, auth=1)                                                   # the alert clears
    tick(70, auth=0)                                                   # and comes back: a new entry
    assert logged() == 3


def test_zone_manager_header_only_links_back_to_the_dashboard():
    page = (Path(__file__).resolve().parents[2] / "frontend" / "zones.html").read_text(encoding="utf-8")
    header = page.split("<header>")[1].split("</header>")[0]
    assert 'id="backLink"' in header and "closeZoneManager" in header   # inside the dashboard it closes the overlay
    assert "/dashboard" not in header                                   # a link here would load a page inside the frame


def test_accelerometer_values_are_saved_with_telemetry(monkeypatch):
    from backend import app as app_module

    monkeypatch.setattr(app_module.socketio, "emit", lambda *a, **k: None)
    monkeypatch.setattr(app_module.state, "mode", "serial")
    time.sleep(1.2)
    monkeypatch.setattr(app_module.state, "last_telemetry_saved", 0.0)
    app_module.process_telemetry_packet({"spd": 12, "lat": 13.1, "lon": 80.3, "fix": 1,
                                         "ax": 0.25, "ay": -0.5, "az": 0.98}, source="TEST")
    row = app_module.zone_conn.execute("SELECT ax, ay, az FROM telemetry ORDER BY id DESC LIMIT 1").fetchone()
    assert (row["ax"], row["ay"], row["az"]) == (0.25, -0.5, 0.98)


def test_old_telemetry_table_gets_accelerometer_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, speed REAL, "
                "alcohol REAL, distance REAL, tilt REAL, lat REAL, lon REAL, state TEXT, zone_id TEXT)")
    old.commit()
    old.close()
    conn = zone_db.connect(path)
    zone_db.log_telemetry(conn, speed=5, ax=0.1, ay=0.2, az=0.3)
    assert conn.execute("SELECT az FROM telemetry").fetchone()["az"] == 0.3
    conn.close()


def test_login_event_uses_the_latest_car_position(monkeypatch):
    from backend import app as app_module

    monkeypatch.setattr(app_module.state, "latest_telemetry", {"spd": 41.5, "lat": 12.5, "lon": 79.5})
    assert app_module.vehicle_position() == (41.5, 12.5, 79.5)
    monkeypatch.setattr(app_module.state, "latest_telemetry", None)
    assert app_module.vehicle_position()[0] == 0.0


def test_unused_liveness_stub_is_gone():
    from backend import app as app_module
    assert app_module.app.test_client().post("/api/liveness").status_code == 404
