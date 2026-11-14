"""ESP32 pairing, per-device tokens and the command queue (GET /api/v1/device/commands + ack)."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as server  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
import device_registry  # noqa: E402

MAC = "AA:BB:CC:DD:EE:01"
SAMPLE = {
    "timestamp": "2026-11-14T11:00:00+05:30",
    "sensors": {
        "alcohol_adc": 1250, "distance_cm": 45.6,
        "accelerometer": {"x": 0.12, "y": 0.08, "z": 9.72},
        "gyroscope": {"x": 0.02, "y": 0.01, "z": 0.03},
        "speed_rpm": 85, "ir_pulse_count": 20,
        "gps": {"latitude": 12.841901, "longitude": 80.224845, "fix_valid": False, "satellites": 0},
    },
    "alerts": {"alcohol": False, "overspeed": False, "accident": False, "obstacle": False, "buzzer": False},
}


@pytest.fixture()
def client():
    for d in device_registry.list_devices():
        device_registry.remove_device(d["device_id"])
    device_registry.ensure_default_device()
    device_registry.clear_failures()
    return server.app.test_client()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def pair(client, mac=MAC, device_id=config.DEVICE_ID, drop_zone_list=True):
    device_registry.open_pairing(device_id, mac)
    res = client.post("/api/v1/device/register", json={"mac": mac, "firmware": "1.0.0"})
    assert res.status_code == 201, res.get_json()
    if drop_zone_list:       # registering queues the zone list for the ESP32; most tests are about other commands
        with device_registry._connect() as conn:
            conn.execute("DELETE FROM device_commands WHERE type='ZONES'")
            conn.commit()
    return res.get_json()["token"]


def queue(client, command, value=None):
    body = {"command": command}
    if value is not None:
        body["value"] = value
    assert client.post("/api/command", json=body).status_code == 200


def get_commands(client, token, **params):
    return client.get("/api/v1/device/commands", headers=bearer(token), query_string=params)


# Pairing and tokens

def test_device_id_is_configured_on_the_backend(client):
    assert [d["device_id"] for d in device_registry.list_devices()] == [config.DEVICE_ID]
    assert device_registry.list_devices()[0]["status"] == "pending"


def test_register_is_refused_while_no_pairing_window_is_open(client):
    res = client.post("/api/v1/device/register", json={"mac": MAC})
    assert res.status_code == 403
    assert res.get_json()["code"] == "pairing_closed"


def test_register_returns_the_device_id_and_a_token_once(client):
    device_registry.open_pairing(config.DEVICE_ID, MAC)
    res = client.post("/api/v1/device/register", json={"mac": MAC.lower(), "firmware": "1.0.0"})
    body = res.get_json()
    assert res.status_code == 201
    assert body["device_id"] == config.DEVICE_ID
    assert len(body["token"]) >= 40 and body["token_type"] == "Bearer"
    again = client.post("/api/v1/device/register", json={"mac": MAC})
    assert again.status_code == 409 and again.get_json()["code"] == "already_registered"


def test_the_backend_keeps_only_a_hash_of_the_token(client):
    token = pair(client)
    with database.get_db_connection() as conn:
        stored = conn.execute("SELECT token_hash FROM devices WHERE device_id=?", (config.DEVICE_ID,)).fetchone()[0]
    assert stored and stored != token and token not in stored
    assert "token_hash" not in device_registry.get_device(config.DEVICE_ID)


def test_pairing_for_one_mac_is_not_claimed_by_another(client):
    device_registry.open_pairing(config.DEVICE_ID, MAC)
    res = client.post("/api/v1/device/register", json={"mac": "AA:BB:CC:DD:EE:99"})
    assert res.status_code == 403
    assert device_registry.get_device(config.DEVICE_ID)["status"] == "pending"


def test_an_expired_window_cannot_be_used(client):
    device_registry.open_pairing(config.DEVICE_ID)
    with database.get_db_connection() as conn:
        conn.execute("UPDATE devices SET pairing_until=? WHERE device_id=?", (device_registry.now_text(-5), config.DEVICE_ID))
        conn.commit()
    assert client.post("/api/v1/device/register", json={"mac": MAC}).status_code == 403


@pytest.mark.parametrize("body", [{}, {"mac": "nope"}, {"mac": 5}, {"mac": "AA:BB:CC:DD:EE"}])
def test_register_rejects_a_bad_mac(client, body):
    assert client.post("/api/v1/device/register", json=body).status_code == 422


def test_register_needs_json(client):
    assert client.post("/api/v1/device/register", data="x").status_code == 400


def test_repeated_wrong_registrations_are_blocked(client):
    codes = [client.post("/api/v1/device/register", json={"mac": MAC}).status_code for _ in range(12)]
    assert codes[:10] == [403] * 10 and codes[10:] == [429, 429]


def test_reset_makes_the_old_token_useless(client):
    token = pair(client)
    assert get_commands(client, token).status_code == 200
    device_registry.reset_device(config.DEVICE_ID)
    assert get_commands(client, token).status_code == 401


def test_a_disabled_device_is_refused(client):
    token = pair(client)
    device_registry.set_enabled(config.DEVICE_ID, False)
    assert get_commands(client, token).status_code == 401
    device_registry.set_enabled(config.DEVICE_ID, True)
    assert get_commands(client, token).status_code == 200


# Telemetry with a device token

def test_telemetry_needs_no_device_id_with_a_token(client):
    token = pair(client)
    res = client.post("/api/v1/telemetry", json=SAMPLE, headers=bearer(token))
    body = res.get_json()
    assert res.status_code == 201, body
    assert body["device_id"] == config.DEVICE_ID
    assert "commands_pending" in body
    assert device_registry.get_device(config.DEVICE_ID)["last_seen"]


def test_telemetry_may_not_name_another_device(client):
    token = pair(client)
    res = client.post("/api/v1/telemetry", json=dict(SAMPLE, device_id="SOMEONE_ELSE"), headers=bearer(token))
    assert res.status_code == 403 and res.get_json()["code"] == "device_mismatch"


def test_telemetry_with_the_matching_device_id_is_fine(client):
    token = pair(client)
    res = client.post("/api/v1/telemetry", json=dict(SAMPLE, device_id=config.DEVICE_ID), headers=bearer(token))
    assert res.status_code == 201


def test_telemetry_with_a_wrong_token_is_refused(client):
    pair(client)
    res = client.post("/api/v1/telemetry", json=SAMPLE, headers=bearer("wrong"))
    assert res.status_code == 401


def test_the_shared_key_still_sends_telemetry_but_needs_a_device_id(client, monkeypatch):
    key = {"X-API-Key": server.get_api_key()}
    assert client.post("/api/v1/telemetry", json=SAMPLE, headers=key).status_code == 422
    assert client.post("/api/v1/telemetry", json=dict(SAMPLE, device_id="X1"), headers=key).status_code == 201
    monkeypatch.setattr(config, "DEVICE_ALLOW_LEGACY_KEY", False)
    assert client.post("/api/v1/telemetry", json=dict(SAMPLE, device_id="X1"), headers=key).status_code == 401


# Commands

def test_commands_need_a_device_token(client):
    assert client.get("/api/v1/device/commands").status_code == 401
    assert client.get("/api/v1/device/commands", headers={"X-API-Key": server.get_api_key()}).status_code == 401


def test_no_commands_when_nothing_is_queued(client):
    token = pair(client)
    body = get_commands(client, token).get_json()
    assert body["commands"] == [] and body["count"] == 0
    assert body["device_id"] == config.DEVICE_ID


def test_a_dashboard_command_reaches_the_esp32_with_id_type_and_value(client):
    token = pair(client)
    queue(client, "LIMIT", 40)
    body = get_commands(client, token).get_json()
    assert body["count"] == 1
    cmd = body["commands"][0]
    assert cmd["type"] == "LIMIT" and cmd["value"] == 40 and isinstance(cmd["id"], int) and cmd["attempt"] == 1
    assert {"created_at", "expires_at"} <= set(cmd)


def test_commands_without_a_value_have_null(client):
    token = pair(client)
    queue(client, "STOP")
    assert get_commands(client, token).get_json()["commands"][0]["value"] is None


def test_commands_come_oldest_first(client):
    token = pair(client)
    queue(client, "STOP")
    queue(client, "ACK")
    queue(client, "RESET")
    assert [c["type"] for c in get_commands(client, token).get_json()["commands"]] == ["STOP", "ACK", "RESET"]


def test_a_command_is_not_offered_again_straight_away(client):
    token = pair(client)
    queue(client, "STOP")
    assert get_commands(client, token).get_json()["count"] == 1
    assert get_commands(client, token).get_json()["count"] == 0


def test_an_acknowledged_command_never_comes_back(client, monkeypatch):
    monkeypatch.setattr(config, "DEVICE_COMMAND_REDELIVER_S", 0)
    token = pair(client)
    queue(client, "STOP")
    cid = get_commands(client, token).get_json()["commands"][0]["id"]
    res = client.post("/api/v1/device/commands/ack", json={"acks": [{"id": cid, "result": "executed"}]}, headers=bearer(token))
    assert res.status_code == 200 and res.get_json()["acknowledged"] == [cid]
    assert get_commands(client, token).get_json()["count"] == 0


def test_an_unacknowledged_command_is_offered_again_then_given_up(client, monkeypatch):
    monkeypatch.setattr(config, "DEVICE_COMMAND_REDELIVER_S", 0)
    monkeypatch.setattr(config, "DEVICE_COMMAND_MAX_ATTEMPTS", 3)
    token = pair(client)
    queue(client, "STOP")
    attempts = [get_commands(client, token).get_json()["commands"] for _ in range(3)]
    assert [a[0]["attempt"] for a in attempts] == [1, 2, 3]
    assert attempts[0][0]["id"] == attempts[2][0]["id"]
    assert get_commands(client, token).get_json()["count"] == 0
    assert device_registry.list_commands(config.DEVICE_ID)[0]["status"] == "failed"


def test_acknowledging_twice_is_harmless(client):
    token = pair(client)
    queue(client, "RESET")
    cid = get_commands(client, token).get_json()["commands"][0]["id"]
    ack = {"acks": [{"id": cid, "result": "executed"}]}
    assert client.post("/api/v1/device/commands/ack", json=ack, headers=bearer(token)).get_json()["acknowledged"] == [cid]
    second = client.post("/api/v1/device/commands/ack", json=ack, headers=bearer(token))
    assert second.status_code == 200 and second.get_json()["already_done"] == [cid]


def test_a_single_ack_may_be_sent_without_a_list(client):
    token = pair(client)
    queue(client, "ACK")
    cid = get_commands(client, token).get_json()["commands"][0]["id"]
    res = client.post("/api/v1/device/commands/ack", json={"id": cid, "result": "rejected", "detail": "busy"}, headers=bearer(token))
    assert res.get_json()["acknowledged"] == [cid]
    assert device_registry.list_commands(config.DEVICE_ID)[0]["status"] == "failed"


def test_a_device_cannot_acknowledge_another_devices_command(client):
    token = pair(client)
    device_registry.add_device("CAR_2")
    device_registry.open_pairing("CAR_2", "AA:BB:CC:DD:EE:02")
    other = client.post("/api/v1/device/register", json={"mac": "AA:BB:CC:DD:EE:02"}).get_json()["token"]
    queue(client, "STOP")
    other_cmd = get_commands(client, other).get_json()["commands"][0]["id"]
    res = client.post("/api/v1/device/commands/ack", json={"acks": [{"id": other_cmd, "result": "executed"}]}, headers=bearer(token))
    assert res.get_json()["unknown"] == [other_cmd] and res.get_json()["acknowledged"] == []


@pytest.mark.parametrize("body", [
    {}, {"acks": []}, {"acks": "x"}, {"acks": [{"id": 1}]}, {"acks": [{"result": "executed"}]},
    {"acks": [{"id": "1", "result": "executed"}]}, {"acks": [{"id": 1, "result": "maybe"}]},
    {"acks": [{"id": 1, "result": "executed", "detail": 5}]},
])
def test_bad_acknowledgements_are_rejected(client, body):
    token = pair(client)
    res = client.post("/api/v1/device/commands/ack", json=body, headers=bearer(token))
    assert res.status_code == 422 and res.get_json()["code"] == "validation_failed"


def test_a_newer_auth_replaces_an_older_one_still_waiting(client):
    token = pair(client)
    queue(client, "AUTH", 1)
    queue(client, "AUTH", 0)
    cmds = get_commands(client, token).get_json()["commands"]
    assert [(c["type"], c["value"]) for c in cmds] == [("AUTH", 0)]


def test_commands_that_nobody_collected_expire(client, monkeypatch):
    monkeypatch.setattr(config, "DEVICE_COMMAND_TTL_S", -1)
    token = pair(client)
    queue(client, "STOP")
    assert get_commands(client, token).get_json()["count"] == 0
    assert device_registry.list_commands(config.DEVICE_ID)[0]["status"] == "expired"


def test_commands_are_only_queued_for_paired_devices(client):
    queue(client, "STOP")                     # nothing is paired yet, so nothing is queued
    token = pair(client)
    assert get_commands(client, token).get_json()["count"] == 0


def test_the_response_tells_the_esp32_the_current_lock_state(client):
    token = pair(client)
    server.state.active_driver_auth = 0
    assert get_commands(client, token).get_json()["state"] == {"auth": 0}
    server.state.active_driver_auth = 1
    assert get_commands(client, token).get_json()["state"] == {"auth": 1}
    server.state.active_driver_auth = 0


def test_telemetry_reports_how_many_commands_are_waiting(client):
    token = pair(client)
    queue(client, "STOP")
    queue(client, "RESET")
    res = client.post("/api/v1/telemetry", json=copy.deepcopy(SAMPLE), headers=bearer(token))
    assert res.get_json()["commands_pending"] >= 2


def test_values_that_are_not_valid_are_not_queued(client):
    token = pair(client)
    assert device_registry.enqueue_command("FIRE_MISSILES") == []
    assert device_registry.enqueue_command("LIMIT", "fast") == []
    assert device_registry.enqueue_command("LIMIT", 999) == []
    assert get_commands(client, token).get_json()["count"] == 0


# Danger zones are sent to the ESP32 (command ZONES)

def zone_commands(client, token):
    return [c for c in get_commands(client, token).get_json()["commands"] if c["type"] == "ZONES"]


def test_the_zone_list_is_queued_when_the_esp32_registers(client):
    token = pair(client, drop_zone_list=False)
    cmds = zone_commands(client, token)
    assert len(cmds) == 1
    value = cmds[0]["value"]
    assert value["count"] == len(value["zones"]) >= 37                       # the Tamil Nadu list is loaded
    assert set(value["zones"][0]) == {"id", "name", "lat", "lon", "r", "limit", "sev"}
    assert len(value["version"]) == 8


def test_a_zone_change_sends_a_new_list_and_replaces_the_old_one(client):
    token = pair(client)
    first = client.get("/api/zones").get_json()["zones"]
    saved = client.post("/api/zones", json={"code": "ZT1", "name": "Test school zone", "latitude": 13.0, "longitude": 80.0,
                                            "radius_meters": 120, "speed_limit": 25, "severity": "HIGH"})
    assert saved.status_code == 200
    client.post("/api/zones", json={"code": "ZT1", "name": "Test school zone", "latitude": 13.0, "longitude": 80.0,
                                    "radius_meters": 200, "speed_limit": 25, "severity": "HIGH"})
    try:
        cmds = zone_commands(client, token)
        assert len(cmds) == 1                                                  # the older list was replaced
        zone = next(z for z in cmds[0]["value"]["zones"] if z["id"] == "ZT1")
        assert zone["r"] == 200 and zone["limit"] == 25 and cmds[0]["value"]["count"] == len(first) + 1
    finally:
        num = next(z["id"] for z in client.get("/api/zones").get_json()["zones"] if z.get("code") == "ZT1" or z.get("name") == "Test school zone")
        client.delete(f"/api/zones/{num}")


def test_deleting_a_zone_changes_the_version(client):
    token = pair(client)
    client.post("/api/zones", json={"code": "ZT2", "name": "Temp zone", "latitude": 13.1, "longitude": 80.1})
    before = zone_commands(client, token)[0]["value"]["version"]
    num = next(z["id"] for z in client.get("/api/zones").get_json()["zones"] if z.get("name") == "Temp zone")
    assert client.delete(f"/api/zones/{num}").status_code == 200
    after = [c for c in get_commands(client, token).get_json()["commands"] if c["type"] == "ZONES"]
    assert after and after[-1]["value"]["version"] != before


def test_a_zone_list_that_is_too_long_or_wrong_is_not_queued(client):
    pair(client)
    assert device_registry.enqueue_command("ZONES", {"zones": [{}] * (config.DEVICE_MAX_ZONES + 1)}) == []
    assert device_registry.enqueue_command("ZONES", {"zones": "x"}) == []
    assert device_registry.enqueue_command("ZONES", None) == []


def test_the_dashboard_cannot_send_a_zone_list_by_hand(client):
    assert client.post("/api/command", json={"command": "ZONES", "value": {"zones": []}}).status_code == 400
