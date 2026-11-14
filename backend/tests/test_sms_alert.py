"""Circuit Digest SMS alerts: only serious alerts, never spam, never a secret in the output, never a crash."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as A  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
import sms_alert  # noqa: E402

API_KEY = "circuit-digest-secret-key-123"
TEMPLATE = "103"
TO = ["918220454887", "919812345678"]


@pytest.fixture()
def sms(tmp_path, monkeypatch):
    """A configured account, a fresh settings file and log, and a fake service that records every call."""
    for key in ("ACCIALERT_SMS_API_KEY", "ACCIALERT_SMS_TEMPLATE_ID", "ACCIALERT_SMS_TO", "ACCIALERT_SMS_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "sms_settings.json"
    path.write_text(json.dumps({"api_key": API_KEY, "template_id": TEMPLATE, "to_numbers": TO,
                                "cooldown_s": 300, "max_per_hour": 6, "max_per_day": 20, "max_per_month": 90}), encoding="utf-8")
    monkeypatch.setattr(config, "SMS_SETTINGS_FILE", path)
    monkeypatch.setattr(sms_alert, "RUN_IN_BACKGROUND", False)
    with database.get_db_connection() as conn:
        conn.execute("DELETE FROM sms_log")
        conn.commit()
    sms_alert.reset_for_tests()

    calls = []
    reply = {"status": 200, "json": {"success": True, "message": "queued"}}

    def fake_post(url, params=None, headers=None, data=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers, "data": json.loads(data), "timeout": timeout})
        if isinstance(reply.get("raise"), Exception):
            raise reply["raise"]
        return SimpleNamespace(status_code=reply["status"], json=lambda: reply["json"])

    monkeypatch.setattr(sms_alert.requests, "post", fake_post)
    return SimpleNamespace(calls=calls, reply=reply, path=path, mp=monkeypatch)


def write_settings(sms, **changes):
    data = json.loads(sms.path.read_text(encoding="utf-8"))
    data.update(changes)
    sms.path.write_text(json.dumps(data), encoding="utf-8")


# ---- what is texted

def test_an_accident_is_texted_to_every_recipient_with_the_template_values(sms):
    assert sms_alert.notify("ACCIDENT", {"driver": "Arun", "speed": 42}) is True
    assert [c["data"]["mobiles"] for c in sms.calls] == TO
    call = sms.calls[0]
    assert call["url"] == sms_alert.DEFAULT_API_URL and call["url"].startswith("https://")
    assert call["params"] == {"ID": TEMPLATE}
    assert call["headers"]["Authorization"] == API_KEY and call["timeout"] == config.SMS_TIMEOUT_S
    assert call["data"] == {"mobiles": TO[0], "var1": "Accident", "var2": "Arun"}
    assert [r["status"] for r in database.get_sms_log(2)] == ["sent", "sent"]


@pytest.mark.parametrize("alert,word", [("ALCOHOL DETECTED", "Alcohol"), ("UNAUTHORISED DRIVER", "UnauthorisedDriver"),
                                         ("UNAUTHORISED LOGIN", "UnauthorisedLogin")])
def test_the_other_serious_alerts_are_texted(sms, alert, word):
    assert sms_alert.notify(alert, {"driver": "Unknown"}) is True and len(sms.calls) == len(TO)
    assert sms.calls[0]["data"]["var1"] == word


@pytest.mark.parametrize("alert", ["OVERSPEED", "OBSTACLE DETECTED", "DANGER ZONE", "ZONE_ENTER", "AUTH_OK"])
def test_minor_alerts_are_not_texted(sms, alert):
    assert sms_alert.notify(alert, {}) is False and sms.calls == []


def test_plain_values_are_letters_digits_and_spaces_only_and_at_most_30(sms):
    sms_alert.notify("ACCIDENT", {"driver": "Dr. O'Neil-Smith the Third, of Chennai & Madurai (night)"})
    var2 = sms.calls[0]["data"]["var2"]
    assert len(var2) <= 30 and all(c.isalnum() or c == " " for c in var2) and "  " not in var2


def test_an_unknown_driver_gets_a_word_not_an_empty_value(sms):
    sms_alert.notify("ACCIDENT", {"driver": None})
    assert sms.calls[0]["data"]["var2"] == "Unknown"
    sms.calls.clear()
    sms_alert.reset_for_tests()
    sms_alert.notify("ACCIDENT", {"driver": "!!!"})
    assert sms.calls[0]["data"]["var2"] == "Unknown"


def test_the_values_can_be_chosen(sms):
    write_settings(sms, var1="driver", var2="speed")
    sms_alert.notify("ACCIDENT", {"driver": "Arun", "speed": 41.6})
    assert (sms.calls[0]["data"]["var1"], sms.calls[0]["data"]["var2"]) == ("Arun", "42")


def test_a_bad_value_choice_is_a_setup_problem(sms):
    write_settings(sms, var1="nonsense")
    assert not sms_alert.status()["configured"]
    write_settings(sms, var1="alert", value_style="fancy")
    assert not sms_alert.status()["configured"]


@pytest.mark.parametrize("given,expected", [
    ("8220454887", "918220454887"), ("+918220454887", "918220454887"), ("91 82204 54887", "918220454887"),
    ("918220454887", "918220454887"), ("0822045488", None), ("12345", None), ("+14155550100", None), ("", None),
])
def test_phone_numbers_are_put_in_the_form_the_service_wants(given, expected):
    assert sms_alert.normalise_number(given) == expected


def test_numbers_entered_as_plus_91_are_accepted(sms):
    write_settings(sms, to_numbers=["+918220454887"])
    sms_alert.notify("ACCIDENT", {})
    assert sms.calls[0]["data"]["mobiles"] == "918220454887"


# ---- it cannot spam

def test_the_same_alert_is_texted_once_per_cooldown(sms):
    assert sms_alert.notify("ACCIDENT", {}) is True
    assert sms_alert.notify("ACCIDENT", {}) is False
    assert len(sms.calls) == len(TO)
    assert sms_alert.notify("ALCOHOL DETECTED", {}) is True          # a different alert is not blocked


def test_the_cooldown_ends(sms):
    write_settings(sms, cooldown_s=0)
    assert sms_alert.notify("ACCIDENT", {}) and sms_alert.notify("ACCIDENT", {})


def test_the_hourly_cap_holds_even_with_different_alerts(sms):
    write_settings(sms, max_per_hour=4, cooldown_s=0)                # 2 recipients: two alerts fit, the third does not
    assert sms_alert.notify("ACCIDENT", {}) and sms_alert.notify("ALCOHOL DETECTED", {})
    assert sms_alert.notify("UNAUTHORISED DRIVER", {}) is False
    assert len(sms.calls) == 4


def test_the_daily_cap_holds(sms):
    write_settings(sms, max_per_day=2, cooldown_s=0)
    assert sms_alert.notify("ACCIDENT", {}) is True
    assert sms_alert.notify("ALCOHOL DETECTED", {}) is False


def test_the_cap_survives_a_restart(sms):
    write_settings(sms, max_per_hour=2, cooldown_s=0)
    assert sms_alert.notify("ACCIDENT", {}) is True
    sms_alert.reset_for_tests()                                      # like a new server process: memory is empty, the log is not
    assert sms_alert.notify("ALCOHOL DETECTED", {}) is False


def test_a_burst_of_alerts_sends_one_set_of_texts(sms):
    results = [sms_alert.notify("ACCIDENT", {}) for _ in range(50)]
    assert results.count(True) == 1 and len(sms.calls) == len(TO)


# ---- problems never break the car

def test_nothing_is_sent_when_it_is_not_set_up(sms):
    sms.path.unlink()
    assert sms_alert.notify("ACCIDENT", {}) is False and sms.calls == []
    assert sms_alert.status()["configured"] is False


def test_it_can_be_switched_off(sms):
    write_settings(sms, enabled=False)
    assert sms_alert.notify("ACCIDENT", {}) is False and sms.calls == []


def test_the_environment_overrides_the_file(sms):
    sms.mp.setenv("ACCIALERT_SMS_TO", "9111111111")
    sms.mp.setenv("ACCIALERT_SMS_TEMPLATE_ID", "7")
    sms_alert.notify("ACCIDENT", {})
    assert [c["data"]["mobiles"] for c in sms.calls] == ["919111111111"]
    assert sms.calls[0]["params"] == {"ID": "7"}


@pytest.mark.parametrize("changes,problem", [
    ({"api_key": ""}, "API key"), ({"template_id": ""}, "template ID"), ({"template_id": "abc"}, "template ID"),
    ({"to_numbers": ["12345"]}, "not an Indian mobile"), ({"to_numbers": ["+14155550100"]}, "not an Indian mobile"),
    ({"to_numbers": []}, "no recipient"),
])
def test_bad_settings_are_reported_and_nothing_is_sent(sms, changes, problem):
    write_settings(sms, **changes)
    s = sms_alert.status()
    assert not s["configured"] and any(problem in p for p in s["problems"])
    assert sms_alert.notify("ACCIDENT", {}) is False and sms.calls == []


def test_at_most_five_recipients(sms):
    write_settings(sms, to_numbers=[f"98000000{i:02d}" for i in range(8)])
    sms_alert.notify("ACCIDENT", {})
    assert len(sms.calls) == config.SMS_MAX_RECIPIENTS


@pytest.mark.parametrize("status,text", [(403, "not verified"), (401, "API key"), (404, "template ID"), (429, "limit")])
def test_a_refusal_is_explained_and_logged_as_failed(sms, status, text):
    sms.reply.update(status=status, json={"message": "nope"})
    assert sms_alert.notify("ACCIDENT", {}) is True                  # it was tried, and it did not raise
    row = database.get_sms_log(1)[0]
    assert row["status"] == "failed" and text in row["detail"]


def test_an_error_answer_with_status_200_is_still_a_failure(sms):
    sms.reply.update(status=200, json={"success": False, "message": "template not found"})
    sms_alert.notify("ACCIDENT", {})
    assert database.get_sms_log(1)[0]["status"] == "failed"


def test_the_monthly_cap_holds(sms):
    write_settings(sms, max_per_month=2, cooldown_s=0)
    assert sms_alert.notify("ACCIDENT", {}) is True
    assert sms_alert.notify("ALCOHOL DETECTED", {}) is False         # the free plan's monthly allowance is protected


def test_an_address_that_is_not_https_is_warned_about(sms):
    write_settings(sms, api_url="http://www.circuitdigest.cloud/api/v1/send_sms")
    assert any("https" in w for w in sms_alert.status()["warnings"])


def test_failed_messages_do_not_use_up_the_cap(sms):
    write_settings(sms, max_per_hour=2, cooldown_s=0)
    sms.reply.update(status=401, json={"message": "auth"})
    assert sms_alert.notify("ACCIDENT", {}) is True
    sms.reply.update(status=200, json={"success": True})
    assert sms_alert.notify("ACCIDENT", {}) is True


def test_a_network_error_is_absorbed(sms):
    sms.reply["raise"] = requests.ConnectTimeout("slow")
    assert sms_alert.notify("ACCIDENT", {}) is True
    assert database.get_sms_log(1)[0]["status"] == "failed"


def test_the_api_key_and_full_numbers_are_never_shown(sms):
    sms_alert.notify("ACCIDENT", {})
    shown = json.dumps([sms_alert.status(), database.get_sms_log(20)])
    assert API_KEY not in shown
    assert all(n not in shown for n in TO)
    assert sms_alert.status()["recipients"][0].endswith("4887")


def test_sending_in_the_background_does_not_block(sms):
    sms.mp.setattr(sms_alert, "RUN_IN_BACKGROUND", True)
    import time
    started = time.time()
    assert sms_alert.notify("ACCIDENT", {}) is True
    assert time.time() - started < 1.0
    deadline = time.time() + 3
    while len(sms.calls) < len(TO) and time.time() < deadline:
        time.sleep(0.05)
    assert len(sms.calls) == len(TO)


# ---- wired into the alerts

def tick(**kw):
    packet = {"spd": 0, "alc": 0, "dist": 999, "tilt": 0, "auth": 1, "lat": 13.0, "lon": 80.0, "ax": 0, "ay": 0, "az": 1}
    packet.update(kw)
    A.evaluate_and_dispatch_alerts(packet)


@pytest.fixture()
def alerts(sms):
    sms.mp.setattr(A.socketio, "emit", lambda *a, **k: None)
    sms.mp.setattr(A.state, "active_alerts", {})
    sms.mp.setattr(A.state, "last_logged_alerts", {})
    sms.mp.setattr(A.state, "active_driver_auth", 1)
    sms.mp.setattr(A.state, "active_driver_name", "Arun")
    return sms


def test_an_accident_alert_sends_a_text_once_not_every_second(alerts):
    tick(tilt=80)
    tick(tilt=80)
    tick(tilt=80)
    assert len(alerts.calls) == len(TO)
    assert alerts.calls[0]["data"]["var1"] == "Accident"


def test_an_alcohol_alert_sends_a_text(alerts):
    tick(alc=900)
    assert len(alerts.calls) == len(TO) and alerts.calls[0]["data"]["var1"] == "Alcohol"


def test_a_locked_car_that_is_only_switched_on_is_not_an_emergency(alerts):
    alerts.mp.setattr(A.state, "active_driver_auth", 0)
    tick(auth=0, spd=0)
    assert alerts.calls == []


def test_a_locked_car_that_moves_is_texted(alerts):
    alerts.mp.setattr(A.state, "active_driver_auth", 0)
    tick(auth=0, spd=25)
    assert len(alerts.calls) == len(TO) and alerts.calls[0]["data"]["var1"] == "UnauthorisedDriver"


def test_overspeed_alone_is_not_texted(alerts):
    tick(spd=95)
    assert alerts.calls == []


TURNING = [0.0] * 4 + [-0.3] * 4 + [0.3] * 4 + [0.03] * 4                       # right, left, straight


def _login(alerts, who, recognised, yaws):
    import base64
    import cv2
    import numpy as np
    ok, buf = cv2.imencode(".jpg", np.full((120, 160, 3), 128, np.uint8))
    frame = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
    it = iter(yaws)
    alerts.mp.setattr(A, "recognise", lambda f: (1, [20, 20, 60, 60], who, recognised, 90.0 if recognised else 20.0, "", next(it)))
    client = A.app.test_client()
    token = client.post("/api/auth/challenge").get_json()["token"]
    return client.post("/api/authenticate", json={"frames": [frame] * len(yaws), "challenge": token}).get_json()


def test_a_rejected_face_login_is_texted(alerts):
    res = _login(alerts, "Unknown", False, TURNING)
    assert res["authorized"] is False
    assert len(alerts.calls) == len(TO) and alerts.calls[0]["data"]["var1"] == "UnauthorisedLogin"


def test_an_authorised_login_is_not_texted(alerts):
    assert _login(alerts, "Arun", True, TURNING)["authorized"] is True
    assert alerts.calls == []


# ---- endpoints

def test_status_endpoint_hides_secrets(sms):
    body = A.app.test_client().get("/api/sms/status").get_data(as_text=True)
    assert API_KEY not in body and TO[0] not in body
    assert json.loads(body)["configured"] is True


def test_the_test_text_only_works_from_the_laptop_itself(sms):
    client = A.app.test_client()
    other = client.post("/api/sms/test", environ_overrides={"REMOTE_ADDR": "192.168.1.50"})
    assert other.status_code == 403 and sms.calls == []
    local = client.post("/api/sms/test", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert local.status_code == 200 and len(sms.calls) == len(TO)


def test_the_test_text_says_when_it_is_not_set_up(sms):
    sms.path.unlink()
    res = A.app.test_client().post("/api/sms/test", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert res.status_code == 400 and "not set up" in res.get_json()["message"]


# ---- readable wording and the map link (template 101: "Your {var1} is currently at {var2}.")

def test_readable_wording_with_a_map_link_in_free_style(sms):
    write_settings(sms, template_id="101", var1="alert_text", var2="location", value_style="free")
    sms_alert.notify("ACCIDENT", {"lat": 13.05, "lon": 80.25, "driver": "Arun"})
    call = sms.calls[0]
    assert call["params"] == {"ID": "101"}
    assert call["data"]["var1"] == "vehicle accident"
    assert call["data"]["var2"] == "https://maps.google.com/?q=13.05000,80.25000"


def test_plain_style_still_strips_everything_but_letters_and_digits(sms):
    write_settings(sms, var1="alert_text", var2="location", value_style="plain")
    sms_alert.notify("ACCIDENT", {"lat": 13.05, "lon": 80.25})
    data = sms.calls[0]["data"]
    assert data["var1"] == "vehicle accident"
    assert data["var2"] == "lat 13 0500 N lon 80 2500 E"             # coordinates in words: no link, no punctuation
    assert all(c.isalnum() or c == " " for c in data["var1"] + data["var2"]) and "http" not in data["var2"]


def test_without_a_position_the_location_says_so(sms):
    write_settings(sms, var1="alert_text", var2="location", value_style="free")
    sms_alert.notify("ACCIDENT", {"lat": None, "lon": None})
    assert sms.calls[0]["data"]["var2"] == "unknown location"


def test_free_style_removes_control_characters_and_long_text(sms):
    write_settings(sms, var1="driver", var2="driver", value_style="free")
    junk = "Arun" + chr(13) + chr(10) + chr(0) + "x" * 300
    sms_alert.notify("ACCIDENT", {"driver": junk})
    value = sms.calls[0]["data"]["var1"]
    assert len(value) == 100 and all(ord(c) >= 32 for c in value)


def test_bare_style_sends_the_link_without_https(sms):
    write_settings(sms, template_id="101", var1="alert_text", var2="location", value_style="bare")
    sms_alert.notify("ACCIDENT", {"lat": 13.05, "lon": 80.25})
    assert sms.calls[0]["data"]["var2"] == "maps.google.com/?q=13.05000,80.25000"
