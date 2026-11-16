"""First page (face login), dashboard, admin panel password, SOS button and the face-presence check."""

import base64
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import admin_auth  # noqa: E402
import app as server  # noqa: E402
import config  # noqa: E402
import sms_alert  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_AUTH_REQUIRED", True)
    admin_auth.reset_for_tests()
    sms_alert.reset_for_tests()
    server.state.active_driver_auth = 0
    return server.app.test_client()


def login(client, password="1234"):
    return client.post("/api/admin/login", json={"password": password})


# ---- first page and dashboard

def test_first_page_is_the_face_login_with_an_admin_button(client):
    html = client.get("/").get_data(as_text=True)
    assert "login.js" in html and 'href="/admin-login"' in html and "Admin Panel" in html


def test_dashboard_is_closed_until_the_face_login_succeeds(client):
    res = client.get("/home")
    assert res.status_code == 302 and res.headers["Location"].endswith("/")
    server.state.active_driver_auth = 1
    assert client.get("/home").status_code == 200


def test_dashboard_has_sos_and_no_emergency_stop(client):
    server.state.active_driver_auth = 1
    html = client.get("/home").get_data(as_text=True)
    assert 'id="btn-sos"' in html and 'id="btn-emergency-stop"' not in html
    assert "btn-emergency-stop" not in html.replace('id="btn-sos"', "")   # no such button any more


# ---- admin password

def test_admin_panel_needs_the_password(client):
    res = client.get("/admin")
    assert res.status_code == 302 and "/admin-login" in res.headers["Location"]
    assert client.get("/admin-login").status_code == 200
    assert login(client, "wrong").status_code == 401
    assert client.get("/admin").status_code == 302
    assert login(client).status_code == 200
    assert client.get("/admin").status_code == 200
    assert client.get("/api/admin/status").get_json()["admin"] is True


def test_the_admin_cookie_is_http_only(client):
    cookie = login(client).headers["Set-Cookie"]
    assert "admin_session=" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_logout_closes_the_admin_panel(client):
    login(client)
    client.post("/api/admin/logout")
    assert client.get("/admin").status_code == 302


def test_five_wrong_passwords_lock_the_address(client):
    for _ in range(config.ADMIN_MAX_WRONG):
        assert login(client, "nope").status_code == 401
    res = login(client, "1234")                      # even the right one is refused while locked
    assert res.status_code == 429 and "Try again" in res.get_json()["message"]


def test_a_session_expires():
    token, _, _ = admin_auth.login("1234", "x", now=1000.0)
    assert admin_auth.is_valid(token, now=1001.0)
    assert not admin_auth.is_valid(token, now=1000.0 + config.ADMIN_SESSION_TTL_S + 1)


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/enrol", {"name": "A", "license": "1", "frames": ["x"]}),
    ("post", "/api/zones", {"code": "ZZ", "name": "n", "latitude": 1, "longitude": 1}),
    ("delete", "/api/zones/1", None),
    ("post", "/zm/api/zones", {"id": "ZZ"}),
    ("post", "/api/drivers", {"name": "x"}),
    ("delete", "/api/logs", None),
    ("post", "/api/serial/connect", {"port": "COM3"}),
    ("post", "/api/command", {"command": "RESET"}),
    ("post", "/api/command", {"command": "STOP"}),
])
def test_changes_are_refused_without_the_admin_login(client, method, path, body):
    res = getattr(client, method)(path, json=body)
    assert res.status_code == 401 and res.get_json()["message"] == "Admin login required."


def test_after_login_the_same_changes_are_allowed(client):
    login(client)
    assert client.post("/api/command", json={"command": "RESET"}).status_code == 200


def test_the_dashboard_keeps_working_without_the_admin_login(client):
    assert client.get("/api/zones").status_code == 200                                   # reading is open
    assert client.get("/api/events").status_code == 200
    assert client.post("/api/command", json={"command": "AUTH", "value": 0}).status_code == 200   # lock vehicle
    assert client.post("/api/command", json={"command": "ACK"}).status_code == 200
    assert client.post("/api/auth/challenge").status_code == 200


# ---- the admin panel page itself

def test_admin_view_holds_the_admin_items_and_the_dashboard_view_does_not():
    html = (Path(server.FRONTEND_DIR) / "index.html").read_text(encoding="utf-8")
    assert 'class="panel-card admin-only"' in html                  # Controls, drivers, zones, events
    assert 'class="dash-only"' in html and "view-admin" in html and "view-dash" in html


# ---- face presence

def frame():
    ok, buf = cv2.imencode(".jpg", np.full((120, 160, 3), 128, np.uint8))
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


@pytest.mark.parametrize("count", [0, 1, 2])
def test_face_present_reports_the_number_of_faces_and_changes_nothing(client, monkeypatch, count):
    monkeypatch.setattr(server.face_sface, "models_ready", True)
    monkeypatch.setattr(server.face_sface, "_faces", lambda f: [object()] * count)      # only the face count is needed
    server.state.active_driver_auth = 0
    before = len(server.database.get_events(limit=500)["events"])
    res = client.post("/api/face-present", json={"frame": frame()})
    assert res.get_json()["faces"] == count
    assert server.state.active_driver_auth == 0
    assert len(server.database.get_events(limit=500)["events"]) == before


def test_face_present_needs_a_picture(client):
    assert client.post("/api/face-present", json={}).status_code == 400


# ---- SOS

@pytest.fixture()
def texts(monkeypatch):
    sent = []
    monkeypatch.setattr(sms_alert, "load_settings", lambda: {
        "enabled": True, "configured": True, "problems": [], "to_numbers": ["918220454887"], "cooldown_s": 300,
        "max_per_hour": 6, "max_per_day": 20, "max_per_month": 90, "api_key": "k", "template_id": "101",
        "var1": "alert_text", "var2": "location", "value_style": "bare", "provider": "circuitdigest",
        "api_url": sms_alert.DEFAULT_API_URL})
    monkeypatch.setattr(sms_alert, "RUN_IN_BACKGROUND", False)
    monkeypatch.setattr(sms_alert, "_post", lambda *a, **k: (sent.append(a) or (200, "ok")))
    return sent


def test_sos_is_recorded_and_texted(client, texts):
    before = len(server.database.get_events(limit=500, event_types=("SOS",))["events"])
    res = client.post("/api/sos").get_json()
    assert res["status"] == "success"
    events = server.database.get_events(limit=500, event_types=("SOS",))["events"]
    assert len(events) == before + 1 and events[0]["severity"] == "CRITICAL"
    assert res["texted"] is True and len(texts) == 1


def test_sos_still_goes_out_when_the_hourly_limit_is_used_up(client, texts, monkeypatch):
    monkeypatch.setattr(server.database, "count_sms_since", lambda seconds: 6 if seconds == 3600 else 0)
    assert client.post("/api/sos").get_json()["texted"] is True


def test_sos_is_not_sent_when_the_monthly_limit_is_used_up(client, texts, monkeypatch):
    monkeypatch.setattr(server.database, "count_sms_since", lambda seconds: 90 if seconds > 86400 else 0)
    res = client.post("/api/sos").get_json()
    assert res["texted"] is False and "not sent" in res["message"]


def test_a_second_sos_within_30_seconds_is_not_texted_again(client, texts):
    assert client.post("/api/sos").get_json()["texted"] is True
    assert client.post("/api/sos").get_json()["texted"] is False
