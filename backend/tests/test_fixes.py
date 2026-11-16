"""Tests for the safety fixes: command checks, safe telemetry, severity, audit log, calibration, no leaks.

The tests use the temporary data folder from conftest.py, never the real database.
"""

import math
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as A  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402


@pytest.fixture(scope="module")
def quiet_server():
    """No live-feed side effects for the whole module."""
    patch = pytest.MonkeyPatch()
    patch.setattr(A.socketio, "emit", lambda *a, **k: None)
    patch.setattr(A.state, "mode", "serial")
    yield
    patch.undo()


@pytest.fixture()
def client(quiet_server):
    database.init_db()
    database.clear_events()                                  # test database only (see conftest.py)
    A.state.active_alerts = {}
    A.state.last_logged_alerts = {}
    saved = (A.state.active_driver_name, A.state.active_driver_auth)
    yield A.app.test_client()
    A.state.active_driver_name, A.state.active_driver_auth = saved


def post(client, path, body=None, **kw):
    return client.post(path, json=body, **kw)


# ---------------------------------------------------------------- commands
@pytest.mark.parametrize("cmd", ["STOP", "RESET", "ACK", "stop"])
def test_allowed_commands_without_value(client, cmd):
    assert post(client, "/api/command", {"command": cmd}).status_code == 200


@pytest.mark.parametrize("body", [
    {"command": "UNKNOWN_COMMAND"}, {"command": "SIGNAL", "value": "RED"}, {"command": "ZONES"}, {"command": ""},
    {"command": "AUTH"}, {"command": "AUTH", "value": 5}, {"command": "AUTH", "value": "yes"},
    {"command": "LIMIT"}, {"command": "LIMIT", "value": -1}, {"command": "LIMIT", "value": 999},
    {"command": "LIMIT", "value": "fast"},
])
def test_bad_commands_are_400_and_not_run(client, body):
    before = len(client.get("/api/serial/history").get_json()["history"])
    r = post(client, "/api/command", body)
    assert r.status_code == 400 and r.get_json()["message"]
    assert len(client.get("/api/serial/history").get_json()["history"]) == before


def test_serial_send_uses_the_same_check(client):
    assert post(client, "/api/serial/send", {"command": "UNKNOWN_COMMAND"}).status_code == 400
    assert post(client, "/api/serial/send", {"command": "LIMIT", "value": 25}).status_code == 200


def test_valid_limit_value_is_applied(client):
    assert post(client, "/api/command", {"command": "LIMIT", "value": "35"}).get_json()["value"] == 35.0


def test_toggle_auth_updates_the_lock_state_the_alerts_read(client):
    post(client, "/api/command", {"command": "AUTH", "value": 0})
    assert A.state.active_driver_auth == 0
    assert client.get("/api/status").get_json()["active_driver_authorized"] == 0
    A.process_telemetry_packet({"spd": 0, "auth": 1}, source="TEST")
    assert "UNAUTHORISED DRIVER" not in A.state.active_alerts       # locked but standing still: nobody is driving
    A.process_telemetry_packet({"spd": 12, "auth": 1}, source="TEST")
    assert "UNAUTHORISED DRIVER" in A.state.active_alerts           # still locked on the server, and moving
    post(client, "/api/command", {"command": "AUTH", "value": 1})
    assert A.state.active_driver_auth == 1
    A.process_telemetry_packet({"spd": 12, "auth": 1}, source="TEST")
    assert "UNAUTHORISED DRIVER" not in A.state.active_alerts       # unlock clears the red banner


# ---------------------------------------------------------------- drivers
def test_select_unknown_driver_is_404_and_changes_nothing(client):
    A.state.active_driver_name, A.state.active_driver_auth = "Unknown", 0
    r = post(client, "/api/drivers/select", {"name": "Not Registered", "authorized": 1})
    assert r.status_code == 404
    assert A.state.active_driver_name == "Unknown" and A.state.active_driver_auth == 0


