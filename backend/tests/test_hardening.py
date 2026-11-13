"""Tests for the security / integration hardening (bad input, GPS loss, threads, API abuse)."""
import csv
import io
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402
import zone_api  # noqa: E402
from zones import ZoneEngine, valid_position, validate_zone, zones_to_esp32_cmd  # noqa: E402

M_PER_DEG_LAT = 111194.9

ZONE = {"id": "Z1", "name": "School", "type": "school", "lat": 13.0, "lon": 80.0,
        "radius_m": 30, "speed_limit": 10, "severity": "high", "active": 1}


def north(lat, metres):
    return lat + metres / M_PER_DEG_LAT


def types(events):
    return [e["type"] for e in events]


# ------------------------------------------------- GPS loss / bad input
def test_gps_loss_inside_zone_holds_state_no_false_exit_or_double_enter():
    e = ZoneEngine([ZONE])
    assert types(e.update(13.0, 80.0, 5)) == ["ZONE_ENTER"]
    assert e.update(0.0, 0.0, 5, fix=0) == []        # ESP32 reports fix=0 with 0,0
    assert e.update(0.0, 0.0, 5) == []               # (0,0) alone is also "no fix"
    assert e.update(13.0, 80.0, 5, fix=0) == []      # fix flag wins over coordinates
    assert e.update(13.0, 80.0, 5) == []             # GPS back: no duplicate ENTER
    assert e.current_zone["id"] == "Z1"


@pytest.mark.parametrize("lat,lon", [
    (None, None), ("abc", "def"), (float("nan"), 80.0), (91.0, 80.0), (13.0, 181.0),
    (float("inf"), 80.0), ([], {}), (True, False),
])
def test_bad_coordinates_never_raise_and_give_no_events(lat, lon):
    e = ZoneEngine([ZONE])
    assert e.update(lat, lon, 5) == []


def test_bad_speed_treated_as_zero_not_crash():
    e = ZoneEngine([ZONE])
    assert types(e.update(13.0, 80.0, None)) == ["ZONE_ENTER"]   # no overspeed from junk speed
    assert e.update(13.0, 80.0, float("nan")) == []
    assert e.update(13.0, 80.0, -5) == []
    assert e.update(13.0, 80.0, "fast") == []


def test_numeric_strings_from_json_are_accepted():
    e = ZoneEngine([ZONE])
    assert types(e.update("13.0", "80.0", "5")) == ["ZONE_ENTER"]


def test_valid_position():
    assert valid_position(0, 0) is None
    assert valid_position(13, 80) == (13.0, 80.0)
    assert valid_position(-90, 180) == (-90.0, 180.0)


# ------------------------------------------------------ zone validation
@pytest.mark.parametrize("change", [
    {"severity": "extreme"}, {"severity": None}, {"id": ""}, {"id": "a b"}, {"id": "x" * 33},
    {"id": "Z1;DROP"}, {"name": ""}, {"name": "n" * 101}, {"type": ""},
    {"radius_m": 0}, {"radius_m": -1}, {"radius_m": float("nan")}, {"radius_m": 1e9},
    {"lat": 91}, {"lon": -181}, {"lat": "abc"}, {"speed_limit": -1}, {"speed_limit": 301},
    {"speed_limit": float("inf")},
])
def test_validate_zone_rejects_bad_values(change):
    with pytest.raises(ValueError):
        validate_zone(dict(ZONE, **change))


def test_validate_zone_rejects_non_dict_and_missing():
    for bad in (None, [], "x", 5):
        with pytest.raises(ValueError):
            validate_zone(bad)
    z = dict(ZONE)
    del z["radius_m"]
    with pytest.raises(ValueError):
        validate_zone(z)


def test_validate_zone_normalises():
    v = validate_zone(dict(ZONE, severity="HIGH", active="true", speed_limit=9.6, name="  A  "))
    assert v["severity"] == "high" and v["active"] == 1 and v["speed_limit"] == 10 and v["name"] == "A"
    assert validate_zone(dict(ZONE, active="0"))["active"] == 0


