"""Tilt is measured from where the car rests, and only a tilt that lasts counts (no false accident from a touch)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import device_api  # noqa: E402

REST = (1.01, -0.02, -0.21)          # this car: the sensor board reads about 99 degrees when "from Z" (X is the upright axis)


@pytest.fixture(autouse=True)
def fresh():
    device_api.reset_tilt_reference()
    yield
    device_api.reset_tilt_reference()


def tilt(vec, device="CAR1"):
    return device_api.tilt_from_rest(device, *vec)


def test_the_first_reading_is_the_resting_direction():
    assert tilt(REST) == 0.0
    assert tilt(REST) == pytest.approx(0.0, abs=0.5)


@pytest.mark.parametrize("rest", [(0, 0, 1), (0, 0, -1), (1, 0, 0), (0, -1, 0), REST])
def test_any_way_the_board_is_fixed_reads_zero_at_rest(rest):
    tilt(rest)
    for _ in range(5):
        assert tilt(rest) == pytest.approx(0.0, abs=0.5)


def test_a_touch_that_lasts_one_or_two_readings_does_not_count():
    tilt(REST)
    tilt(REST)
    assert tilt((0.3, -0.3, -0.9)) < config.THRESHOLDS["tilt"]["warning"]        # the spike (like the 164 degrees reading)
    assert tilt((0.3, -0.3, -0.9)) < config.THRESHOLDS["tilt"]["warning"]
    assert tilt(REST) == pytest.approx(0.0, abs=0.5)


def test_a_car_on_its_side_counts_once_it_lasts():
    tilt(REST)
    side = (0.0, 1.0, -0.2)                                                      # rolled about 90 degrees
    values = [tilt(side) for _ in range(4)]
    assert values[-1] >= config.THRESHOLDS["tilt"]["accident"]
    assert values[0] < config.THRESHOLDS["tilt"]["accident"] <= values[2]        # not at once: after the third reading


def test_a_roll_over_is_never_absorbed_as_the_new_rest():
    tilt(REST)
    side = (0.0, 1.0, -0.2)
    for _ in range(500):
        value = tilt(side)
    assert value >= config.THRESHOLDS["tilt"]["accident"]


def test_a_car_parked_on_a_gentle_slope_is_followed():
    tilt(REST)
    slope = (0.97, -0.02, -0.38)                                                # about 10 degrees from rest
    for _ in range(300):
        value = tilt(slope)
    assert value < 3.0


def test_reset_makes_the_next_reading_the_new_rest():
    tilt(REST)
    side = (0.0, 1.0, -0.2)
    for _ in range(4):
        tilt(side)
    device_api.reset_tilt_reference("CAR1")
    assert tilt(side) == 0.0


def test_each_device_has_its_own_rest():
    tilt(REST, "A")
    tilt((0, 0, 1), "B")
    assert tilt(REST, "A") == pytest.approx(0.0, abs=0.5) and tilt((0, 0, 1), "B") == pytest.approx(0.0, abs=0.5)


def test_a_dead_sensor_gives_no_tilt():
    tilt(REST)
    assert tilt((0.0, 0.0, 0.0)) == 0.0


def reading(x, y, z, gyro=(0.0, 0.0, 0.0), **extra):
    r = {"device_id": "CAR1", "accelerometer.x": x * 9.80665, "accelerometer.y": y * 9.80665, "accelerometer.z": z * 9.80665,
         "gyroscope.x": gyro[0], "gyroscope.y": gyro[1], "gyroscope.z": gyro[2], "speed_rpm": 0, "alcohol_adc": 500,
         "distance_cm": 100, "gps.latitude": 0, "gps.longitude": 0, "gps.fix_valid": False,
         "alert_accident": False, "alert_alcohol": False, "alert_obstacle": False, "alert_overspeed": False}
    r.update(extra)
    return r


def test_the_dashboard_packet_uses_it():
    assert device_api.to_dashboard_packet(reading(*REST))["tilt"] == 0.0
    assert device_api.to_dashboard_packet(reading(*REST))["tilt"] == pytest.approx(0.0, abs=0.5)
    assert config.THRESHOLDS["tilt"]["accident"] == 50.0


# ---- a roll-over is violent; turning the car by hand is gentle

SIDE = (0.0, 1.0, -0.2)                         # rolled about 90 degrees


def run_side(gyro, **extra):
    """Rest, then lie on the side for several readings. Returns the last dashboard packet."""
    device_api.to_dashboard_packet(reading(*REST))
    packet = None
    for _ in range(5):
        packet = device_api.to_dashboard_packet(reading(*SIDE, gyro=gyro, **extra))
    return packet


def test_by_default_a_big_tilt_is_an_accident_by_itself(monkeypatch):
    monkeypatch.setattr(config, "TILT_REQUIRE_SHOCK", False)
    packet = run_side(gyro=(40.0, 30.0, 10.0))                    # slow turn: no knock, no fast spin
    assert packet["tilt"] >= 50 and packet["tilt_violent"] is True


def test_with_the_shock_rule_a_slow_turn_by_hand_is_not_violent(monkeypatch):
    monkeypatch.setattr(config, "TILT_REQUIRE_SHOCK", True)
    packet = run_side(gyro=(40.0, 30.0, 10.0))                    # the false accident had at most 82 deg/s and 1.08 g
    assert packet["tilt"] >= 50 and packet["tilt_violent"] is False


def test_a_fast_spin_is_violent(monkeypatch):
    monkeypatch.setattr(config, "TILT_REQUIRE_SHOCK", True)
    packet = run_side(gyro=(300.0, 0.0, 0.0))
    assert packet["tilt"] >= 60 and packet["tilt_violent"] is True


def test_a_short_knock_missed_by_the_one_sample_is_caught_by_the_peaks(monkeypatch):
    monkeypatch.setattr(config, "TILT_REQUIRE_SHOCK", True)
    packet = run_side(gyro=(10.0, 0.0, 0.0), peak_gyro_dps=400.0)     # the ESP32 saw 400 deg/s at some moment of the second
    assert packet["tilt_violent"] is True


def test_the_server_raises_no_accident_for_a_slow_turn_but_does_for_a_roll_over(monkeypatch):
    from backend import app as app_module
    monkeypatch.setattr(app_module.sms_alert, "notify", lambda *a, **k: True)
    app_module.state.active_alerts = {}
    app_module.process_telemetry_packet({"spd": 0, "dist": 500, "tilt": 99, "tilt_violent": False, "az": -0.2, "auth": 1}, source="TEST")
    assert "ACCIDENT" not in app_module.state.active_alerts
    app_module.process_telemetry_packet({"spd": 0, "dist": 500, "tilt": 99, "tilt_violent": True, "az": -0.2, "auth": 1}, source="TEST")
    assert "ACCIDENT" in app_module.state.active_alerts
    app_module.state.active_alerts = {}


def test_the_optional_peaks_are_read_and_bad_ones_are_ignored():
    body = {"device_id": "CAR1", "timestamp": "2026-11-15T13:00:00+05:30",
            "sensors": {"alcohol_adc": 500, "distance_cm": 100, "accelerometer": {"x": 0, "y": 0, "z": 9.8},
                        "gyroscope": {"x": 0, "y": 0, "z": 0}, "speed_rpm": 0, "ir_pulse_count": 0,
                        "gps": {"latitude": 0, "longitude": 0, "fix_valid": False, "satellites": 0},
                        "peak_accel_g": 2.2, "peak_gyro_dps": "fast"},
            "alerts": {"alcohol": False, "overspeed": False, "accident": False, "obstacle": False, "buzzer": False}}
    values, errors, _ = device_api.validate_payload(body)
    assert not errors and values["peak_accel_g"] == 2.2 and "peak_gyro_dps" not in values


# ---- left and right are the same

import math


def rolled(angle_deg):
    """The resting vector turned about the car's X axis by angle_deg (positive and negative = the two sides)."""
    a = math.radians(angle_deg)
    return (0.0, math.sin(a), math.cos(a))


