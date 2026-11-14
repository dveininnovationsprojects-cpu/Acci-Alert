"""Which map the page draws: the Google-style street map, or Google Maps when the optional environment key is set."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as server  # noqa: E402

KEY = "AIzaSyTESTTESTTESTTESTTESTTESTTEST12345"
FRONT = Path(server.FRONTEND_DIR)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("ACCIALERT_GOOGLE_MAPS_KEY", raising=False)
    return server.app.test_client()


def test_no_key_means_the_street_map(client):
    body = client.get("/api/maps-config").get_json()
    assert body["provider"] == "street" and body["google_key"] == ""


def test_the_environment_key_switches_to_google_maps(client, monkeypatch):
    monkeypatch.setenv("ACCIALERT_GOOGLE_MAPS_KEY", KEY)
    body = client.get("/api/maps-config").get_json()
    assert body["provider"] == "google" and body["google_key"] == KEY


@pytest.mark.parametrize("bad", ["short", "has spaces in the key 1234567890", "key<script>alert(1)</script>1234", "x" * 101])
def test_a_wrong_looking_environment_key_is_ignored(client, monkeypatch, bad):
    monkeypatch.setenv("ACCIALERT_GOOGLE_MAPS_KEY", bad)
    assert client.get("/api/maps-config").get_json()["provider"] == "street"


def test_the_key_can_no_longer_be_set_from_the_page(client):
    assert client.post("/api/maps-config", json={"google_key": KEY}).status_code == 405
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert "maps-key" not in html and "Google Maps API key" not in html


def test_the_vehicle_is_a_google_style_car_and_the_live_location_a_blue_dot():
    js = (FRONT / "static" / "js" / "map.js").read_text(encoding="utf-8")
    assert "function carIconSvg()" in js and "icon: this.carIcon" in js and "carIconSvg(), 44, 44, 22, 22" in js     # both views
    assert "icon: this.dotIcon, zIndexOffset: 800" in js and "liveDotSvg(), 26, 26, 13, 13" in js                   # live location = dot
    assert "const same = this.vehicle" not in js                                                                        # the dot is never hidden
