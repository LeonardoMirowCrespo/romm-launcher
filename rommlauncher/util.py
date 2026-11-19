"""Small helpers: JSON files, hashes, times, and writing a file safely.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from .ui import warn


def load_json(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            merged = dict(default)
            merged.update(data)
            return merged
    except FileNotFoundError:
        pass
    except Exception as exc:  # corrupt file: keep a copy, start fresh
        shutil.copy2(path, path.with_suffix(f".broken-{int(time.time())}"))
        warn(f"Could not read {path} ({exc}); a copy was kept next to it.")
    return dict(default)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str | None:
    try:
        return sha256(path.read_bytes())
    except FileNotFoundError:
        return None


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fmt_time(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else "unknown time"


def fmt_duration(seconds: float) -> str:
    """Play time: '0 min', '42 min', '3 h 5 min', '120 h'."""
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f"{minutes} min"
    if hours >= 100 or not minutes:
        return f"{hours} h"
    return f"{hours} h {minutes} min"


def write_new_file(dest: Path, body: bytes) -> None:
    """Write via a temporary file, so a half-written core never exists under its real name."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(body)
    tmp.replace(dest)
