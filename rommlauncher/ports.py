"""PC ports: games rebuilt for the PC that play your ROM's game instead of RetroArch.
Two, both by HarbourMasters and built on the same framework (libultraship), so one
`Port` description covers both:

- Ship of Harkinian (SoH): Ocarina of Time (checked against its source, release 9.3).
- 2 Ship 2 Harkinian (2S2H): Majora's Mask (checked against its source, release 5.0.1).

Both have widescreen, any resolution, 60 fps and HD texture packs. How they work:
- They don't run the ROM. Once, they turn it into an archive in their data folder
  (SoH: `oot.o2r`, or `oot-mq.o2r` for Master Quest; 2S2H: `mm.o2r`) and after that only
  need that file. A ROM path on the command line is turned into the archive without a
  file picker; with the archive already there, it would be made again, so the ROM is
  passed only while it's missing. Neither takes a zipped ROM, so a zip from RomM is
  unpacked into a temporary folder for that first start.
- Their data folder (settings, archives, saves) is the folder they're started in on
  Windows, and `$SHIP_HOME` on Linux (the AppImages pick ~/.local/share/soh and
  ~/.local/share/2ship when it isn't set). The launcher starts them in their data folder
  and sets SHIP_HOME, so it always knows where the saves are.
- Saves are JSON files. SoH: `Save/file1.sav` to `file3.sav` and `global.sav`; it also
  leaves `fileN-<time>.bak` copies and writes through `.temp` files, which stay out of the
  sync. 2S2H: `saves/file1.json` to `file3.json` and the game's backup copies
  `fileNbackup.json`; its `fileN_invalid_<time>.json` (saves it couldn't read) and the old
  `global.json` (moved into its settings) stay out.

The launcher can also download them: the latest release from their GitHub page, checked
against the SHA-256 GitHub lists for it, into `<port>/<system>/` in the launcher folder
(`soh/windows/`, `2ship/linux/`, ...). Nothing that's already there is replaced.
"""

from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import zipfile
from dataclasses import dataclass
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
TWOSHIP = "2ship"
RETROARCH = "retroarch"

# Where the ports are downloaded from: their releases on GitHub (tests point these elsewhere).
GITHUB_API = "https://api.github.com"
GITHUB = "https://github.com"
ROM_FOLDER = ".romm-launcher-rom"  # in the data folder: a zipped ROM unpacked for the first start


class PortInstallError(Exception):
    pass


def _words(game: "Game") -> set[str]:
    return set(re.findall(r"[a-z0-9]+", f"{game.name} {game.rom.get('fs_name') or ''}".lower()))


def is_master_quest(game: "Game") -> bool:
    words = _words(game)
    return "master" in words and "quest" in words


