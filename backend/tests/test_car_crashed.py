"""An obstacle 4.0 cm away or closer means the car has crashed: a CAR CRASHED alert and a text to the owner."""

import pytest

from backend import app as A
from backend import config, sms_alert


@pytest.fixture()
def texts(monkeypatch):
    sent = []
    monkeypatch.setattr(A.sms_alert, "notify", lambda kind, info=None, force=False: sent.append(kind) or True)
    A.state.active_alerts = {}
    yield sent
    A.state.active_alerts = {}


def reading(dist):
    A.state.active_alerts = {}
    A.process_telemetry_packet({"spd": 0, "dist": dist, "auth": 1, "lat": 13.0, "lon": 80.0, "fix": 1}, source="TEST")


@pytest.mark.parametrize("dist", [4.4, 4.0, 3.9, 2.0, 1.0, 0.5, 0.0])
def test_four_cm_or_closer_is_a_crash_and_is_texted(texts, dist):
    reading(dist)
    assert "CAR CRASHED" in A.state.active_alerts
    assert "CAR CRASHED" in texts


@pytest.mark.parametrize("dist", [4.5, 4.6, 5, 24, 25, 80, 500])
def test_farther_than_four_cm_is_not_a_crash(texts, dist):
    reading(dist)
    assert "CAR CRASHED" not in A.state.active_alerts
    assert "CAR CRASHED" not in texts


def test_the_obstacle_alert_still_works_between_four_and_twenty_five_cm(texts):
    reading(10)
    assert "OBSTACLE DETECTED" in A.state.active_alerts and "CAR CRASHED" not in A.state.active_alerts
    assert "OBSTACLE DETECTED" not in texts            # obstacles alone are still not texted


def test_the_text_says_crashed_car_with_the_position():
    settings = {"var1": "alert_text", "var2": "location", "value_style": "bare"}
    var1, var2 = sms_alert.build_variables("CAR CRASHED", {"lat": 12.842, "lon": 80.2253}, settings)
    assert var1 == "crashed car" and var2 == "maps.google.com/?q=12.84200,80.22530"


def test_the_limit_is_in_the_settings():
    assert config.THRESHOLDS["obstacle"]["crash"] == 4.5 and "CAR CRASHED" in config.SMS_ALERT_TYPES


# ---- the "unauthorised driver" alert needs a moving car

@pytest.mark.parametrize("speed,expected", [(0, False), (1.5, False), (2.0, False), (2.1, True), (25, True)])
def test_a_locked_car_raises_the_unauthorised_driver_alert_only_when_it_moves(monkeypatch, speed, expected):
    monkeypatch.setattr(A.sms_alert, "notify", lambda *a, **k: True)
    A.state.active_alerts = {}
    monkeypatch.setattr(A.state, "active_driver_auth", 0)
    A.process_telemetry_packet({"spd": speed, "auth": 0, "dist": 500, "lat": 13.0, "lon": 80.0, "fix": 1}, source="TEST")
    assert ("UNAUTHORISED DRIVER" in A.state.active_alerts) is expected
    A.state.active_alerts = {}


def test_at_four_cm_only_the_crash_message_is_raised_not_the_plain_obstacle_one(texts):
    reading(4.0)
    assert "CAR CRASHED" in A.state.active_alerts and "OBSTACLE DETECTED" not in A.state.active_alerts


def test_just_outside_the_crash_range_it_is_the_obstacle_message():
    A.state.active_alerts = {}
    A.process_telemetry_packet({"spd": 0, "dist": 6.0, "auth": 1, "lat": 13.0, "lon": 80.0, "fix": 1}, source="TEST")
    assert "OBSTACLE DETECTED" in A.state.active_alerts and "CAR CRASHED" not in A.state.active_alerts
    A.state.active_alerts = {}


# ---- "Dist: none" (the ESP32 sends 500, the dashboard shows "> 400") is a crash too

def esp32_reading(distance):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import device_api
    r = {"device_id": "CRASHCAR", "accelerometer.x": 0.0, "accelerometer.y": 0.0, "accelerometer.z": 9.80665,
         "gyroscope.x": 0.0, "gyroscope.y": 0.0, "gyroscope.z": 0.0, "speed_rpm": 0, "alcohol_adc": 500,
         "distance_cm": distance, "gps.latitude": 12.84, "gps.longitude": 80.22, "gps.fix_valid": False,
         "alert_accident": False, "alert_alcohol": False, "alert_obstacle": False, "alert_overspeed": False}
    return device_api.to_dashboard_packet(r)


@pytest.mark.parametrize("distance,expected", [(500.0, True), (450.0, True), (400.1, True), (400.0, False), (150.0, False), (45.6, False)])
def test_the_device_packet_tells_when_the_sensor_has_no_echo(distance, expected):
    assert esp32_reading(distance)["no_echo"] is expected


def test_no_echo_raises_the_car_crashed_alert_and_the_text(texts):
    A.state.active_alerts = {}
    A.process_telemetry_packet(dict(esp32_reading(500.0), auth=1), source="TEST")
    assert "CAR CRASHED" in A.state.active_alerts and "CAR CRASHED" in texts
    assert "OBSTACLE DETECTED" not in A.state.active_alerts
    A.state.active_alerts = {}


def test_a_normal_distance_from_the_esp32_is_not_a_crash(texts):
    A.state.active_alerts = {}
    A.process_telemetry_packet(dict(esp32_reading(150.0), auth=1), source="TEST")
    assert "CAR CRASHED" not in A.state.active_alerts and texts == []
    A.state.active_alerts = {}


def test_the_no_echo_rule_can_be_switched_off(texts, monkeypatch):
    monkeypatch.setattr(A.config, "CRASH_ON_NO_ECHO", False)
    A.state.active_alerts = {}
    A.process_telemetry_packet(dict(esp32_reading(500.0), auth=1), source="TEST")
    assert "CAR CRASHED" not in A.state.active_alerts
    A.state.active_alerts = {}


def test_a_packet_without_the_flag_is_not_changed_by_it(texts):
    """USB or older packets have no no_echo flag: a large distance there is never a crash."""
    A.state.active_alerts = {}
    A.process_telemetry_packet({"spd": 0, "dist": 500, "auth": 1, "lat": 13.0, "lon": 80.0, "fix": 1}, source="TEST")
    assert "CAR CRASHED" not in A.state.active_alerts
    A.state.active_alerts = {}
