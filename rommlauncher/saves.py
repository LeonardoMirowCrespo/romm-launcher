"""Saves: one-file saves (FileSave), Citra's 3DS save folders (FolderSave), backups
of replaced saves, RomM's newest save (fetch_remote), and decide(), which picks the
newer save.
"""

from __future__ import annotations

import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from . import paths
from .romm import RommError
from .util import file_hash, parse_time, sha256

if TYPE_CHECKING:
    from .games import Game
    from .romm import RommClient


BACKUPS_TO_KEEP = 20


def backup_bytes(data: bytes, rom_id: int, reason: str, suffix: str) -> Path:
    """Store a copy of a save in the backup folder and prune old backups."""
    folder = paths.BACKUP_DIR / str(rom_id)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = folder / f"{stamp}-{reason}{suffix}"
    n = 1
    while dest.exists():
        n += 1
        dest = folder / f"{stamp}-{reason}-{n}{suffix}"
    dest.write_bytes(data)
    backups = sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
    for old in backups[:-BACKUPS_TO_KEEP]:
        old.unlink(missing_ok=True)
    return dest


def backup_file(path: Path, rom_id: int, reason: str) -> Path | None:
    if not path.exists():
        return None
    return backup_bytes(path.read_bytes(), rom_id, reason, path.suffix)


def citra_save_folder(game: "Game", rom_file: Path) -> Path | None:
    """Citra's save folder for a 3DS game:
    <saves>/Citra/Citra/sdmc/Nintendo 3DS/<id0>/<id1>/title/<high>/<low>/data"""
    target = game.rom.get("save_target")  # RomM gives e.g. "00040000/00033500"
    if not target or not re.fullmatch(r"[0-9a-fA-F]{8}/[0-9a-fA-F]{8}", target):
        tid = game.rom.get("title_id") or title_id_from_rom(rom_file)
        if not tid or not re.fullmatch(r"[0-9a-fA-F]{16}", tid):
            return None
        target = f"{tid[:8]}/{tid[8:]}"
    target = target.lower()
    sd = game.save_dir(rom_file) / "Citra" / "sdmc" / "Nintendo 3DS"
    roots = sorted(sd.glob("*/*/title"))
    for root in roots:
        if (root / target / "data").is_dir():
            return root / target / "data"
    root = roots[0] if roots else sd / ("0" * 32) / ("0" * 32) / "title"
    return root / target / "data"


class FileSave:
    """A save that is one file, like RetroArch's .srm files."""

    def __init__(self, path: Path):
        self.path = path
        self.location = path
        self.upload_name = path.name
        self.suffix = path.suffix

    def hash(self) -> str | None:
        return file_hash(self.path)

    def hash_of(self, data: bytes) -> str:
        return sha256(data)

    def read(self) -> bytes:
        return self.path.read_bytes()

    def write(self, data: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(self.path)

    def modified(self) -> datetime | None:
        try:
            return datetime.fromtimestamp(self.path.stat().st_mtime, timezone.utc)
        except FileNotFoundError:
            return None


class FolderSave:
    """A save that is a whole folder (Citra's 3DS saves). In RomM it is one zip.

    Its hash covers file names and contents only, so the same save gives the same
    hash whether it's a folder here or a zip from RomM."""

    ZIP_DATE = (1980, 1, 1, 0, 0, 0)

    def __init__(self, folder: Path, upload_name: str):
        self.path = folder
        self.location = folder
        self.upload_name = upload_name
        self.suffix = ".zip"

    def files(self) -> list[tuple[str, Path]]:
        if not self.path.is_dir():
            return []
        return sorted(
            (p.relative_to(self.path).as_posix(), p) for p in self.path.rglob("*") if p.is_file()
        )

    @staticmethod
    def _tree_hash(entries) -> str | None:
        lines = [f"{name}\0{sha256(data)}\n" for name, data in sorted(entries)]
        return sha256("".join(lines).encode()) if lines else None

    def hash(self) -> str | None:
        return self._tree_hash((name, p.read_bytes()) for name, p in self.files())

    def hash_of(self, data: bytes) -> str | None:
        return self._tree_hash(self._members(data))

    @staticmethod
    def _members(data: bytes) -> list[tuple[str, bytes]]:
        import io

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                out = []
                for info in z.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename.replace("\\", "/")
                    parts = name.split("/")
                    if name.startswith("/") or ".." in parts or ":" in parts[0]:
                        raise RommError(f"The save zip contains an unsafe path: {name}")
                    out.append((name, z.read(info)))
                return out
        except zipfile.BadZipFile as exc:
            raise RommError(f"The save from RomM is not a valid zip ({exc}).") from exc

    def read(self) -> bytes:
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, p in self.files():
                info = zipfile.ZipInfo(name, date_time=self.ZIP_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
        return buf.getvalue()

    def write(self, data: bytes) -> None:
        """Replace the folder with the zip's contents (unpacked beside it, then swapped)."""
        members = self._members(data)
        stamp = int(time.time())
        tmp = self.path.with_name(f"{self.path.name}.romm-new-{stamp}")
        for name, content in members:
            dest = tmp / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        tmp.mkdir(parents=True, exist_ok=True)
        old = self.path.with_name(f"{self.path.name}.romm-old-{stamp}")
        if self.path.exists():
            self.path.rename(old)
        tmp.rename(self.path)
        shutil.rmtree(old, ignore_errors=True)

    def modified(self) -> datetime | None:
        times = [p.stat().st_mtime for _, p in self.files()]
        return datetime.fromtimestamp(max(times), timezone.utc) if times else None


def title_id_from_rom(rom_file: Path) -> str | None:
    """The 16-hex-digit title ID from a .3ds/.cci (NCSD) or .cxi (NCCH) header."""
    try:
        with open(rom_file, "rb") as f:
            head = f.read(0x120)
    except OSError:
        return None
    if head[0x100:0x104] == b"NCSD":
        return f"{int.from_bytes(head[0x108:0x110], 'little'):016x}"
    if head[0x100:0x104] == b"NCCH":
        return f"{int.from_bytes(head[0x118:0x120], 'little'):016x}"
    return None


@dataclass
class RemoteSave:
    info: dict | None
    data: bytes | None
    hash: str | None = None

    @property
    def updated(self) -> datetime | None:
        return parse_time(self.info.get("updated_at")) if self.info else None


def fetch_remote(client: RommClient, game: Game, save: "FileSave | FolderSave") -> RemoteSave:
    info = client.newest_save(game.id, game.save_exts)
    if not info:
        return RemoteSave(None, None)
    data = client.download_save(info)
    return RemoteSave(info, data, save.hash_of(data))


def decide(local_hash: str | None, remote_hash: str | None, last_synced: str | None) -> str:
    """What to do with the saves before playing.

    Returns one of:
      "none"     - nothing to do (no saves anywhere, or they're identical)
      "download" - RomM's save is newer, put it on this computer
      "keep"     - this computer's save is newer, keep it (upload after playing)
      "conflict" - both changed since the last sync, the player must choose
    """
    if remote_hash is None:
        return "keep" if local_hash else "none"
    if local_hash == remote_hash:
        return "none"
    if local_hash is None or local_hash == last_synced:
        return "download"
    if remote_hash == last_synced:
        return "keep"
    return "conflict"