def test_select_registered_driver_works(client):
    name = database.get_all_drivers()[0]["name"]
    database.add_or_update_driver(name, None, 1, "")
    r = post(client, "/api/drivers/select", {"name": name.upper(), "authorized": 1})
    assert r.status_code == 200 and A.state.active_driver_name == name and A.state.active_driver_auth == 1


def test_unauthorised_registered_driver_cannot_be_selected_as_authorised(client):
    database.add_or_update_driver("Blocked Person", "BLK-1", 0, "")
    r = post(client, "/api/drivers/select", {"name": "Blocked Person", "authorized": 1})
    assert r.status_code == 200 and r.get_json()["authorized"] == 0


def test_select_requires_a_name(client):
    assert post(client, "/api/drivers/select", {}).status_code == 400


# ---------------------------------------------------------------- calibration
@pytest.mark.parametrize("engine,lo,hi,good", [("sface", 0.20, 0.80, 0.55), ("lbph", 10.0, 70.0, 42.0)])
def test_threshold_is_checked_and_applied_to_the_active_recogniser(client, monkeypatch, engine, lo, hi, good):
    monkeypatch.setattr(A, "use_sface", lambda: engine == "sface")
    old = (A.face_sface.threshold, A.face_recognizer.threshold, config.MATCH_THRESHOLD)
    try:
        for bad in (hi + 1, lo - 0.01, "abc", None, float("nan"), True):
            assert post(client, "/api/config", {"threshold": bad}).status_code == 400, bad
        r = post(client, "/api/config", {"threshold": good})
        assert r.status_code == 200 and r.get_json()["threshold"] == good and r.get_json()["engine"] == engine
        live = A.face_sface.threshold if engine == "sface" else A.face_recognizer.threshold
        assert live == good                                          # really applied, not just stored
        slider = client.get("/api/config").get_json()["slider"]
        assert slider["min"] == lo and slider["max"] == hi and slider["hint"]
    finally:
        A.face_sface.threshold, A.face_recognizer.threshold, config.MATCH_THRESHOLD = old


def test_liveness_flag_must_be_a_boolean(client):
    was = config.ENABLE_LIVENESS_BY_DEFAULT
    try:
        assert post(client, "/api/config", {"liveness": "yes"}).status_code == 400
        assert post(client, "/api/config", {"liveness": False}).status_code == 200
        assert config.ENABLE_LIVENESS_BY_DEFAULT is False
    finally:
        config.ENABLE_LIVENESS_BY_DEFAULT = was


def test_config_body_must_be_json_object(client):
    assert client.post("/api/config", data="[1]", content_type="application/json").status_code == 400


# ---------------------------------------------------------------- audit log + counters
def _face_rows():
    database.add_event("AUTH_OK", "Arun", details="Match 91%, liveness passed")
    database.add_event("UNAUTHORISED DRIVER", "Unknown", details="Match 22%, liveness pending")
    database.add_event("UNAUTHORISED", "Unknown", details="from the camera page")
    database.add_event("ACCIDENT", "Arun", details="Critical impact")                                       # not a face event
    database.add_event("UNAUTHORISED DRIVER", "Unknown", details="Vehicle operated by unauthorized subject")  # alert, not face


def test_audit_log_lists_only_face_login_events_with_real_values(client):
    _face_rows()
    logs = client.get("/api/logs").get_json()["logs"]
    assert len(logs) == 3 and {l["type"] for l in logs} == {"AUTH_OK", "UNAUTHORISED"}
    by = {(l["type"], l["details"]): l for l in logs}
    ok = by[("AUTH_OK", "Match 91%, liveness passed")]
    assert ok["match_score"] == 91.0 and ok["liveness_result"] == "PASSED" and ok["severity"] == "INFO"
    bad = by[("UNAUTHORISED", "Match 22%, liveness pending")]
    assert bad["match_score"] == 22.0 and bad["liveness_result"] == "PENDING"
    unknown = by[("UNAUTHORISED", "from the camera page")]
    assert unknown["match_score"] is None and unknown["liveness_result"] is None   # no invented numbers


