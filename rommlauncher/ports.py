"""PC ports: games rebuilt for the PC that play your ROM's game instead of RetroArch.
For now one: Ship of Harkinian (SoH, by HarbourMasters), Ocarina of Time built from the
decompiled game, with widescreen, any resolution, 60 fps and HD texture packs.

How SoH works (checked against its source, release 9.3):
- It doesn't run the ROM. Once, it turns the ROM into `oot.o2r` (`oot-mq.o2r` for
  Master Quest) in its data folder, and after that only needs that file. A ROM path on
  its command line is turned into the archive without a file picker.
- Its data folder (settings, the .o2r files, `Save/`) is the folder it's started in on
  Windows, and `$SHIP_HOME` on Linux and macOS (the AppImage picks ~/.local/share/soh
  when that isn't set). The launcher starts it in its data folder and sets SHIP_HOME, so
  it always knows where the saves are.
- Saves are JSON files in `Save/`: `file1.sav` to `file3.sav` (the three slots) and
  `global.sav`. SoH also leaves `fileN-<time>.bak` copies of saves it couldn't load, and
  writes through `<name>.temp`; those stay out of the sync.

The launcher can also download SoH: the latest release from its GitHub page
(HarbourMasters/Shipwright), the Win64 zip or the Linux AppImage, checked against the
SHA-256 GitHub lists for it, into `soh/<system>/` in the launcher folder. Nothing that's
already there is replaced.
"""

from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from . import paths
from . import system
from .core_downloads import CoreInstallError, http_download
from .saves import FolderSave
from .util import sha256, write_new_file

if TYPE_CHECKING:
    from .games import Game


SOH = "soh"
RETROARCH = "retroarch"
PLAYERS = {SOH: "Ship of Harkinian", RETROARCH: "RetroArch"}

SOH_PLATFORMS = ("n64", "ngc")  # SoH takes the N64 and GameCube releases
SOH_SAVE_FOLDER = "Save"
SOH_SITE = "https://www.shipofharkinian.com"
# Where SoH is downloaded from: its releases on GitHub (tests point these elsewhere).
GITHUB_API = "https://api.github.com"
GITHUB = "https://github.com"
SOH_REPO = "HarbourMasters/Shipwright"


def _words(game: "Game") -> set[str]:
    return set(re.findall(r"[a-z0-9]+", f"{game.name} {game.rom.get('fs_name') or ''}".lower()))


def is_soh_game(game: "Game") -> bool:
    """Ocarina of Time for the N64 or GameCube (not the 3DS remake): SoH can play it."""
    words = _words(game)
    return game.platform in SOH_PLATFORMS and {"ocarina", "time"} <= words and "3d" not in words


def is_master_quest(game: "Game") -> bool:
    words = _words(game)
    return "master" in words and "quest" in words


def soh_archive(game: "Game") -> str:
    """The file SoH makes from this game's ROM."""
    return "oot-mq.o2r" if is_master_quest(game) else "oot.o2r"


def player_choices(game: "Game") -> list[str]:
    """What can play this game: [] when it's RetroArch only (no question to ask)."""
    return [SOH, RETROARCH] if is_soh_game(game) else []


def soh_program(config: dict) -> Path | None:
    """SoH's program; a relative "soh" (one the launcher downloaded) is in the launcher folder."""
    if not config.get("soh"):
        return None
    program = Path(config["soh"]).expanduser()
    return program if program.is_absolute() else paths.APP_HOME / program


def soh_installed(config: dict) -> bool:
    program = soh_program(config)
    return bool(program and program.is_file())


def soh_data_dir(config: dict) -> Path | None:
    """SoH's data folder (settings, .o2r files, Save/): config "soh_data" if set; else on
    Windows the program's folder, elsewhere the program's folder if SoH's files are
    there already, else ~/.local/share/soh (where the AppImage puts them)."""
    if config.get("soh_data"):
        return Path(config["soh_data"]).expanduser()
    program = soh_program(config)
    if not program:
        return None
    if system.IS_WINDOWS or program.parent == soh_install_dir():
        return program.parent
    if any((program.parent / f).exists() for f in ("shipofharkinian.json", "oot.o2r", "oot-mq.o2r")):
        return program.parent
    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".local" / "share"
    return base / "soh"