@pytest.mark.parametrize("side", [+1, -1])
def test_tilting_to_either_side_is_measured_the_same(side):
    tilt((0.0, 0.0, 1.0))
    values = [tilt(rolled(side * 55)) for _ in range(4)]
    assert values[-1] == pytest.approx(55.0, abs=1.0)
    assert values[-1] >= config.THRESHOLDS["tilt"]["accident"]


@pytest.mark.parametrize("angle", [-60, -51, 51, 60])
def test_fifty_degrees_or_more_on_either_side_raises_the_accident_alert(monkeypatch, angle):
    from backend import app as app_module
    monkeypatch.setattr(app_module.sms_alert, "notify", lambda *a, **k: True)
    monkeypatch.setattr(config, "TILT_REQUIRE_SHOCK", False)
    app_module.state.active_alerts = {}
    device_api.reset_tilt_reference()
    device_api.to_dashboard_packet(reading(0.0, 0.0, 1.0))                        # rest
    packet = None
    for _ in range(4):
        x, y, z = rolled(angle)
        packet = device_api.to_dashboard_packet(reading(x, y, z))
    app_module.process_telemetry_packet(dict(packet, auth=1), source="TEST")
    assert "ACCIDENT" in app_module.state.active_alerts
    app_module.state.active_alerts = {}


@pytest.mark.parametrize("angle", [-40, 40])
def test_below_fifty_degrees_is_not_an_accident(monkeypatch, angle):
    from backend import app as app_module
    monkeypatch.setattr(config, "TILT_REQUIRE_SHOCK", False)
    app_module.state.active_alerts = {}
    device_api.reset_tilt_reference()
    device_api.to_dashboard_packet(reading(0.0, 0.0, 1.0))
    for _ in range(4):
        x, y, z = rolled(angle)
        packet = device_api.to_dashboard_packet(reading(x, y, z))
    app_module.process_telemetry_packet(dict(packet, auth=1), source="TEST")
    assert "ACCIDENT" not in app_module.state.active_alerts
    app_module.state.active_alerts = {}


# ---- speed limit 25 km/h

@pytest.mark.parametrize("speed,expected", [(10, False), (25, False), (25.1, True), (40, True)])
def test_overspeed_above_25_kmh(speed, expected):
    from backend import app as app_module
    app_module.state.active_alerts = {}
    app_module.process_telemetry_packet({"spd": speed, "dist": 500, "auth": 1, "lat": 13.0, "lon": 80.0, "fix": 1}, source="TEST")
    assert ("OVERSPEED" in app_module.state.active_alerts) is expected
    app_module.state.active_alerts = {}
