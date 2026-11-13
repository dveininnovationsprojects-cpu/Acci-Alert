import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db  # noqa: E402
import zone_api  # noqa: E402

Z = {"id": "Z99", "name": "Test", "type": "school", "lat": 13.0, "lon": 80.0,
     "radius_m": 20, "speed_limit": 5, "severity": "high", "active": 1}


@pytest.fixture
def client(tmp_path):
    app = zone_api.create_dev_app(tmp_path / "api.db")
    return app.test_client()


def test_seeded_and_crud_without_restart(client):
    base = len(client.get("/api/zones").json)
    assert base >= 5
    assert client.post("/api/zones", json=Z).status_code == 201
    assert client.post("/api/zones", json=Z).status_code == 400          # duplicate id
    assert client.put("/api/zones/Z99", json=dict(Z, speed_limit=3)).json["speed_limit"] == 3
    assert client.delete("/api/zones/Z99").status_code == 204
    assert client.delete("/api/zones/Z99").status_code == 404
    assert len(client.get("/api/zones").json) == base


def test_validation_errors(client):
    assert client.post("/api/zones", json={"id": "X"}).status_code == 400
    assert client.post("/api/zones", json=dict(Z, severity="bad")).status_code == 400
    assert client.put("/api/zones/nope", json=Z).status_code == 404


def test_events_filter_and_csv(tmp_path):
    conn = db.connect(tmp_path / "e.db")
    db.log_event(conn, "ZONE_ENTER", "high", zone_id="Z1", timestamp=100)
    db.log_event(conn, "ZONE_EXIT", "low", zone_id="Z1", timestamp=200)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(zone_api.create_zone_blueprint(conn))
    c = app.test_client()
    assert len(c.get("/api/events").json) == 2
    assert len(c.get("/api/events?type=ZONE_EXIT").json) == 1
    assert len(c.get("/api/events?from=150").json) == 1
    assert c.get("/api/events?from=abc").status_code == 400
    r = c.get("/api/events.csv?type=ZONE_ENTER")
    assert r.mimetype == "text/csv" and len(r.text.strip().splitlines()) == 2


def test_callback_gets_esp32_cmd(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    from flask import Flask
    sent = []
    app = Flask(__name__)
    app.register_blueprint(zone_api.create_zone_blueprint(conn, on_zones_changed=sent.append))
    app.test_client().post("/api/zones", json=Z)
    assert sent[-1] == {"cmd": "ZONES", "list": [{"id": "Z99", "lat": 13.0, "lon": 80.0, "r": 20, "lim": 5}]}