def soh_needs_archive(config: dict, game: "Game") -> bool:
    """True when SoH hasn't made its .o2r file from this game's ROM yet."""
    data, program = soh_data_dir(config), soh_program(config)
    name = soh_archive(game)
    return not any(folder and (folder / name).exists() for folder in (data, program and program.parent))


def soh_launch(config: dict, game: "Game", rom_file: Path) -> tuple[list[str], Path, dict]:
    """(command, folder to start in, environment) to start SoH. The ROM is passed only
    while its .o2r doesn't exist (with the archive there, SoH would ask to make it again)."""
    program, data = soh_program(config), soh_data_dir(config)
    data.mkdir(parents=True, exist_ok=True)
    command = [str(program)]
    if soh_needs_archive(config, game):
        command.append(str(rom_file))
    env = dict(os.environ)
    if not system.IS_WINDOWS:
        env["SHIP_HOME"] = str(data)
    return command, data, env


def soh_save(config: dict, rom_file: Path) -> "SohSave | None":
    data = soh_data_dir(config)
    if not data:
        return None
    return SohSave(data / SOH_SAVE_FOLDER, f"{rom_file.stem} (Ship of Harkinian).zip")


def find_soh(config: dict) -> str:
    """The configured SoH if it's there, else the first one found in the usual places."""
    current = config.get("soh") or ""
    if current and soh_installed(config):
        return current
    mine = installed_soh(soh_install_dir())
    if mine:
        return config_path(mine)
    home = Path.home()
    if system.IS_WINDOWS:
        drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        folders = [drive / "SoH", drive / "Games" / "SoH", drive / "Games" / "Ship of Harkinian",
                   home / "Games" / "SoH", home / "Games" / "Ship of Harkinian"]
        folders += sorted(home.glob("Downloads/SoH*"))
        candidates = [f / "soh.exe" for f in folders]
    else:
        candidates = [p for d in (home / "Applications", home / "Games", home / "Downloads", home / ".local" / "bin")
                      for p in sorted(d.glob("[Ss]o[Hh]*.[Aa]pp[Ii]mage"))]
    which = shutil.which("soh")
    if which:
        candidates.append(Path(which))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return current


# ----- downloading SoH -------------------------------------------------------

class SohInstallError(Exception):
    pass


def soh_install_dir() -> Path:
    """Where the launcher puts the SoH it downloads (its saves and settings end up there too)."""
    return paths.SOH_DIR / system.OS_KEY


def soh_asset_suffix() -> str | None:
    """The end of the release file's name for this computer, or None if SoH has no build for it."""
    if platform.machine().lower() not in ("amd64", "x86_64", "x64"):
        return None
    return {"windows": "-win64.zip", "linux": ".appimage"}.get(system.OS_KEY)


def latest_soh_release() -> dict:
    """SoH's latest release for this computer: version, name, file, size, url, sha256."""
    suffix = soh_asset_suffix()
    if not suffix:
        raise SohInstallError(f"Ship of Harkinian has no download for this computer here; see {SOH_SITE}")
    try:
        release = json.loads(http_download(f"{GITHUB_API}/repos/{SOH_REPO}/releases/latest", timeout=30))
    except (CoreInstallError, ValueError) as exc:
        raise SohInstallError(f"Couldn't ask GitHub for Ship of Harkinian's latest version ({exc}).") from exc
    asset = next((a for a in release.get("assets") or [] if (a.get("name") or "").lower().endswith(suffix)), None)
    if not asset:
        raise SohInstallError(f"GitHub lists no {suffix} file for Ship of Harkinian {release.get('tag_name')}.")
    url = asset.get("browser_download_url") or ""
    if not url.startswith(f"{GITHUB}/{SOH_REPO}/releases/download/"):
        raise SohInstallError(f"Unexpected download address for Ship of Harkinian: {url}")
    digest = asset.get("digest") or ""
    return {"version": release.get("tag_name") or "?", "name": release.get("name") or "",
            "file": asset["name"], "size": asset.get("size"), "url": url,
            "sha256": digest[7:].lower() if digest.startswith("sha256:") else None}


