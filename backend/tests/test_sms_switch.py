"""The admin panel's SMS on/off button."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as A  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
import sms_alert  # noqa: E402

FRONT = Path(A.FRONTEND_DIR)


@pytest.fixture()
def sms(tmp_path, monkeypatch):
    for key in ("ACCIALERT_SMS_API_KEY", "ACCIALERT_SMS_TEMPLATE_ID", "ACCIALERT_SMS_TO", "ACCIALERT_SMS_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "sms_settings.json"
    path.write_text(json.dumps({"api_key": "secret-key-123", "template_id": "103", "to_numbers": ["918220454887"]}), encoding="utf-8")
    monkeypatch.setattr(config, "SMS_SETTINGS_FILE", path)
    monkeypatch.setattr(sms_alert, "RUN_IN_BACKGROUND", False)
    sent = []
    monkeypatch.setattr(sms_alert, "_send_all", lambda *a, **k: sent.append(a))
    with database.get_db_connection() as conn:
        conn.execute("DELETE FROM sms_log")
        conn.commit()
    sms_alert.reset_for_tests()
    return sent


def post(enabled):
    return A.app.test_client().post("/api/sms/enabled", json={"enabled": enabled})


def test_on_by_default(sms):
    assert A.app.test_client().get("/api/sms/status").get_json()["enabled"] is True


def test_off_stops_every_text_including_sos(sms):
    assert post(False).get_json()["enabled"] is False
    assert sms_alert.notify("ACCIDENT", {"driver": "Arun"}) is False
    assert sms_alert.notify("SOS", {"driver": "Arun"}) is False
    assert sms == []
    assert A.app.test_client().get("/api/sms/status").get_json()["active"] is False


def test_on_again_sends(sms):
    post(False)
    assert post(True).get_json()["enabled"] is True
    assert sms_alert.notify("ACCIDENT", {"driver": "Arun"}) is True
    assert len(sms) == 1


def test_the_choice_survives_a_restart(sms):
    post(False)
    assert json.loads(sms_alert._switch_file().read_text(encoding="utf-8")) == {"enabled": False}
    assert sms_alert.load_settings()["enabled"] is False          # read again from disk, like a new start


def test_the_key_and_numbers_are_not_touched(sms):
    before = config.SMS_SETTINGS_FILE.read_text(encoding="utf-8")
    post(False)
    assert config.SMS_SETTINGS_FILE.read_text(encoding="utf-8") == before


def test_environment_variable_still_wins(sms, monkeypatch):
    post(True)
    monkeypatch.setenv("ACCIALERT_SMS_ENABLED", "0")
    assert sms_alert.load_settings()["enabled"] is False


@pytest.mark.parametrize("bad", [None, "yes", 1, 0, [], {}])
def test_only_true_or_false_is_accepted(sms, bad):
    assert post(bad).status_code == 400


def test_the_change_needs_the_admin_login(sms, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_AUTH_REQUIRED", True)
    assert post(False).status_code == 401
    assert sms_alert.load_settings()["enabled"] is True


def test_the_button_is_in_the_admin_panel(sms):
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    assert 'id="btn-sms-switch"' in html
    js = (FRONT / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
    assert "/api/sms/enabled" in js
