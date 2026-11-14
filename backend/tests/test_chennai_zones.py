"""Chennai accident spots from public reports, and the map that shows only the zones near the car."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as server  # noqa: E402
import db as zone_db  # noqa: E402

ROOT = Path(server.__file__).resolve().parent
FRONT = Path(server.FRONTEND_DIR)
ZONES = json.loads((ROOT / "zones_chennai.json").read_text(encoding="utf-8"))["zones"]


def test_the_chennai_file_is_valid_and_complete():
    assert [z["id"] for z in ZONES] == [f"CB{n:02d}" for n in range(1, 14)]
    for z in ZONES:
        zone_db.validate_zone(z)                                     # the zone manager accepts every entry
        assert 12.6 <= z["lat"] <= 13.3 and 79.9 <= z["lon"] <= 80.4, z["name"]        # inside the Chennai area
        assert 100 <= z["radius_m"] <= 300, z["name"]                 # a spot, never a whole district
    assert len({z["name"] for z in ZONES}) == len(ZONES)


def test_every_zone_has_a_source_in_the_notes():
    notes = (Path(server.__file__).resolve().parent.parent / "docs" / "ZONE_SOURCES.md").read_text(encoding="utf-8")
    for z in ZONES:
        assert z["id"] in notes or "CB11-13" in notes, z["id"]
    assert notes.count("https://") >= 5


def test_the_file_loads_once_and_twice_adds_nothing():
    conn = zone_db.connect(Path(tempfile.mkdtemp()) / "z.db")
    first = zone_db.seed_zones_from_json(conn, ROOT / "zones_chennai.json")
    second = zone_db.seed_zones_from_json(conn, ROOT / "zones_chennai.json")
    assert first == 13 and second == 0


def test_the_chennai_zones_are_in_the_running_servers_list():
    ids = {z["id"] for z in zone_db.list_zones(server.zone_conn)}
    assert {f"CB{n:02d}" for n in range(1, 14)} <= ids and zone_db.get_meta(server.zone_conn, "chennai_blackspots_loaded") == "1"


def test_district_zones_are_not_drawn_as_filled_circles():
    js = (FRONT / "static" / "js" / "map.js").read_text(encoding="utf-8")
    assert "areaZoneMinRadiusM = 1500" in js and "fill: false" in js and "dashArray: \"8, 8\"" in js
    zones_js = (FRONT / "zones.js").read_text(encoding="utf-8")
    assert "AREA_RADIUS_M = 1500" in zones_js and "fill: !area" in zones_js


def test_only_nearby_spots_are_drawn_unless_asked():
    js = (FRONT / "static" / "js" / "map.js").read_text(encoding="utf-8")
    assert "alertDistanceM = 1000" in js and "nearRadiusM" not in js and "showAllZones" in js and "Show all danger spots" in js
    assert "this.showAllZones = true" in js                           # every danger spot has a pin, so the dangers always show
    assert js.index("if (!near && !this.showAllZones) return;") < js.index('items.push({ kind: "pin"')
    assert 'if (near) { nearCount += 1; items.push({ kind: "circle"' in js       # the circle (and the notification) only for the near ones
    assert "zone-go-nearest" in js


def test_the_map_looks_like_google_maps_with_a_satellite_view_and_a_google_maps_link():
    js = (FRONT / "static" / "js" / "map.js").read_text(encoding="utf-8")
    assert "World_Street_Map" in js and "cartocdn" not in js and "World_Imagery" in js and "L.control.layers" in js
    assert "https://www.google.com/maps/search/?api=1&query=" in js and "zone-pin" in js


def test_the_map_shows_the_live_location_and_has_a_my_location_button():
    map_js = (FRONT / "static" / "js" / "map.js").read_text(encoding="utf-8")
    dash_js = (FRONT / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert "updateLiveLocation(lat, lon, accuracyM)" in map_js and "live-dot" in map_js and "centerOnLive()" in map_js
    assert "MapManager.updateLiveLocation(pos.coords.latitude" in dash_js and "liveLocationOff" in dash_js
    assert "this.currentPoint() || this.view.getCenter()" in map_js               # near zones follow the live location when there is no car
    for page in ("index.html", "dashboard.html"):
        assert 'id="btn-map-me"' in (FRONT / page).read_text(encoding="utf-8")


def test_in_the_admin_panel_the_map_is_below_the_tabs_and_full_width():
    css = (FRONT / "static" / "css" / "dashboard.css").read_text(encoding="utf-8")
    assert ".view-admin .panel-card.admin-only { order: 1; }" in css
    assert ".view-admin .mid-grid { order: 2; grid-template-columns: 1fr !important; }" in css


def test_the_vehicle_has_its_own_car_icon_and_the_old_car_drawing_is_gone():
    js = (FRONT / "static" / "js" / "map.js").read_text(encoding="utf-8")
    assert "custom-car-marker" not in js and "M18.92 6.01C18.72" not in js          # the old car drawing is gone
    assert "carIconSvg()" in js and "drawLive()" in js