@dataclass(frozen=True)
class Port:
    """One PC port. `key` is everything the launcher calls it: the --with choice, the
    config key of its program (and `<key>_data` for its data folder), RomM's "emulator"
    for its saves, `hash_<key>` in state.json and the folder it's downloaded into."""

    key: str
    name: str  # "Ship of Harkinian"
    game: str  # "Ocarina of Time"
    words: tuple[str, ...]  # words the game's name has
    site: str
    repo: str  # on GitHub
    exe: str  # the Windows program
    appimage: str  # glob for the Linux AppImage
    downloads: tuple[tuple[str, str], ...]  # (OS_KEY, end of the release file's name)
    archive: str  # what it makes from the ROM
    settings: str  # its settings file (shows a folder is its data folder)
    save_folder: str
    save_files: str  # regex for the save files that are synced
    linux_data: str  # its data folder in ~/.local/share
    mq_archive: str = ""  # Master Quest's archive (SoH)
    first_run: str = ""  # what it asks on the first start, if anything
    platforms: tuple[str, ...] = ("n64", "ngc")  # it takes the N64 and GameCube releases
    tip: str = "the PC version, with widescreen, high resolution and 60 fps"

    @property
    def data_key(self) -> str:
        return f"{self.key}_data"

    def plays(self, game: "Game") -> bool:
        """The game this port is (N64/GameCube release, not the 3DS remake)."""
        words = _words(game)
        return game.platform in self.platforms and set(self.words) <= words and "3d" not in words

    def archive_for(self, game: "Game") -> str:
        """The file the port makes from this game's ROM."""
        return self.mq_archive if self.mq_archive and is_master_quest(game) else self.archive

    def program(self, config: dict) -> Path | None:
        """Its program; a relative path (one the launcher downloaded) is in the launcher folder."""
        if not config.get(self.key):
            return None
        program = Path(config[self.key]).expanduser()
        return program if program.is_absolute() else paths.APP_HOME / program

    def installed(self, config: dict) -> bool:
        program = self.program(config)
        return bool(program and program.is_file())

    def data_dir(self, config: dict) -> Path | None:
        """Its data folder (settings, archives, saves): config "<key>_data" if set; else on
        Windows the program's folder, elsewhere the program's folder if its files are there
        already (or the launcher installed it), else ~/.local/share/<name> (the AppImage's)."""
        if config.get(self.data_key):
            return Path(config[self.data_key]).expanduser()
        program = self.program(config)
        if not program:
            return None
        if system.IS_WINDOWS or program.parent == self.install_dir():
            return program.parent
        markers = (self.settings, self.archive, self.mq_archive)
        if any(f and (program.parent / f).exists() for f in markers):
            return program.parent
        xdg = os.environ.get("XDG_DATA_HOME", "")
        base = Path(xdg) if xdg and Path(xdg).is_absolute() else Path.home() / ".local" / "share"
        return base / self.linux_data

    def saves_dir(self, config: dict) -> Path | None:
        data = self.data_dir(config)
        return data / self.save_folder if data else None

    def needs_archive(self, config: dict, game: "Game") -> bool:
        """True when the port hasn't made its archive from this game's ROM yet."""
        data, program = self.data_dir(config), self.program(config)
        name = self.archive_for(game)
        return not any(folder and (folder / name).exists() for folder in (data, program and program.parent))

    def launch(self, config: dict, game: "Game", rom_file: Path) -> tuple[list[str], Path, dict]:
        """(command, folder to start in, environment) to start the port. The ROM is passed
        only while its archive doesn't exist (unzipped first if it's a zip; `cleanup`
        removes that copy after the game closes)."""
        program, data = self.program(config), self.data_dir(config)
        data.mkdir(parents=True, exist_ok=True)
        command = [str(program)]
        if self.needs_archive(config, game):
            command.append(str(unpacked_rom(rom_file, data / ROM_FOLDER)))
        env = dict(os.environ)
        if not system.IS_WINDOWS:
            env["SHIP_HOME"] = str(data)
        return command, data, env

    def cleanup(self, config: dict) -> None:
        """Remove a ROM unpacked for the first start (the port has its archive by now)."""
        data = self.data_dir(config)
        if data:
            shutil.rmtree(data / ROM_FOLDER, ignore_errors=True)

    def save(self, config: dict, rom_file: Path) -> "PortSave | None":
        folder = self.saves_dir(config)
        if not folder:
            return None
        return PortSave(folder, f"{rom_file.stem} ({self.name}).zip", self)

    def find(self, config: dict) -> str:
        """The configured program if it's there, else the first one found in the usual places."""
        current = config.get(self.key) or ""
        if current and self.installed(config):
            return current
        mine = self.installed_program(self.install_dir())
        if mine:
            return config_path(mine)
        home = Path.home()
        if system.IS_WINDOWS:
            drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
            short = self.exe.rsplit(".", 1)[0]
            names = dict.fromkeys([short, short.upper(), self.name])
            folders = [base / n for base in (drive, drive / "Games", home / "Games") for n in names]
            folders += sorted(home.glob(f"Downloads/{short}*"))
            candidates = [f / self.exe for f in folders]
        else:
            candidates = [p for d in (home / "Applications", home / "Games", home / "Downloads", home / ".local" / "bin")
                          for p in sorted(d.glob(self.appimage))]
        which = shutil.which(self.exe.rsplit(".", 1)[0])
        if which:
            candidates.append(Path(which))
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return current

    # ----- downloading it -----------------------------------------------------

    def install_dir(self) -> Path:
        """Where the launcher puts the copy it downloads (its saves and settings end up there too)."""
        return paths.APP_HOME / self.key / system.OS_KEY

    def asset_suffix(self) -> str | None:
        """The end of the release file's name for this computer, or None if there's no build for it."""
        if platform.machine().lower() not in ("amd64", "x86_64", "x64"):
            return None
        return dict(self.downloads).get(system.OS_KEY)

    def latest_release(self) -> dict:
        """The latest release for this computer: version, name, file, size, url, sha256."""
        suffix = self.asset_suffix()
        if not suffix:
            raise PortInstallError(f"{self.name} has no download for this computer here; see {self.site}")
        try:
            release = json.loads(http_download(f"{GITHUB_API}/repos/{self.repo}/releases/latest", timeout=30))
        except (CoreInstallError, ValueError) as exc:
            raise PortInstallError(f"Couldn't ask GitHub for {self.name}'s latest version ({exc}).") from exc
        asset = next((a for a in release.get("assets") or [] if (a.get("name") or "").lower().endswith(suffix)), None)
        if not asset:
            raise PortInstallError(f"GitHub lists no {suffix} file for {self.name} {release.get('tag_name')}.")
        url = asset.get("browser_download_url") or ""
        if not url.startswith(f"{GITHUB}/{self.repo}/releases/download/"):
            raise PortInstallError(f"Unexpected download address for {self.name}: {url}")
        digest = asset.get("digest") or ""
        return {"version": release.get("tag_name") or "?", "name": release.get("name") or "",
                "file": asset["name"], "size": asset.get("size"), "url": url,
                "sha256": digest[7:].lower() if digest.startswith("sha256:") else None}

    def installed_program(self, folder: Path) -> Path | None:
        """The program in a folder the launcher installed it in, if it's there."""
        if not folder.is_dir():
            return None
        if system.OS_KEY == "windows":
            return folder / self.exe if (folder / self.exe).is_file() else None
        return next((p for p in sorted(folder.iterdir()) if p.is_file() and p.name.lower().endswith(".appimage")), None)

    def install(self, release: dict, label: str | None = None) -> Path:
        """Download it into install_dir() and return its program. Checked against GitHub's
        SHA-256 when GitHub lists one; files already in the folder are never replaced (an
        installed copy is returned as it is). A zip is unpacked (debug symbols left out)
        and the folder holding the program moved in."""
        dest = self.install_dir()
        existing = self.installed_program(dest)
        if existing:
            return existing
        try:
            data = http_download(release["url"], label, timeout=120)
        except CoreInstallError as exc:
            raise PortInstallError(str(exc)) from exc
        if release.get("sha256") and sha256(data) != release["sha256"]:
            raise PortInstallError("The download doesn't match the checksum GitHub lists for it; nothing was installed.")
        if not release["file"].lower().endswith(".zip"):
            program = dest / release["file"]
            write_new_file(program, data)
            program.chmod(0o755)
            return program
        windows = system.OS_KEY == "windows"
        tmp = dest.with_name(f"{dest.name}.part")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                members = zf.infolist()
                for info in members:
                    if not safe_member(info.filename):
                        raise PortInstallError(f"The download contains an unsafe path: {info.filename}")
                zf.extractall(tmp, [m for m in members if not m.filename.lower().endswith(".pdb")])
            if windows:
                found = [p for p in tmp.rglob("*") if p.name.lower() == self.exe]
            else:
                found = [p for p in tmp.rglob("*") if p.is_file() and p.name.lower().endswith(".appimage")]
            program = min(found, key=lambda p: len(p.parts), default=None)
            if not program:
                raise PortInstallError(f"{release['file']} doesn't contain {self.exe if windows else 'an AppImage'}.")
            dest.mkdir(parents=True, exist_ok=True)
            for item in program.parent.iterdir():
                if not (dest / item.name).exists():
                    item.rename(dest / item.name)
        except zipfile.BadZipFile as exc:
            raise PortInstallError(f"{release['file']} isn't a valid zip ({exc}).") from exc
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        program = dest / program.name
        if not windows:
            program.chmod(0o755)
        return program


