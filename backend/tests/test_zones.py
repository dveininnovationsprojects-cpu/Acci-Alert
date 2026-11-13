import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent  # backend/
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import zones as zmod  # noqa: E402
from zones import ZoneEngine, haversine_m  # noqa: E402

M_PER_DEG_LAT = 111194.9  # metres per degree latitude (sphere, R=6371 km)


def north(lat, metres):
    return lat + metres / M_PER_DEG_LAT


ZONE = {"id": "Z1", "name": "School", "type": "school", "lat": 13.0, "lon": 80.0,
        "radius_m": 30, "speed_limit": 10, "severity": "high", "active": 1}


def types(events):
    return [e["type"] for e in events]


# ------------------------------------------------------------- haversine
def test_haversine_zero_and_known_distance():
    assert haversine_m(13.0, 80.0, 13.0, 80.0) == 0
    assert haversine_m(0, 0, 1, 0) == pytest.approx(M_PER_DEG_LAT, rel=1e-3)


# ---------------------------------------------------------- enter / exit
def test_enter_and_exit_once():
    e = ZoneEngine([ZONE])
    assert types(e.update(north(13.0, 100), 80.0, 5)) == []
    assert types(e.update(north(13.0, 10), 80.0, 5)) == ["ZONE_ENTER"]
    assert types(e.update(13.0, 80.0, 5)) == []          # still inside: no repeat
    assert types(e.update(north(13.0, 60), 80.0, 5)) == ["ZONE_EXIT"]
    assert types(e.update(north(13.0, 90), 80.0, 5)) == []


def test_hysteresis_prevents_flicker():
    e = ZoneEngine([ZONE])
    e.update(north(13.0, 29), 80.0, 5)                    # ENTER
    out = []
    for m in (31, 29, 33, 30.5, 34, 28):                  # jitter around the edge (<= r+5)
        out += e.update(north(13.0, m), 80.0, 5)
    assert out == []
    assert types(e.update(north(13.0, 36), 80.0, 5)) == ["ZONE_EXIT"]


def test_jitter_on_boundary_does_not_double_enter():
    e = ZoneEngine([ZONE])
    out = []
    for m in (31, 29, 31, 29, 31, 29):
        out += e.update(north(13.0, m), 80.0, 5)
    assert types(out).count("ZONE_ENTER") == 1
    assert types(out).count("ZONE_EXIT") == 0


def test_t11_ten_crossings_exactly_ten_enter_exit():
    e = ZoneEngine([ZONE])
    enters = exits = 0
    for _ in range(10):
        for m in (80, 50, 31, 20, 5, 0, 5, 20, 28, 31, 34, 37, 60, 80):
            for ev in e.update(north(13.0, m), 80.0, 5):
                enters += ev["type"] == "ZONE_ENTER"
                exits += ev["type"] == "ZONE_EXIT"
    assert (enters, exits) == (10, 10)


# ------------------------------------------------------------ pre-warning
def test_prewarn_once_per_approach_and_rearms():
    e = ZoneEngine([ZONE])
    assert types(e.update(north(13.0, 45), 80.0, 5)) == ["ZONE_APPROACH"]
    assert types(e.update(north(13.0, 40), 80.0, 5)) == []
    assert types(e.update(north(13.0, 100), 80.0, 5)) == []   # leaves ring, re-arms
    assert types(e.update(north(13.0, 45), 80.0, 5)) == ["ZONE_APPROACH"]


def test_no_prewarn_beyond_ring():
    e = ZoneEngine([ZONE])
    assert e.update(north(13.0, 55), 80.0, 5) == []


# ------------------------------------------------------------- overspeed
def test_overspeed_once_then_rearms():
    e = ZoneEngine([ZONE])
    assert types(e.update(13.0, 80.0, 9)) == ["ZONE_ENTER"]
    ev = e.update(13.0, 80.0, 15)
    assert types(ev) == ["OVERSPEED_IN_ZONE"] and ev[0]["severity"] == "high"
    assert e.update(13.0, 80.0, 16) == []                  # no duplicate
    assert e.update(13.0, 80.0, 8) == []                   # back under limit
    assert types(e.update(13.0, 80.0, 12)) == ["OVERSPEED_IN_ZONE"]


def test_enter_and_overspeed_in_same_fix():
    e = ZoneEngine([ZONE])
    assert types(e.update(13.0, 80.0, 20)) == ["ZONE_ENTER", "OVERSPEED_IN_ZONE"]


def test_overspeed_not_raised_outside():
    e = ZoneEngine([ZONE])
    assert e.update(north(13.0, 200), 80.0, 50) == []


# ---------------------------------------------------- critical / disabled
def test_critical_zone_flag_and_zero_limit():
    z = dict(ZONE, id="Z9", severity="critical", speed_limit=0)
    e = ZoneEngine([z])
    ev = e.update(13.0, 80.0, 0)
    assert ev[0]["type"] == "ZONE_ENTER" and ev[0]["critical"] and ev[0]["severity"] == "critical"
    assert e.current_zone["id"] == "Z9"
    assert e.effective_limit() == 0


def test_disabled_zone_ignored():
    e = ZoneEngine([dict(ZONE, active=0)])
    assert e.update(13.0, 80.0, 50) == []


def test_disabling_zone_while_inside_emits_exit():
    e = ZoneEngine([ZONE])
    e.update(13.0, 80.0, 5)
    e.set_zones([dict(ZONE, active=0)])
    assert types(e.update(13.0, 80.0, 5)) == ["ZONE_EXIT"]
    assert e.current_zone is None


