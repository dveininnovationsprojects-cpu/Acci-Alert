"""Backup for AcciAlert: both databases and the saved face signatures.

Uses SQLite's online backup API, so it is safe while the server is running
(a plain file copy can miss data that is still in the -wal file).

    python backend/backup.py                 # backup to backend/backups/
    python backend/backup.py --keep 20       # keep the newest 20 backups of each file
    python backend/backup.py --dest D:/usb   # choose another folder

Files written (the time is added to each name):
    accialert_<time>.db     dashboard database: events, zones, drivers, commands
    zones_<time>.db         zone database: zone events, telemetry, zones
    face_gallery_<time>.npz saved face signatures
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db  # noqa: E402

DEFAULT_DEST = Path(__file__).resolve().parent / "backups"


def backup_db(src=db.DEFAULT_DB, dest_dir=DEFAULT_DEST, keep=10, now=None, prefix="accialert"):
    """Create <prefix>_YYYYmmdd_HHMMSS.db in dest_dir, verify it, prune old ones.
    Returns the backup path. Raises if the backup fails its integrity check."""
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"database not found: {src}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    out = dest_dir / f"{prefix}_{stamp}.db"

    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(out))
    try:
        s.backup(d)
        ok = d.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error:
        d.close()
        s.close()
        out.unlink(missing_ok=True)          # do not leave half a backup behind
        raise
    finally:
        d.close()
        s.close()
    if ok != "ok":
        out.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed integrity check: {ok}")

    if keep and keep > 0:
        for old in sorted(dest_dir.glob(f"{prefix}_*.db"))[:-keep]:
            old.unlink()
    return out


def backup_all(data_dir=None, dest_dir=DEFAULT_DEST, keep=10, now=None):
    """Back up the dashboard database, the zone database and the face signatures.
    Returns {"dashboard": path, "zones": path, "faces": path or None}.
    One damaged file does not stop the others; the problems are reported in a RuntimeError at the end."""
    data_dir = Path(data_dir) if data_dir else db.DEFAULT_DB.parent
    dest_dir = Path(dest_dir)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    written = {"dashboard": None, "zones": None, "faces": None}
    problems = []

    for key, name, prefix in (("dashboard", "accialert.db", "accialert"), ("zones", "accialert_zones.db", "zones")):
        try:
            written[key] = backup_db(data_dir / name, dest_dir, keep, now, prefix=prefix)
        except (OSError, sqlite3.Error, RuntimeError) as err:
            problems.append(f"{name}: {err}")

    gallery = data_dir / "face_gallery.npz"
    if gallery.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"face_gallery_{stamp}.npz"
        shutil.copy2(gallery, out)
        written["faces"] = out
        if keep and keep > 0:
            for old in sorted(dest_dir.glob("face_gallery_*.npz"))[:-keep]:
                old.unlink()

    if problems:
        raise RuntimeError("Some backups failed: " + "; ".join(problems))
    return written


def main():
    ap = argparse.ArgumentParser(description="Back up the AcciAlert databases and face signatures")
    ap.add_argument("--data", default=str(db.DEFAULT_DB.parent), help="folder that holds the database files")
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--keep", type=int, default=10, help="backups to keep of each file (0 = keep all)")
    a = ap.parse_args()
    try:
        written = backup_all(a.data, a.dest, a.keep)
    except RuntimeError as err:
        print(err)
        sys.exit(1)
    for label, path in written.items():
        print(f"{label:10s} {path if path else 'nothing to back up'}")


if __name__ == "__main__":
    main()
