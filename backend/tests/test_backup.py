import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backup  # noqa: E402
import db  # noqa: E402


def test_backup_contains_data_including_unflushed_wal(tmp_path):
    src = tmp_path / "a.db"
    c = db.connect(src)                      # keep open: data may still be in -wal
    db.log_event(c, "ACCIDENT", "critical", lat=1.0, lon=2.0)
    db.upsert_zone(c, {"id": "Z1", "name": "n", "type": "t", "lat": 1, "lon": 2,
                       "radius_m": 5, "speed_limit": 5, "severity": "low", "active": 1})
    out = backup.backup_db(src, tmp_path / "bk")
    b = sqlite3.connect(str(out))
    assert b.execute("SELECT type FROM events").fetchone()[0] == "ACCIDENT"
    assert b.execute("SELECT COUNT(*) FROM danger_zones").fetchone()[0] == 1
    b.close()
    c.close()


def test_keep_prunes_oldest(tmp_path):
    src = tmp_path / "a.db"
    db.connect(src).close()
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    for i in range(5):
        backup.backup_db(src, tmp_path / "bk", keep=3, now=t0 + timedelta(minutes=i))
    names = sorted(p.name for p in (tmp_path / "bk").glob("*.db"))
    assert names == ["accialert_20260101_120200.db", "accialert_20260101_120300.db",
                     "accialert_20260101_120400.db"]


def test_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.backup_db(tmp_path / "nope.db", tmp_path / "bk")


def test_purge_scheduler_removes_old_telemetry(tmp_path):
    p = tmp_path / "p.db"
    c = db.connect(p)
    db.log_telemetry(c, speed=1, timestamp=time.time() - 25 * 3600)
    db.log_telemetry(c, speed=2, timestamp=time.time())
    stop = db.start_purge_scheduler(p, interval_s=3600)
    for _ in range(50):                      # runs once immediately
        if c.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0] == 1:
            break
        time.sleep(0.1)
    stop()
    assert [r[0] for r in c.execute("SELECT speed FROM telemetry")] == [2]
    c.close()
