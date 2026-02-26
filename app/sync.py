"""Sync the SQLite database to iCloud Drive for cross-device backup.

Copies the DB file to ~/Library/Mobile Documents/com~apple~CloudDocs/TimeTracker/
periodically. iCloud handles the actual cloud sync. To restore on a new machine,
run: python -m app.sync restore
"""

import shutil
import logging
import time
import threading
from pathlib import Path

import config

log = logging.getLogger(__name__)


def _ensure_sync_dir():
    config.ICLOUD_SYNC_DIR.mkdir(parents=True, exist_ok=True)


def sync_to_icloud():
    """Copy the local DB to iCloud Drive."""
    if not config.DB_PATH.exists():
        return
    try:
        _ensure_sync_dir()
        dest = config.ICLOUD_SYNC_DIR / "timetracker.db"
        shutil.copy2(str(config.DB_PATH), str(dest))
        # Also copy WAL if it exists (for consistency)
        wal = Path(str(config.DB_PATH) + "-wal")
        if wal.exists():
            shutil.copy2(str(wal), str(dest) + "-wal")
        log.info("Synced DB to iCloud: %s", dest)
    except Exception as e:
        log.error("iCloud sync failed: %s", e)


def restore_from_icloud():
    """Copy the iCloud DB back to local. For use on a new machine."""
    src = config.ICLOUD_SYNC_DIR / "timetracker.db"
    if not src.exists():
        print(f"No iCloud backup found at {src}")
        return False
    if config.DB_PATH.exists():
        backup = Path(str(config.DB_PATH) + ".local-backup")
        shutil.copy2(str(config.DB_PATH), str(backup))
        print(f"Local DB backed up to {backup}")
    shutil.copy2(str(src), str(config.DB_PATH))
    wal_src = Path(str(src) + "-wal")
    if wal_src.exists():
        shutil.copy2(str(wal_src), str(config.DB_PATH) + "-wal")
    print(f"Restored DB from iCloud: {src} -> {config.DB_PATH}")
    return True


def sync_to_turso():
    """Push local DB to Turso cloud."""
    try:
        from app.turso import sync_local_to_turso
        sync_local_to_turso()
    except Exception as e:
        log.error("Turso sync failed: %s", e)


def start_sync_thread():
    """Start a background thread that periodically syncs to iCloud and Turso."""
    def loop():
        while True:
            time.sleep(config.ICLOUD_SYNC_INTERVAL)
            sync_to_icloud()
            sync_to_turso()

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    # Initial sync
    sync_to_icloud()
    sync_to_turso()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        restore_from_icloud()
    else:
        print("Usage: python -m app.sync restore")
        print(f"  Restores DB from iCloud ({config.ICLOUD_SYNC_DIR})")
