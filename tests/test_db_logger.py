"""
Unit Tests for DatabaseLogger Module
"""

import sqlite3
import pytest
from ai.db_logger import DatabaseLogger


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_accialert.db"
    logger = DatabaseLogger(db_path=db_file)
    return logger


def test_tables_created(temp_db):
    with sqlite3.connect(str(temp_db.db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        assert "drivers" in tables
        assert "events" in tables


def test_register_driver(temp_db):
    temp_db.register_enrolled_driver("Arun", 25)
    with sqlite3.connect(str(temp_db.db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name, face_count, status FROM drivers WHERE name='Arun'")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "Arun"
        assert row[1] == 25
        assert row[2] == "ACTIVE"


def test_log_auth_event_authorised(temp_db):
    evt = temp_db.log_auth_event(
        driver="Arun",
        is_authorised=True,
        confidence_distance=32.5,
        similarity_pct=72.9,
        extra_details="Unit test pass"
    )
    assert evt["type"] == "AUTH_OK"
    assert evt["severity"] == "INFO"
    assert evt["driver"] == "Arun"

    recent = temp_db.get_recent_events(limit=5)
    assert len(recent) >= 1
    assert recent[0]["driver"] == "Arun"
    assert recent[0]["type"] == "AUTH_OK"


def test_log_auth_event_unauthorised(temp_db):
    evt = temp_db.log_auth_event(
        driver="Unknown",
        is_authorised=False,
        confidence_distance=48.2,
        similarity_pct=59.8,
        extra_details="Unknown intruder"
    )
    assert evt["type"] == "UNAUTHORISED"
    assert evt["severity"] == "HIGH"
    assert evt["driver"] == "Unknown"