def test_counters_count_only_face_login_events(client):
    _face_rows()
    s = client.get("/api/logs/stats").get_json()
    assert (s["total_verifications"], s["authorised_unlocks"], s["security_rejections"]) == (3, 1, 2)


def test_counters_with_no_events(client):
    s = client.get("/api/logs/stats").get_json()
    assert (s["total_verifications"], s["authorised_unlocks"], s["security_rejections"]) == (0, 0, 0)
    assert s["last_telemetry_event"] == "--"


def test_logs_limit_is_checked(client):
    assert client.get("/api/logs?limit=abc").status_code == 400


def test_clearing_logs_leaves_a_trace(client):
    _face_rows()
    assert client.delete("/api/logs").status_code == 200
    events = database.get_events(limit=50)["events"]
    assert [e["event_type"] for e in events] == ["LOGS_CLEARED"] and "5 event" in events[0]["details"]


# ---------------------------------------------------------------- severity
@pytest.mark.parametrize("etype,sev", [("ACCIDENT", "CRITICAL"), ("OVERSPEED", "WARNING"), ("AUTH_OK", "INFO"),
                                       ("ZONE_ENTER", "MEDIUM"), ("SOMETHING_NEW", "INFO"), ("accident", "CRITICAL")])
def test_default_severity_by_event_type(client, etype, sev):
    database.add_event(etype, "x")
    assert database.get_events(limit=1)["events"][0]["severity"] == sev


def test_explicit_severity_wins(client):
    database.add_event("ACCIDENT", "x", severity="low")
    assert database.get_events(limit=1)["events"][0]["severity"] == "LOW"


def test_alert_events_store_the_alert_severity(client):
    A.process_telemetry_packet({"spd": 0, "tilt": 150, "auth": 1}, source="TEST")
    ev = [e for e in database.get_events(limit=20)["events"] if e["event_type"] == "ACCIDENT"][0]
    assert ev["severity"] == "CRITICAL"


def test_events_api_filters_by_severity(client):
    database.add_event("ACCIDENT", "x")
    database.add_event("OVERSPEED", "x")
    database.add_event("AUTH_OK", "x")
    got = client.get("/api/events?severity=critical&limit=50").get_json()["data"]
    assert got["total"] == 1 and got["events"][0]["event_type"] == "ACCIDENT"
    assert client.get("/api/events?severity=ALL&limit=50").get_json()["data"]["total"] == 3
    assert client.get("/api/events?severity=critical&type=OVERSPEED").get_json()["data"]["total"] == 0


def test_csv_has_a_severity_column_and_filter(client):
    database.add_event("ACCIDENT", "x")
    database.add_event("AUTH_OK", "x")
    text = client.get("/api/events/export?severity=CRITICAL").get_data(as_text=True)
    lines = text.strip().splitlines()
    assert "Severity" in lines[0] and len(lines) == 2 and "ACCIDENT" in lines[1] and "CRITICAL" in lines[1]