def installed_soh(folder: Path) -> Path | None:
    """The SoH program in a folder the launcher installed it in, if it's there."""
    if not folder.is_dir():
        return None
    if system.OS_KEY == "windows":
        return folder / "soh.exe" if (folder / "soh.exe").is_file() else None
    return next((p for p in sorted(folder.iterdir()) if p.is_file() and p.name.lower().endswith(".appimage")), None)


def install_soh(release: dict, label: str | None = None) -> Path:
    """Download SoH into soh_install_dir() and return its program. Checked against
    GitHub's SHA-256 when GitHub lists one; files already in the folder are never
    replaced (an installed SoH is returned as it is)."""
    dest = soh_install_dir()
    existing = installed_soh(dest)
    if existing:
        return existing
    try:
        data = http_download(release["url"], label, timeout=120)
    except CoreInstallError as exc:
        raise SohInstallError(str(exc)) from exc
    if release.get("sha256") and sha256(data) != release["sha256"]:
        raise SohInstallError("The download doesn't match the checksum GitHub lists for it; nothing was installed.")
    if not release["file"].lower().endswith(".zip"):
        program = dest / release["file"]
        write_new_file(program, data)
        program.chmod(0o755)
        return program
    tmp = dest.with_name(f"{dest.name}.part")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                parts = info.filename.replace("\\", "/").split("/")
                if info.filename.startswith(("/", "\\")) or ".." in parts or ":" in parts[0]:
                    raise SohInstallError(f"The download contains an unsafe path: {info.filename}")
            zf.extractall(tmp)
        exe = min(tmp.rglob("soh.exe"), key=lambda p: len(p.parts), default=None)
        if not exe:
            raise SohInstallError(f"{release['file']} doesn't contain soh.exe.")
        dest.mkdir(parents=True, exist_ok=True)
        for item in exe.parent.iterdir():
            if not (dest / item.name).exists():
                item.rename(dest / item.name)
    except zipfile.BadZipFile as exc:
        raise SohInstallError(f"{release['file']} isn't a valid zip ({exc}).") from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dest / "soh.exe"


def config_path(program: Path) -> str:
    """How config.json stores SoH's program: relative to the launcher folder when it's inside
    (so the folder can move), else the full path."""
    try:
        return program.relative_to(paths.APP_HOME).as_posix()
    except ValueError:
        return str(program)


class SohSave(FolderSave):
    """Ship of Harkinian's saves: the .sav files in its Save folder (three slots and
    global.sav), synced with RomM as one zip. SoH's own .bak copies and .temp files are
    left out and never touched; a .sav that RomM's newer zip doesn't have is removed
    (the caller backs the whole save up first)."""

    emulator = SOH
    exts = ["zip"]

    @staticmethod
    def _is_save(name: str) -> bool:
        return "/" not in name and name.lower().endswith(".sav")

    def files(self) -> list[tuple[str, Path]]:
        if not self.path.is_dir():
            return []
        return sorted((p.name, p) for p in self.path.iterdir() if p.is_file() and self._is_save(p.name))

    def hash_of(self, data: bytes) -> str | None:
        return self._tree_hash((n, d) for n, d in self._members(data) if self._is_save(n))

    def write(self, data: bytes) -> None:
        members = [(n, d) for n, d in self._members(data) if self._is_save(n)]
        self.path.mkdir(parents=True, exist_ok=True)
        for name, content in members:
            tmp = self.path / f"{name}.romm-new"
            tmp.write_bytes(content)
            tmp.replace(self.path / name)
        keep = {n.lower() for n, _ in members}
        for name, p in self.files():
            if name.lower() not in keep:
                p.unlink()
