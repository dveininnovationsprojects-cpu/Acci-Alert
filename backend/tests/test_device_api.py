"""Tests for the ESP32 Wi-Fi telemetry API (POST /api/v1/telemetry).

Uses a temporary database and a fixed API key, so real data is never touched.
"""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as app_module  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
import device_api  # noqa: E402

KEY = "test-key-123"

GOOD = {
    "device_id": "ACCIALERT_ESP32_01",
    "timestamp": "2026-11-14T11:00:00+05:30",
    "sensors": {
        "alcohol_adc": 1250,
        "distance_cm": 45.6,
        "accelerometer": {"x": 0.12, "y": 0.08, "z": 9.72},
        "gyroscope": {"x": 0.02, "y": 0.01, "z": 0.03},
        "speed_rpm": 85,
        "ir_pulse_count": 20,
        "gps": {"latitude": 12.841901, "longitude": 80.224845, "fix_valid": False, "satellites": 0},
    },
    "alerts": {"alcohol": False, "overspeed": False, "accident": False, "obstacle": False, "buzzer": False},
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv(config.DEVICE_API_KEY_ENV, KEY)
    monkeypatch.setattr(database, "_last_device_purge", 0.0)
    database.init_db()
    monkeypatch.setattr(app_module.state, "mode", "simulation")
    return app_module.app.test_client()


def post(client, body=None, key=KEY, headers=None, **kw):
    h = {"X-API-Key": key} if key else {}
    h.update(headers or {})
    return client.post("/api/v1/telemetry", json=copy.deepcopy(body if body is not None else GOOD), headers=h, **kw)


def mutate(path, value):
    body = copy.deepcopy(GOOD)
    cur = body
    parts = path.split(".")
    for p in parts[:-1]:
        cur = cur[p]
    if value is DELETE:
        del cur[parts[-1]]
    else:
        cur[parts[-1]] = value
    return body


DELETE = object()


# success

def test_valid_reading_returns_201_and_is_stored(client):
    r = post(client)
    assert r.status_code == 201
    data = r.get_json()
    assert data["status"] == "success" and data["stored"] is True and data["id"] >= 1
    assert data["device_id"] == "ACCIALERT_ESP32_01"
    rows = database.get_device_telemetry()
    assert len(rows) == 1
    row = rows[0]
    assert row["alcohol_adc"] == 1250 and row["distance_cm"] == 45.6
    assert row["accel_z"] == 9.72 and row["gyro_x"] == 0.02
    assert row["speed_rpm"] == 85 and row["ir_pulse_count"] == 20
    assert row["gps_fix"] == 0 and row["gps_satellites"] == 0
    assert row["device_timestamp"] == "2026-11-14T11:00:00+05:30" and row["received_at"]
    assert row["buzzer"] == 0


def test_no_fix_gives_warning_not_error(client):
    data = post(client).get_json()
    assert any("fix_valid is false" in w for w in data["warnings"])


def test_alert_flags_are_stored(client):
    body = mutate("alerts.accident", True)
    body["alerts"]["buzzer"] = True
    assert post(client, body).status_code == 201
    row = database.get_device_telemetry()[0]
    assert row["alert_accident"] == 1 and row["buzzer"] == 1 and row["alert_alcohol"] == 0


def test_reading_reaches_dashboard_and_switches_mode(client):
    r = post(client)
    assert r.get_json()["dashboard_updated"] is True
    assert app_module.state.mode == "serial"
    t = app_module.state.latest_telemetry
    assert t["source"] == "ESP32_WIFI"
    assert t["spd"] == pytest.approx(85 * config.WHEEL_CIRCUMFERENCE_M * 60 / 1000, abs=0.1)
    assert t["az"] == pytest.approx(9.72 / 9.80665, abs=0.01)
    assert t["fix"] == 0
    assert t["alc"] == round(1250 * 1023 / config.DEVICE_ADC_MAX)  # scaled to the 0-1023 alarm scale


def test_device_accident_flag_raises_dashboard_alert(client):
    r = post(client, mutate("alerts.accident", True))
    assert "ACCIDENT" in r.get_json()["server_alerts"]
    assert app_module.state.latest_telemetry["state"] == "EMERGENCY"


def test_normal_reading_raises_no_accident_alert(client):
    r = post(client)
    assert "ACCIDENT" not in r.get_json()["server_alerts"]


def test_whole_number_float_accepted_for_integer_field(client):
    assert post(client, mutate("sensors.alcohol_adc", 1250.0)).status_code == 201


# authentication

@pytest.mark.parametrize("key", [None, "", "wrong-key"])
def test_missing_or_wrong_key_is_401_and_nothing_stored(client, key):
    r = post(client, key=key)
    assert r.status_code == 401 and r.get_json()["code"] == "unauthorized"
    assert database.get_device_telemetry() == []


def test_bearer_token_accepted(client):
    r = post(client, key=None, headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 201


def test_list_requires_key(client):
    assert client.get("/api/v1/telemetry").status_code == 401
    post(client)
    r = client.get("/api/v1/telemetry?limit=5", headers={"X-API-Key": KEY})
    assert r.status_code == 200 and r.get_json()["count"] == 1


def test_ping_needs_no_key(client):
    r = client.get("/api/v1/ping")
    assert r.status_code == 200 and r.get_json()["status"] == "ok"


# validation

REQUIRED = [
    "device_id", "timestamp", "sensors", "alerts",
    "sensors.alcohol_adc", "sensors.distance_cm", "sensors.accelerometer", "sensors.gyroscope",
    "sensors.speed_rpm", "sensors.ir_pulse_count", "sensors.gps",
    "sensors.accelerometer.x", "sensors.accelerometer.y", "sensors.accelerometer.z",
    "sensors.gyroscope.x", "sensors.gyroscope.y", "sensors.gyroscope.z",
    "sensors.gps.latitude", "sensors.gps.longitude", "sensors.gps.fix_valid", "sensors.gps.satellites",
    "alerts.alcohol", "alerts.overspeed", "alerts.accident", "alerts.obstacle", "alerts.buzzer",
]


@pytest.mark.parametrize("path", REQUIRED)
def test_every_required_field_is_enforced(client, path):
    r = post(client, mutate(path, DELETE))
    assert r.status_code == 422, path
    assert database.get_device_telemetry() == []
    assert any(e["field"].startswith(path) or path.startswith(e["field"]) for e in r.get_json()["errors"])


BAD_VALUES = [
    ("sensors.alcohol_adc", -1), ("sensors.alcohol_adc", 5000), ("sensors.alcohol_adc", "abc"),
    ("sensors.alcohol_adc", 12.5), ("sensors.alcohol_adc", True), ("sensors.alcohol_adc", None),
    ("sensors.distance_cm", -3), ("sensors.distance_cm", 9999), ("sensors.distance_cm", "far"),
    ("sensors.accelerometer.z", 999), ("sensors.gyroscope.y", -5000),
    ("sensors.speed_rpm", -1), ("sensors.speed_rpm", 1e9),
    ("sensors.ir_pulse_count", -5), ("sensors.ir_pulse_count", 1.5),
    ("sensors.gps.latitude", 91), ("sensors.gps.latitude", "north"),
    ("sensors.gps.longitude", -181),
    ("sensors.gps.fix_valid", "yes"), ("sensors.gps.fix_valid", 1),
    ("sensors.gps.satellites", -1), ("sensors.gps.satellites", 65),
    ("alerts.buzzer", "on"), ("alerts.accident", 1),
    ("device_id", ""), ("device_id", "bad id!"), ("device_id", 5), ("device_id", "x" * 65),
    ("timestamp", "yesterday"), ("timestamp", 12345), ("timestamp", ""),
    ("sensors", "text"), ("alerts", []),
]


@pytest.mark.parametrize("path,value", BAD_VALUES)
def test_bad_values_are_rejected_with_422(client, path, value):
    r = post(client, mutate(path, value))
    assert r.status_code == 422, (path, value)
    body = r.get_json()
    assert body["code"] == "validation_failed" and body["errors"]
    assert database.get_device_telemetry() == []


def test_nan_and_infinity_rejected(client):
    for bad in ("NaN", "Infinity", "-Infinity"):
        raw = '{"device_id":"A","timestamp":"2026-11-14T11:00:00Z","sensors":{"alcohol_adc":1,"distance_cm":%s}}' % bad
        r = client.post("/api/v1/telemetry", data=raw, headers={"X-API-Key": KEY, "Content-Type": "application/json"})
        assert r.status_code in (400, 422)
    assert database.get_device_telemetry() == []


def test_all_errors_reported_together(client):
    body = mutate("sensors.alcohol_adc", -1)
    body["sensors"]["distance_cm"] = "x"
    del body["alerts"]["buzzer"]
    errs = post(client, body).get_json()["errors"]
    assert {e["field"] for e in errs} >= {"sensors.alcohol_adc", "sensors.distance_cm", "alerts.buzzer"}


def test_z_suffix_timestamp_accepted(client):
    assert post(client, mutate("timestamp", "2026-11-14T05:30:00Z")).status_code == 201


def test_extra_fields_are_ignored(client):
    body = copy.deepcopy(GOOD)
    body["firmware"] = "1.2.3"
    body["sensors"]["temp_c"] = 31
    assert post(client, body).status_code == 201


# transport errors

def test_wrong_content_type_is_415(client):
    r = client.post("/api/v1/telemetry", data="x=1", headers={"X-API-Key": KEY, "Content-Type": "text/plain"})
    assert r.status_code == 415


def test_invalid_json_is_400(client):
    r = client.post("/api/v1/telemetry", data="{not json", headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    assert r.status_code == 400 and r.get_json()["code"] == "invalid_json"


def test_empty_body_is_400(client):
    r = client.post("/api/v1/telemetry", data="", headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    assert r.status_code == 400


def test_json_array_body_is_422(client):
    r = client.post("/api/v1/telemetry", data="[1,2]", headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    assert r.status_code == 422


def test_oversized_body_is_413(client):
    big = '{"device_id":"%s"}' % ("a" * (config.DEVICE_MAX_PAYLOAD_BYTES + 10))
    r = client.post("/api/v1/telemetry", data=big, headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_get_on_post_route_is_405_json_free_of_traces(client):
    r = client.put("/api/v1/telemetry", headers={"X-API-Key": KEY})
    assert r.status_code == 405
    assert "Traceback" not in r.get_data(as_text=True)


def test_storage_failure_is_500_and_clear(client, monkeypatch):
    def boom(row):
        raise RuntimeError("disk full")
    monkeypatch.setattr(database, "add_device_telemetry", boom)
    r = post(client)
    assert r.status_code == 500 and r.get_json()["code"] == "storage_error"


def test_dashboard_failure_does_not_lose_the_reading(client, monkeypatch):
    def boom(packet, source="x"):
        raise RuntimeError("pipeline broke")
    monkeypatch.setattr(app_module, "process_telemetry_packet", boom)
    r = post(client)
    assert r.status_code == 201
    assert r.get_json()["dashboard_updated"] is False
    assert len(database.get_device_telemetry()) == 1


# storage housekeeping

def test_old_readings_are_purged(client):
    import sqlite3
    post(client)
    conn = sqlite3.connect(database.DATABASE_PATH)
    conn.execute("UPDATE device_telemetry SET received_at = '2000-01-01 00:00:00'")
    conn.commit(); conn.close()
    database._last_device_purge = 0.0
    assert database.purge_device_telemetry(24) == 1
    assert database.get_device_telemetry() == []


def test_limit_parameter_validated(client):
    assert client.get("/api/v1/telemetry?limit=abc", headers={"X-API-Key": KEY}).status_code == 400


def test_key_file_created_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv(config.DEVICE_API_KEY_ENV, raising=False)
    monkeypatch.setattr(config, "DEVICE_API_KEY_FILE", tmp_path / "k.txt")
    k1 = device_api.get_api_key()
    assert len(k1) >= 20 and (tmp_path / "k.txt").read_text().strip() == k1
    assert device_api.get_api_key() == k1


# GPS fix handling (zone checks)

def _at_school_zone(fix):
    body = mutate("sensors.gps.latitude", 13.0827)
    body["sensors"]["gps"]["longitude"] = 80.2707
    body["sensors"]["gps"]["fix_valid"] = fix
    body["sensors"]["gps"]["satellites"] = 6 if fix else 0
    body["sensors"]["speed_rpm"] = 400  # about 4.9 km/h, below every zone limit
    return body


def test_inside_zone_with_gps_fix_raises_zone_alert(client):
    r = post(client, _at_school_zone(True))
    assert "DANGER ZONE" in r.get_json()["server_alerts"]


def test_inside_zone_without_gps_fix_raises_no_zone_alert(client):
    r = post(client, _at_school_zone(False))
    assert not [a for a in r.get_json()["server_alerts"] if "ZONE" in a]
