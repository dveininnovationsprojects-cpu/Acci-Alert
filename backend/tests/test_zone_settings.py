"""Danger zone notification distance (Danger Zone Management) and the Google Maps view."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import admin_auth  # noqa: E402
import app as server  # noqa: E402
import config  # noqa: E402
from zones import ZoneEngine  # noqa: E402

FRONT = Path(server.FRONTEND_DIR)


@pytest.fixture()
def client():
    yield server.app.test_client()
    server.zone_db.set_meta(server.zone_conn, "zone_alert_distance_m", config.ZONE_ALERT_DEFAULT_M)
    server.zone_engine.prewarn_m = float(config.ZONE_ALERT_DEFAULT_M)


def test_default_distance_and_limits(client):
    body = client.get("/api/zone-settings").get_json()
    assert body["alert_distance_m"] == 1000 and body["min"] == 50 and body["max"] == 5000
    assert body["presets"] == [100, 250, 500, 1000, 2000, 5000]


@pytest.mark.parametrize("metres", [50, 100, 250, 1234, 5000])
def test_a_chosen_distance_is_saved_and_used_by_the_zone_engine(client, metres):
    res = client.post("/api/zone-settings", json={"alert_distance_m": metres})
    assert res.status_code == 200 and res.get_json()["alert_distance_m"] == metres
    assert client.get("/api/zone-settings").get_json()["alert_distance_m"] == metres
    assert server.zone_engine.prewarn_m == metres
    assert server.zone_db.get_meta(server.zone_conn, "zone_alert_distance_m") == str(metres)    # kept for the next start


@pytest.mark.parametrize("bad", [49, 5001, 0, -100, "abc", None, "", True, [250]])
def test_a_wrong_distance_is_refused(client, bad):
    before = client.get("/api/zone-settings").get_json()["alert_distance_m"]
    res = client.post("/api/zone-settings", json={"alert_distance_m": bad})
    assert res.status_code == 400 and "50 to 5000" in res.get_json()["message"]
    assert client.get("/api/zone-settings").get_json()["alert_distance_m"] == before


def test_only_the_admin_can_change_it(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_AUTH_REQUIRED", True)
    admin_auth.reset_for_tests()
    assert client.get("/api/zone-settings").status_code == 200                       # reading is open (the map needs it)
    assert client.post("/api/zone-settings", json={"alert_distance_m": 300}).status_code == 401
    assert client.get("/api/zone-settings").get_json()["alert_distance_m"] == 1000
    assert client.post("/api/admin/login", json={"password": "1234"}).status_code == 200
    assert client.post("/api/zone-settings", json={"alert_distance_m": 300}).status_code == 200


def test_the_zone_engine_warns_at_the_chosen_distance():
    zone = {"id": "T1", "name": "Test spot", "type": "blackspot", "lat": 13.0, "lon": 80.0, "radius_m": 100,
            "speed_limit": 30, "severity": "high", "active": 1}
    far_north = 13.0 + 0.0045                                  # about 500 m north of the middle, about 400 m from the edge
    for distance_setting, expect in ((250, 0), (500, 1)):
        engine = ZoneEngine([zone], prewarn_m=distance_setting)
        events = engine.update(far_north, 80.0, 20.0)
        assert len([e for e in events if e["type"] == "ZONE_APPROACH"]) == expect, distance_setting


def test_the_zone_tab_has_the_distance_controls():
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert 'id="zone-alert-select"' in html and 'id="btn-save-zone-alert"' in html and "notification distance" in html.lower()
    js = (FRONT / "static" / "js" / "zones.js").read_text(encoding="utf-8")
    assert "/api/zone-settings" in js and "MapManager.setAlertDistance" in js


def test_the_map_uses_the_distance_for_pins_and_notifications():
    js = (FRONT / "static" / "js" / "map.js").read_text(encoding="utf-8")
    assert "distance <= this.alertDistanceM + radius" in js and "checkZoneAlerts()" in js
    assert "Danger zone ahead:" in js and "You are inside a danger zone:" in js
    assert "radius >= this.areaZoneMinRadiusM" in js                 # districts never trigger a "danger zone ahead"
    dash = (FRONT / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert 'socket.on("zone_settings"' in dash


def test_there_is_one_map_which_becomes_google_maps_when_a_key_is_set():
    js = (FRONT / "static" / "js" / "map.js").read_text(encoding="utf-8")
    assert "class GoogleView" in js and "class LeafletView" in js and "maps.googleapis.com/maps/api/js" in js
    assert "/api/maps-config" in js and "gm_authFailure" in js and "fallbackToStreetMap" in js and "gm-err-container" in js and "tilesloaded" in js
    assert "google-map-frame" not in js and "toggleGoogleMap" not in js            # no second map to switch to
    for page in ("index.html", "dashboard.html"):
        assert "btn-map-google" not in (FRONT / page).read_text(encoding="utf-8")


def test_google_view_draws_the_zones_the_car_and_the_live_dot():
    js = (FRONT / "static" / "js" / "map.js").read_text(encoding="utf-8")
    google = js[js.index("class GoogleView"):js.index("class VehicleMapManager")]
    for needle in ("new g.Marker", "new g.Circle", "new g.Polyline", "TrafficLayer", "setZones(items)", "setVehicle(pos, popupHtml)", "setLive(pos, accuracy, popupHtml)"):
        assert needle in google, needle


def test_the_old_map_is_removed_before_the_new_one_is_made():
    js = (FRONT / "static" / "js" / "map.js").read_text(encoding="utf-8")
    body = js[js.index("  startView(create) {"):js.index("  redrawAll() {")]
    assert body.index("this.view.destroy()") < body.index("const view = create()")        # same page element: the order matters
    assert "startView(new " not in js