SHIP_OF_HARKINIAN = Port(
    key=SOH, name="Ship of Harkinian", game="Ocarina of Time", words=("ocarina", "time"),
    site="https://www.shipofharkinian.com", repo="HarbourMasters/Shipwright",
    exe="soh.exe", appimage="[Ss]o[Hh]*.[Aa]pp[Ii]mage",
    downloads=(("windows", "-win64.zip"), ("linux", ".appimage")),
    archive="oot.o2r", mq_archive="oot-mq.o2r", settings="shipofharkinian.json",
    save_folder="Save", save_files=r"[^/]+\.sav", linux_data="soh",
    first_run='then asks "Run SoH?": answer Yes.',
)
TWO_SHIP = Port(
    key=TWOSHIP, name="2 Ship 2 Harkinian", game="Majora's Mask", words=("majora", "mask"),
    site="https://github.com/2ship2harkinian/2ship2harkinian", repo="2ship2harkinian/2ship2harkinian",
    exe="2ship.exe", appimage="2[Ss]hip*.[Aa]pp[Ii]mage",
    downloads=(("windows", "-win64.zip"), ("linux", "-linux.zip")),
    archive="mm.o2r", settings="2ship2harkinian.json",
    save_folder="saves", save_files=r"file\d+(backup)?\.json", linux_data="2ship",
)
PORTS = {p.key: p for p in (SHIP_OF_HARKINIAN, TWO_SHIP)}
PLAYERS = {**{p.key: p.name for p in PORTS.values()}, RETROARCH: "RetroArch"}