def test_set_zones_keeps_state_and_drops_removed():
    e = ZoneEngine([ZONE])
    e.update(13.0, 80.0, 5)
    e.set_zones([dict(ZONE, speed_limit=20)])              # edit, still inside
    assert e.update(13.0, 80.0, 5) == []
    e.set_zones([])                                        # removed
    assert e.current_zone is None


def test_overlapping_zones_pick_most_severe_and_lowest_limit():
    a = dict(ZONE, id="A", severity="medium", speed_limit=8)
    b = dict(ZONE, id="B", severity="high", speed_limit=5)
    e = ZoneEngine([a, b])
    e.update(13.0, 80.0, 1)
    assert e.current_zone["id"] == "B"
    assert e.effective_limit() == 5


def test_esp32_cmd_format():
    cmd = zmod.zones_to_esp32_cmd([ZONE, dict(ZONE, id="Z2", active=0)])
    assert cmd == {"cmd": "ZONES", "list": [{"id": "Z1", "lat": 13.0, "lon": 80.0, "r": 30, "lim": 10}]}


# --------------------------------------------------------------------- DB
@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    yield c
    c.close()


def test_zones_json_has_five_valid_zones_and_loads(conn):
    data = json.loads((ROOT / "zones.json").read_text(encoding="utf-8"))
    assert len(data["zones"]) >= 5
    assert db.seed_zones_from_json(conn, ROOT / "zones.json") == len(data["zones"])
    assert db.seed_zones_from_json(conn, ROOT / "zones.json") == 0   # idempotent
    zs = db.list_zones(conn)
    for i, a in enumerate(zs):                                       # none overlap
        for b in zs[i + 1:]:
            assert haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) > a["radius_m"] + b["radius_m"]


def test_zone_crud_without_restart(conn):
    for i in range(5):
        db.upsert_zone(conn, dict(ZONE, id=f"Z{i}"))
    assert len(db.list_zones(conn)) == 5
    db.upsert_zone(conn, dict(ZONE, id="Z0", speed_limit=3))
    assert db.get_zone(conn, "Z0")["speed_limit"] == 3
    assert db.set_zone_active(conn, "Z1", False)
    assert len(db.list_zones(conn, active_only=True)) == 4
    assert db.delete_zone(conn, "Z2") and not db.delete_zone(conn, "Z2")
    assert len(db.list_zones(conn)) == 4


def test_zone_validation(conn):
    with pytest.raises(Exception):
        db.upsert_zone(conn, dict(ZONE, severity="extreme"))
    with pytest.raises(ValueError):
        db.upsert_zone(conn, {"id": "x"})


def test_event_log_filter_and_csv(conn):
    t0 = 1_000_000.0
    db.log_event(conn, "ACCIDENT", "critical", driver="Arun", lat=1, lon=2, timestamp=t0)
    db.log_event(conn, "ZONE_ENTER", "high", zone_id="Z1", details={"a": 1}, timestamp=t0 + 100)
    db.log_event(conn, "ZONE_EXIT", "low", zone_id="Z1", timestamp=t0 + 200)
    assert len(db.query_events(conn)) == 3
    assert [r["type"] for r in db.query_events(conn, type="ZONE_ENTER")] == ["ZONE_ENTER"]
    assert len(db.query_events(conn, date_from=t0 + 50, date_to=t0 + 150)) == 1
    assert db.query_events(conn)[0]["type"] == "ZONE_EXIT"          # newest first
    csv_text = db.events_to_csv(db.query_events(conn))
    assert csv_text.splitlines()[0] == "id,timestamp,type,severity,driver,speed,lat,lon,zone_id,details"
    assert len(csv_text.strip().splitlines()) == 4


def test_event_is_durable_when_log_event_returns(tmp_path):
    p = tmp_path / "d.db"
    c1 = db.connect(p)
    eid = db.log_event(c1, "ACCIDENT", "critical", lat=1.0, lon=2.0)
    c2 = db.connect(p)                                              # independent connection sees it
    assert c2.execute("SELECT type FROM events WHERE id=?", (eid,)).fetchone()["type"] == "ACCIDENT"


def test_telemetry_recent_and_24h_purge(conn):
    now = time.time()
    db.log_telemetry(conn, speed=1, timestamp=now - 25 * 3600)
    db.log_telemetry(conn, speed=2, timestamp=now - 120)
    db.log_telemetry(conn, speed=3, timestamp=now - 30)
    db.log_telemetry(conn, speed=4, timestamp=now - 5)
    assert [r["speed"] for r in db.recent_telemetry(conn, 60, now=now)] == [3, 4]
    assert db.purge_telemetry(conn, now=now) == 1
    assert conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0] == 3


def test_engine_events_flow_into_db(conn):
    e = ZoneEngine([ZONE])
    for ev in e.update(13.0, 80.0, 20):
        db.log_event(conn, ev["type"], ev["severity"], driver="Arun", speed=ev["speed"],
                     lat=ev["lat"], lon=ev["lon"], zone_id=ev["zone_id"], details=ev["details"])
    assert [r["type"] for r in db.query_events(conn)][::-1] == ["ZONE_ENTER", "OVERSPEED_IN_ZONE"]


def test_drivers(conn):
    db.add_driver(conn, "Arun")
    db.add_driver(conn, "Arun")                                     # replace, no duplicate
    assert [d["name"] for d in db.list_drivers(conn)] == ["Arun"]