def test_engine_skips_invalid_zones_instead_of_crashing():
    e = ZoneEngine()
    rejected = e.set_zones([ZONE, dict(ZONE, id="B", severity="bad"), "junk", None])
    assert len(rejected) == 3
    assert [z["id"] for z in e.zones] == ["Z1"]
    assert types(e.update(13.0, 80.0, 5)) == ["ZONE_ENTER"]


# ------------------------------------------------------- thread safety
def test_engine_thread_safety_smoke():
    e = ZoneEngine([ZONE])
    errors = []

    def mover():
        try:
            for i in range(1500):
                e.update(north(13.0, 0 if i % 2 else 100), 80.0, 5)
        except Exception as ex:  # pragma: no cover
            errors.append(ex)

    def editor():
        try:
            for i in range(400):
                e.set_zones([dict(ZONE, radius_m=30 + (i % 5))])
                _ = e.current_zone, e.effective_limit(), e.zones
        except Exception as ex:  # pragma: no cover
            errors.append(ex)

    ts = [threading.Thread(target=mover), threading.Thread(target=editor)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    assert len(e.zones) == 1


def test_db_shared_connection_is_thread_safe(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    errors = []

    def worker(n):
        try:
            for i in range(50):
                db.log_event(conn, "ZONE_ENTER", "high", zone_id=f"Z{n}", details=str(i))
                db.query_events(conn, limit=5)
        except Exception as ex:  # pragma: no cover
            errors.append(ex)

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 200
    conn.close()


# ----------------------------------------------------- ESP32 ZONES cmd
def test_esp32_cmd_is_capped_most_severe_first_and_skips_invalid():
    zs = [dict(ZONE, id=f"Z{i:02d}", lat=13.0 + i * 0.001,
               severity="critical" if i == 24 else "low") for i in range(25)]
    zs.append(dict(ZONE, id="BAD", severity="nope"))
    zs.append("junk")
    cmd = zones_to_esp32_cmd(zs)
    assert cmd["cmd"] == "ZONES" and len(cmd["list"]) == 20
    assert cmd["list"][0]["id"] == "Z24"
    assert all(set(x) == {"id", "lat", "lon", "r", "lim"} for x in cmd["list"])


def test_esp32_cmd_rounds_coordinates():
    cmd = zones_to_esp32_cmd([dict(ZONE, lat=13.08271234567, lon=80.27071234567)])
    assert cmd["list"][0]["lat"] == 13.082712 and cmd["list"][0]["lon"] == 80.270712


# ------------------------------------------------------- DB leniency
@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "h.db")
    yield c
    c.close()


def test_accident_event_never_rejected_for_bad_fields(conn):
    eid = db.log_event(conn, "ACCIDENT", "catastrophic", speed=float("nan"), lat="abc", lon=2.0,
                       details={"x": 1}, driver="A" * 200, zone_id="Z" * 99)
    r = dict(conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())
    assert r["type"] == "ACCIDENT" and r["severity"] == "high"
    assert r["speed"] is None and r["lat"] is None and r["lon"] == 2.0
    assert "catastrophic" in r["details"] and '"x": 1' in r["details"]
    assert len(r["driver"]) == 64 and len(r["zone_id"]) == 32


def test_event_type_none_becomes_unknown(conn):
    eid = db.log_event(conn, None, "low")
    assert conn.execute("SELECT type FROM events WHERE id=?", (eid,)).fetchone()[0] == "UNKNOWN"


def test_query_limit_is_clamped_and_junk_tolerated(conn):
    for i in range(3):
        db.log_event(conn, "ZONE_ENTER", "low")
    assert len(db.query_events(conn, limit="abc")) == 3
    assert len(db.query_events(conn, limit=0)) == 1
    assert len(db.query_events(conn, limit=10 ** 12)) == 3
    assert len(db.query_events(conn, date_from="junk", date_to=float("nan"))) == 3


def test_csv_blocks_formula_injection(conn):
    db.log_event(conn, "ZONE_ENTER", "low", driver="+cmd", details='=HYPERLINK("http://x")')
    rows = list(csv.reader(io.StringIO(db.events_to_csv(db.query_events(conn)))))
    assert rows[1][4] == "'+cmd"
    assert rows[1][9].startswith("'=")


def test_telemetry_coerces_bad_numbers(conn):
    db.log_telemetry(conn, speed=float("nan"), alcohol="abc", lat=13.0, state="RUNNING" * 20)
    r = conn.execute("SELECT * FROM telemetry").fetchone()
    assert r["speed"] is None and r["alcohol"] is None and r["lat"] == 13.0 and len(r["state"]) == 32


def test_add_driver_validation(conn):
    for bad in ("", "   ", None, "x" * 65, 5):
        with pytest.raises(ValueError):
            db.add_driver(conn, bad)


def test_seed_skips_invalid_zone_entries(conn, tmp_path):
    p = tmp_path / "z.json"
    p.write_text(json.dumps({"zones": [ZONE, dict(ZONE, id="B", severity="bad")]}), encoding="utf-8")
    assert db.seed_zones_from_json(conn, p) == 1
    assert [z["id"] for z in db.list_zones(conn)] == ["Z1"]


# ---------------------------------------------------------- API abuse
@pytest.fixture
def client(tmp_path):
    return zone_api.create_dev_app(tmp_path / "api.db").test_client()


def post_raw(client, text):
    return client.post("/api/zones", data=text, content_type="application/json")


def test_api_rejects_non_object_and_invalid_json(client):
    assert client.post("/api/zones", json=[1, 2]).status_code == 400
    assert post_raw(client, "not json").status_code == 400
    assert post_raw(client, "null").status_code == 400
    assert client.put("/api/zones/Z01", json="x").status_code == 400


def test_api_rejects_bad_zone_values(client):
    base = dict(ZONE, id="N1")
    assert client.post("/api/zones", json=dict(base, id="a b")).status_code == 400
    assert client.post("/api/zones", json=dict(base, severity="x")).status_code == 400
    assert post_raw(client, json.dumps(dict(base, lat=float("nan")))).status_code == 400
    assert client.post("/api/zones", json=dict(base, radius_m=-5)).status_code == 400
    r = client.post("/api/zones", json=dict(base, name="x" * 500))
    assert r.status_code == 400 and "sqlite" not in r.get_data(as_text=True).lower()


def test_api_failed_edit_leaves_zone_unchanged(client):
    before = client.get("/api/zones").json
    z = before[0]
    assert client.put(f"/api/zones/{z['id']}", json=dict(z, severity="bad")).status_code == 400
    assert client.get("/api/zones").json == before


def test_api_query_params_validated_and_clamped(client):
    assert client.get("/api/events?limit=abc").status_code == 400
    assert client.get("/api/events?limit=999999999").status_code == 200
    assert client.get("/api/events?from=nan").status_code == 400
    assert client.get("/api/events.csv?to=zzz").status_code == 400


def test_api_oversized_body_rejected(client):
    r = post_raw(client, "x" * (70 * 1024))
    assert r.status_code == 413 and r.get_json()["error"] == "request too large"


def test_api_security_headers(client):
    r = client.get("/api/zones")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_failing_callback_does_not_fail_request(tmp_path):
    from flask import Flask
    c = db.connect(tmp_path / "cb.db")
    app = Flask(__name__)

    def boom(_cmd):
        raise RuntimeError("serial port gone")

    app.register_blueprint(zone_api.create_zone_blueprint(c, on_zones_changed=boom))
    r = app.test_client().post("/api/zones", json=ZONE)
    assert r.status_code == 201
    assert db.get_zone(c, "Z1") is not None
    c.close()
