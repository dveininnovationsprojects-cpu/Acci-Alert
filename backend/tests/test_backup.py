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


def test_backup_all_covers_both_databases_and_the_face_file(tmp_path):
    import numpy as np
    data = tmp_path / "data"
    data.mkdir()
    zone_conn = db.connect(data / "accialert_zones.db")
    db.log_event(zone_conn, "ZONE_ENTER", "high", driver="Arun")
    dash = sqlite3.connect(str(data / "accialert.db"))
    dash.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, event_type TEXT)")
    dash.execute("INSERT INTO events (event_type) VALUES ('OVERSPEED')")
    dash.commit()
    np.savez(data / "face_gallery.npz", Arun=np.ones((3, 128), np.float32))

    written = backup.backup_all(data, tmp_path / "bk", now=datetime(2026, 1, 1, 10, 0, 0))

    assert written["dashboard"].name == "accialert_20260101_100000.db"
    assert written["zones"].name == "zones_20260101_100000.db"
    assert written["faces"].name == "face_gallery_20260101_100000.npz"
    def first_value(path, sql):
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute(sql).fetchone()[0]
        finally:
            conn.close()                                   # Windows cannot delete a file that is still open

    assert first_value(written["dashboard"], "SELECT event_type FROM events") == "OVERSPEED"
    assert first_value(written["zones"], "SELECT type FROM events") == "ZONE_ENTER"
    assert np.load(written["faces"])["Arun"].shape == (3, 128)

    for minute in (1, 2):                                   # keep=2 prunes each kind separately
        backup.backup_all(data, tmp_path / "bk", keep=2, now=datetime(2026, 1, 1, 10, minute, 0))
    for pattern in ("accialert_*.db", "zones_*.db", "face_gallery_*.npz"):
        assert len(list((tmp_path / "bk").glob(pattern))) == 2
    zone_conn.close()
    dash.close()


def test_one_damaged_database_does_not_stop_the_other_backups(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    sqlite3.connect(str(data / "accialert.db")).close()
    (data / "accialert_zones.db").write_bytes(b"this is not a database" * 500)
    with pytest.raises(RuntimeError, match="accialert_zones.db"):
        backup.backup_all(data, tmp_path / "bk")
    assert len(list((tmp_path / "bk").glob("accialert_*.db"))) == 1       # the healthy one was still saved
    assert not list((tmp_path / "bk").glob("zones_*.db"))                 # and no half-written backup is left
