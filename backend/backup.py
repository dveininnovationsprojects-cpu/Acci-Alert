"""Database backup for AcciAlert.

Uses SQLite's online backup API, so it is safe while the server is running
(a plain file copy can miss data that is still in the -wal file).

    python backend/backup.py                 # backup to backend/backups/
    python backend/backup.py --keep 20       # keep the newest 20 backups
    python backend/backup.py --dest D:/usb   # choose another folder
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

DEFAULT_DEST = Path(__file__).resolve().parent / "backups"


def backup_db(src=db.DEFAULT_DB, dest_dir=DEFAULT_DEST, keep=10, now=None):
    """Create accialert_YYYYmmdd_HHMMSS.db in dest_dir, verify it, prune old ones.
    Returns the backup path. Raises if the backup fails its integrity check."""
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"database not found: {src}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    out = dest_dir / f"accialert_{stamp}.db"

    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(out))
    try:
        s.backup(d)
        ok = d.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        d.close()
        s.close()
    if ok != "ok":
        out.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed integrity check: {ok}")

    if keep and keep > 0:
        for old in sorted(dest_dir.glob("accialert_*.db"))[:-keep]:
            old.unlink()
    return out


def main():
    ap = argparse.ArgumentParser(description="Back up accialert.db")
    ap.add_argument("--src", default=str(db.DEFAULT_DB))
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--keep", type=int, default=10, help="backups to keep (0 = keep all)")
    a = ap.parse_args()
    print("Backup written:", backup_db(a.src, a.dest, a.keep))


if __name__ == "__main__":
    main()
