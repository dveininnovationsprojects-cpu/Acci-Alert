"""
Unit Tests for ESP32SerialCommunicator Module
"""

import json
import pytest
from backend.serial_link import ESP32SerialCommunicator


@pytest.fixture
def comm():
    # Use non-existent port to guarantee mock mode for deterministic testing
    c = ESP32SerialCommunicator(port="COM99_NON_EXISTENT")
    yield c
    c.close()


def test_serial_mock_fallback(comm):
    assert comm.is_mock is True
    assert comm.serial_conn is None


def test_send_auth_authorised(comm):
    success = comm.send_auth(ok=1, driver="Arun")
    assert success is True
    assert comm.last_sent_payload is not None
    assert comm.last_sent_payload["cmd"] == "AUTH"
    assert comm.last_sent_payload["ok"] == 1
    assert comm.last_sent_payload["driver"] == "Arun"


def test_send_auth_unknown(comm):
    success = comm.send_auth(ok=0, driver="Unknown")
    assert success is True
    assert comm.last_sent_payload is not None
    assert comm.last_sent_payload["cmd"] == "AUTH"
    assert comm.last_sent_payload["ok"] == 0
    assert comm.last_sent_payload["driver"] == "Unknown"


def test_send_history_tracking(comm):
    initial_len = len(comm.sent_history)
    comm.send_auth(1, "Kumar")
    comm.send_auth(0, "Unknown")
    assert len(comm.sent_history) == initial_len + 2
    assert comm.sent_history[-2]["payload"]["driver"] == "Kumar"
    assert comm.sent_history[-1]["payload"]["driver"] == "Unknown"