def test_old_database_without_severity_is_upgraded_and_filled(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, timestamp TEXT NOT NULL,
        driver_name TEXT, vehicle_speed REAL, latitude REAL, longitude REAL, danger_zone_name TEXT, details TEXT)""")
    conn.execute("INSERT INTO events (event_type, timestamp) VALUES ('ACCIDENT', '2026-01-01 00:00:00')")
    conn.execute("INSERT INTO events (event_type, timestamp) VALUES ('AUTH_OK', '2026-01-01 00:00:01')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(database, "DATABASE_PATH", path)
    database.init_db()
    database.init_db()                                                     # twice is harmless
    sev = {e["event_type"]: e["severity"] for e in database.get_events(limit=10)["events"]}
    assert sev == {"ACCIDENT": "CRITICAL", "AUTH_OK": "INFO"}


# ---------------------------------------------------------------- safe telemetry
@pytest.mark.parametrize("packet", [
    {"alc": "abc"}, {"spd": None}, {"spd": "fast", "alc": [1], "dist": {}}, {"lat": "north", "lon": float("nan")},
    {"tilt": float("inf")}, {"fix": "x"}, {"auth": "maybe"}, {"ax": True}, {}, {"alc": 12.9},
])
def test_bad_telemetry_values_do_not_crash(client, packet):
    A.process_telemetry_packet(packet, source="TEST")
    t = A.state.latest_telemetry
    for key in ("spd", "alc", "dist", "tilt", "fix"):
        assert isinstance(t[key], (int, float)) and math.isfinite(t[key]), key
    for key in ("lat", "lon"):                      # a position is a real number or unknown (None), never made up
        assert t[key] is None or (isinstance(t[key], (int, float)) and math.isfinite(t[key])), key


def test_bad_value_uses_a_safe_default_and_good_values_survive(client):
    A.process_telemetry_packet({"alc": "abc", "spd": 33.3, "dist": 80}, source="TEST")
    t = A.state.latest_telemetry
    assert t["alc"] == 0 and t["spd"] == 33.3 and t["dist"] == 80.0


def test_non_object_packet_is_ignored(client):
    before = A.state.latest_telemetry
    for junk in ("text", 5, None, [1, 2]):
        A.process_telemetry_packet(junk, source="TEST")
    assert A.state.latest_telemetry is before


def test_alert_logic_accepts_garbage_directly(client):
    A.evaluate_and_dispatch_alerts({"spd": "x", "alc": "y", "dist": None, "tilt": "z", "lat": "a", "lon": "b", "fix": "c"})


def test_accident_in_a_packet_with_one_bad_field_is_still_detected(client):
    A.process_telemetry_packet({"alc": "abc", "tilt": 150, "auth": 1}, source="TEST")
    assert "ACCIDENT" in A.state.active_alerts


# ---------------------------------------------------------------- traffic signal is gone
def test_red_signal_no_longer_raises_an_alert(client):
    A.process_telemetry_packet({"spd": 0, "signal": "RED", "auth": 1, "lat": 13.5, "lon": 80.5}, source="TEST")
    assert "RED SIGNAL" not in A.state.active_alerts
    assert "signal" not in A.state.latest_telemetry


# ---------------------------------------------------------------- status
def test_status_reports_how_old_the_last_reading_is(client):
    A.process_telemetry_packet({"spd": 1}, source="TEST")
    age = client.get("/api/status").get_json()["last_telemetry_age"]
    assert age is not None and 0 <= age < 5


# ---------------------------------------------------------------- database connections are always closed
def _next_write_is_fast():
    started = time.time()
    database.add_event("PROBE", "x")
    return time.time() - started < 2


def test_failed_writes_do_not_block_the_next_write(client):
    keep = []
    for fn in (lambda: database.log_command(None),
               lambda: database.add_or_update_driver(None, None),
               lambda: (database.add_zone("LEAKX", "n", 1, 1, 1, 1), database.add_zone("LEAKX", "n", 1, 1, 1, 1)),
               lambda: database.add_event("X", "d", "abc")):
        try:
            fn()
        except Exception as err:      # keep the error alive, like a logged traceback would
            keep.append(err)
        assert _next_write_is_fast()
    assert len(keep) == 4
    for z in database.get_all_zones():
        if z["code"] == "LEAKX":
            database.delete_zone(z["id"])


# ---------------------------------------------------------------- no mock data, laptop location when the GPS has no fix
def test_the_simulator_is_gone(client):
    assert not (config.BACKEND_DIR / "simulator.py").exists()
    assert not hasattr(A, "simulator")
    assert client.post("/api/simulation/scenario", json={"scenario": "accident"}).status_code == 404


def test_nothing_is_invented_before_the_first_reading(client, monkeypatch):
    monkeypatch.setattr(A.state, "latest_telemetry", None)
    assert A.vehicle_position() == (0.0, None, None)
    assert client.get("/api/telemetry/latest").get_json()["status"] == "empty"


def laptop(client, lat=13.05, lon=80.25, accuracy=35):
    return client.post("/api/laptop-location", json={"lat": lat, "lon": lon, "accuracy_m": accuracy})


def reading(**kw):
    packet = {"spd": 0, "alc": 0, "dist": 100, "tilt": 0, "auth": 1, "lat": 12.84, "lon": 80.22, "fix": 1}
    packet.update(kw)
    A.process_telemetry_packet(packet, source="TEST")
    return A.state.latest_telemetry


def test_the_gps_module_is_the_main_position(client):
    laptop(client)
    t = reading()
    assert (t["lat"], t["lon"], t["position_source"]) == (12.84, 80.22, "GPS")


def test_without_a_gps_fix_the_laptop_location_is_shown(client):
    laptop(client, 13.05, 80.25, 35)
    t = reading(fix=0)
    assert (t["lat"], t["lon"], t["position_source"], t["position_accuracy_m"]) == (13.05, 80.25, "LAPTOP", 35.0)
    assert t["fix"] == 1                                    # accurate enough for the danger zones


def test_a_coarse_laptop_location_is_shown_but_not_used_for_zones(client):
    laptop(client, 13.05, 80.25, 3000)
    t = reading(fix=0)
    assert t["position_source"] == "LAPTOP" and t["fix"] == 0


def test_a_gps_module_that_sends_zero_zero_counts_as_no_fix(client):
    laptop(client)
    assert reading(lat=0, lon=0, fix=1)["position_source"] == "LAPTOP"


def test_with_no_gps_and_no_laptop_location_the_position_is_unknown(client, monkeypatch):
    monkeypatch.setattr(A.state, "laptop_location", None)
    t = reading(fix=0)
    assert (t["lat"], t["lon"], t["position_source"], t["fix"]) == (None, None, "NONE", 0)
    assert A.vehicle_position()[1:] == (None, None)         # events are written without a made-up position


def test_an_old_laptop_location_is_not_used(client, monkeypatch):
    laptop(client)
    A.state.laptop_location["epoch"] -= config.LAPTOP_LOCATION_MAX_AGE_S + 5
    assert reading(fix=0)["position_source"] == "NONE"


def test_the_gps_comes_back_as_soon_as_it_has_a_fix(client):
    laptop(client)
    assert reading(fix=0)["position_source"] == "LAPTOP"
    assert reading(fix=1)["position_source"] == "GPS"


def test_an_event_without_a_position_does_not_crash_the_alert_logic(client, monkeypatch):
    monkeypatch.setattr(A.state, "laptop_location", None)
    reading(fix=0, alc=900)                                 # alcohol alert while the position is unknown
    assert "ALCOHOL DETECTED" in A.state.active_alerts


@pytest.mark.parametrize("body", [{}, {"lat": "x", "lon": 1}, {"lat": 91, "lon": 1}, {"lat": 1, "lon": 181}, {"lat": 0, "lon": 0}])
def test_a_bad_laptop_location_is_refused(client, body):
    assert client.post("/api/laptop-location", json=body).status_code == 400


def test_laptop_location_can_be_read_back(client):
    laptop(client, 13.1, 80.3, 20)
    assert client.get("/api/laptop-location").get_json()["location"] == {"lat": 13.1, "lon": 80.3, "accuracy_m": 20.0}


def test_the_first_real_reading_switches_the_mode_from_waiting(client, monkeypatch):
    monkeypatch.setattr(A.state, "mode", "waiting")
    A.mark_live()
    assert A.state.mode == "serial"
