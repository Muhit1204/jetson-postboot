"""Timestamped backups into ./backups/ before any system file changes
(GUARDRAILS 3.2/5.6). Backups are never overwritten: name collisions get a
numeric suffix so repeated runs in the same second keep every copy.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path


def _timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _unique_path(backups_dir, name):
    candidate = backups_dir / name
    counter = 1
    while candidate.exists():
        candidate = backups_dir / "{}.{}".format(name, counter)
        counter += 1
    return candidate


def backup_file(src, backups_dir, timestamp=None):
    """Copy src into backups_dir as <timestamp>-<basename>; return the copy."""
    src = Path(src)
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp if timestamp is not None else _timestamp()
    dest = _unique_path(backups_dir, "{}-{}".format(stamp, src.name))
    shutil.copy2(str(src), str(dest))
    return dest


def backup_text(name, text, backups_dir, timestamp=None):
    """Write captured text (e.g. an sfdisk dump) as <timestamp>-<name>."""
    backups_dir = Path(backups_dir)
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp if timestamp is not None else _timestamp()
    dest = _unique_path(backups_dir, "{}-{}".format(stamp, name))
    dest.write_text(text, encoding="utf-8")
    return dest