def port_for(game: "Game") -> Port | None:
    """The PC port that can play this game, if there is one."""
    return next((p for p in PORTS.values() if p.plays(game)), None)


def player_choices(game: "Game") -> list[str]:
    """What can play this game: [] when it's RetroArch only (no question to ask)."""
    port = port_for(game)
    return [port.key, RETROARCH] if port else []


def safe_member(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    return not (name.startswith(("/", "\\")) or ".." in parts or ":" in parts[0])


def unpacked_rom(rom_file: Path, folder: Path) -> Path:
    """The ROM to hand a port: the file itself, or for a zip, its biggest file unpacked
    into `folder` (emptied first)."""
    if rom_file.suffix.lower() != ".zip":
        return rom_file
    shutil.rmtree(folder, ignore_errors=True)
    with zipfile.ZipFile(rom_file) as zf:
        files = [i for i in zf.infolist() if not i.is_dir() and safe_member(i.filename)]
        if not files:
            raise OSError(f"{rom_file.name} has no ROM in it")
        info = max(files, key=lambda i: i.file_size)
        dest = folder / Path(info.filename.replace("\\", "/")).name
        folder.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)
    return dest


def config_path(program: Path) -> str:
    """How config.json stores a port's program: relative to the launcher folder when it's
    inside (so the folder can move), else the full path."""
    try:
        return program.relative_to(paths.APP_HOME).as_posix()
    except ValueError:
        return str(program)


class PortSave(FolderSave):
    """A port's saves: the files in its save folder that match `port.save_files`, synced
    with RomM as one zip (emulator = the port's key). The port's own other files (backups
    of saves it couldn't read, half-written files) are left out and never touched; a save
    file that RomM's newer zip doesn't have is removed (the caller backs the whole save up
    first)."""

    exts = ["zip"]

    def __init__(self, folder: Path, upload_name: str, port: Port):
        super().__init__(folder, upload_name)
        self.port = port
        self.emulator = port.key
        self._pattern = re.compile(port.save_files, re.IGNORECASE)

    def _is_save(self, name: str) -> bool:
        return "/" not in name and bool(self._pattern.fullmatch(name))

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
