#!/usr/bin/env python3
"""RomM Launcher - play your RomM library in RetroArch, with saves kept in RomM.

    romm_launcher.py               pick a game from your library and play it
    romm_launcher.py zelda         play the game matching "zelda"
    romm_launcher.py --list        show your library
    romm_launcher.py --upload oot  push this computer's save of a game to RomM
    romm_launcher.py --setup       change RomM address, login, RetroArch paths
    romm_launcher.py --paths       show where settings, ROMs and backups are kept

When you play a game:
  1. the ROM is downloaded to the launcher's ROM folder (a cache it manages itself)
     if it isn't there yet,
  2. the newest save is fetched from RomM into RetroArch's saves folder
     (whatever it replaces is backed up first),
  3. RetroArch starts; when you close it, the save is uploaded back to RomM.

Everything the launcher keeps (settings, sync state, ROMs, save backups) is in the
folder this script is in (or in $ROMM_LAUNCHER_HOME, if set), so the whole folder can be
copied to another machine. Things from older versions' locations are moved there.

Standard library only. Optional: python3-keyring, to remember the RomM password.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

APP_NAME = "RomM Launcher"
IS_WINDOWS = os.name == "nt"


HOME_ENV = "ROMM_LAUNCHER_HOME"


def _os_app_dirs() -> tuple[Path, Path]:
    """The OS's usual (settings, data) folders for this app."""
    home = Path.home()
    if IS_WINDOWS:
        config = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        data = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    else:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return config / "romm-launcher", data / "romm-launcher"


# Everything the launcher keeps (settings, sync state, ROMs, save backups) lives in one
# folder: the folder this script is in, or $ROMM_LAUNCHER_HOME if that is set.
SCRIPT_DIR = Path(__file__).resolve().parent
APP_HOME = Path(os.environ[HOME_ENV]).expanduser() if os.environ.get(HOME_ENV) else SCRIPT_DIR
DATA_DIR = APP_HOME
CONFIG_FILE = APP_HOME / "config.json"
STATE_FILE = APP_HOME / "state.json"
LIBRARY_CACHE = APP_HOME / "library.json"
BACKUP_DIR = APP_HOME / "backups"
# Where older versions kept things; their contents are moved into APP_HOME on start.
OLD_LOCATIONS = list(dict.fromkeys([
    Path.home() / ".config" / "romm-launcher",
    Path.home() / ".local" / "share" / "romm-launcher",
    *_os_app_dirs(),
]))
OLD_ROMS_DIRS = [Path.home() / "Documents" / "Roms"] + [d / "roms" for d in OLD_LOCATIONS]
BACKUPS_TO_KEEP = 20
KEYRING_SERVICE = "romm-launcher"

STEAM_RETROARCH = (
    Path.home() / ".steam" / "debian-installation" / "steamapps" / "common" / "RetroArch"
)
# Where RetroArch lives by default: the standalone Windows build, or Steam on Linux.
DEFAULT_RETROARCH = (
    Path("C:/RetroArch-Win64/retroarch.exe") if IS_WINDOWS else STEAM_RETROARCH / "retroarch"
)
CORE_EXT = ".dll" if IS_WINDOWS else ".dylib" if sys.platform == "darwin" else ".so"
CORE_EXTS = (".dll", ".so", ".dylib")

# config.json is shared between computers: the RomM login, ROM folder and cores are the
# same everywhere, but RetroArch's paths differ per system. Those live in a section per
# system ("windows", "linux", "macos"), and the section for this system is used.
OS_KEY = "windows" if IS_WINDOWS else "macos" if sys.platform == "darwin" else "linux"
OS_KEYS = ("windows", "linux", "macos")
OS_NAMES = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}
MACHINE_KEYS = ("retroarch", "cores_dir", "saves_dir", "sort_saves_by_core")

# RomM platform folder name -> RetroArch core and the save file extensions it writes.
# Cores are named without "_libretro.dll/.so", so the same config works on any OS.
DEFAULT_PLATFORMS = {
    "n64": {"core": "mupen64plus_next", "save_exts": ["srm"]},
    "gba": {"core": "mgba", "save_exts": ["srm", "sav"]},
    "nds": {"core": "melondsds", "save_exts": ["srm", "sav"]},
    # Citra keeps a 3DS save as a folder inside its virtual SD card. It's synced with
    # RomM as one zip of that folder (see FolderSave).
    "3ds": {"core": "citra", "save_exts": ["zip"], "save_layout": "citra"},
}
# Platform settings older versions wrote into config.json; dropped when loading.
OLD_PLATFORM_DEFAULTS = {"3ds": {"save_exts": [], "sync_saves": False}}

# Core file name -> folder RetroArch sorts its saves into ("Sort Saves into Folders").
# Only used when the core's .info file can't be found.
KNOWN_CORE_FOLDERS = {
    "mupen64plus_next": "Mupen64Plus-Next",
    "parallel_n64": "ParaLLEl N64",
    "mgba": "mGBA",
    "gpsp": "gpSP",
    "vba_next": "VBA Next",
    "melondsds": "melonDS DS",
    "melonds": "melonDS",
    "desmume": "DeSmuME",
    "citra": "Citra",
}

try:
    import keyring  # type: ignore

    HAVE_KEYRING = True
except Exception:  # not installed, or no keyring backend available
    keyring = None
    HAVE_KEYRING = False


# --------------------------------------------------------------------------
# Terminal output
# --------------------------------------------------------------------------

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.name != "nt"


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def red(t: str) -> str:
    return _c("31", t)


def say(text: str = "") -> None:
    print(f"  {text}" if text else "")


def ok(text: str) -> None:
    say(f"{green('✔')} {text}")


def warn(text: str) -> None:
    say(f"{yellow('!')} {text}")


def fail(text: str) -> None:
    say(f"{red('✖')} {text}")


class Quit(Exception):
    """The user asked to leave (q, Ctrl+C or Ctrl+D at a prompt)."""


def ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    end = " " if prompt.endswith(">") else ": "
    try:
        answer = input(f"  {prompt}{hint}{end}").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise Quit
    return answer or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        answer = ask(f"{prompt} ({hint})").lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class Progress:
    """A one-line progress bar: `Downloading  [█████░░░░░]  52%  16.6 / 32.0 MB`."""

    def __init__(self, label: str, total: int | None):
        self.label, self.total, self.last = label, total or None, 0.0

    def __call__(self, done: int) -> None:
        now = time.monotonic()
        if now - self.last < 0.1 and done != self.total:
            return
        self.last = now
        if self.total:
            frac = min(done / self.total, 1)
            bar = "█" * int(frac * 24) + "░" * (24 - int(frac * 24))
            line = f"{self.label}  [{bar}] {frac:4.0%}  {human_size(done)} / {human_size(self.total)}"
        else:
            line = f"{self.label}  {human_size(done)}"
        if sys.stdout.isatty():
            print(f"\r  {line}   ", end="", flush=True)

    def finish(self) -> None:
        if sys.stdout.isatty():
            print("\r" + " " * 78 + "\r", end="", flush=True)


# --------------------------------------------------------------------------
# Config and state
# --------------------------------------------------------------------------


def default_config() -> dict:
    return {
        "romm_url": "",
        "username": "",
        "roms_dir": "roms",  # relative paths are inside APP_HOME
        "retroarch": str(DEFAULT_RETROARCH),
        "cores_dir": str(DEFAULT_RETROARCH.parent / "cores"),
        "saves_dir": str(DEFAULT_RETROARCH.parent / "saves"),
        "sort_saves_by_core": True,
        "platforms": json.loads(json.dumps(DEFAULT_PLATFORMS)),
    }


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


def guess_os(config: dict) -> str:
    """Which system a config without per-system sections was written on, from its paths."""
    path = next((str(config[k]) for k in ("retroarch", "cores_dir", "saves_dir") if config.get(k)), "")
    if re.match(r"^[A-Za-z]:[\\/]", path) or "\\" in path or path.lower().endswith(".exe"):
        return "windows"
    if path.startswith("/Applications/") or ".app/" in path:
        return "macos"
    return "linux" if path else OS_KEY


def load_config() -> dict:
    """Settings for this computer. The section for this system ("windows", "linux",
    "macos") overrides the RetroArch paths; the sections themselves are kept so they can
    be written back. An older config with RetroArch paths at the top level is read as
    the section of the system those paths belong to."""
    raw = load_json(CONFIG_FILE, {})
    flat = {k: raw.pop(k) for k in MACHINE_KEYS if k in raw}
    if flat:
        raw.setdefault(guess_os(flat), flat)
    cfg = default_config()
    cfg.update(raw)
    for key in OS_KEYS:
        if not isinstance(cfg.get(key), dict):
            cfg.pop(key, None)
    cfg.update({k: v for k, v in (cfg.get(OS_KEY) or {}).items() if k in MACHINE_KEYS})
    platforms = json.loads(json.dumps(DEFAULT_PLATFORMS))
    for slug, settings in (cfg.get("platforms") or {}).items():
        settings = dict(settings)
        for key, old in OLD_PLATFORM_DEFAULTS.get(slug, {}).items():
            if settings.get(key) == old:
                del settings[key]
        platforms.setdefault(slug, {}).update(settings)
    cfg["platforms"] = platforms
    cfg.pop("games", None)  # left over from the old GUI version
    return cfg


def config_for_disk(cfg: dict) -> dict:
    """The config as it's written to config.json: RetroArch paths go into this system's
    section (if this system has been set up), other systems' sections are kept as they are."""
    out = {k: v for k, v in cfg.items() if k not in MACHINE_KEYS and k not in OS_KEYS}
    for key in OS_KEYS:
        if key in cfg:
            out[key] = dict(cfg[key])
    if OS_KEY in out:
        out[OS_KEY].update({k: cfg[k] for k in MACHINE_KEYS if k in cfg})
    return out


def migrate_flat_config() -> None:
    """Rewrite an older config.json (RetroArch paths at the top level) with per-system sections."""
    if not CONFIG_FILE.exists():
        return
    flat = {k: v for k, v in load_json(CONFIG_FILE, {}).items() if k in MACHINE_KEYS}
    if not flat:
        return
    save_json(CONFIG_FILE, config_for_disk(load_config()))
    say(dim(f"Settings: RetroArch paths moved into the \"{guess_os(flat)}\" section of {CONFIG_FILE.name}."))


def migrate_legacy_dirs() -> None:
    """Move settings, data and ROMs into APP_HOME from where they used to be kept
    (older versions, or the app-data folder before ROMM_LAUNCHER_HOME was set).

    Anything that can't be moved (e.g. a ROM open in RetroArch) is left where it was,
    and the move is tried again next time. Nothing in APP_HOME is ever overwritten."""
    for old in OLD_LOCATIONS:
        if old.resolve() == APP_HOME.resolve() or not old.is_dir():
            continue
        for item in sorted(old.iterdir()):
            dest = APP_HOME / item.name
            if dest.exists():
                warn(f"Not moving {item}: {dest} already exists.")
                continue
            try:
                APP_HOME.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item), str(dest))
                say(dim(f"Moved {item} → {dest}"))
            except OSError as exc:
                warn(f"Could not move {item} ({exc}). Close RetroArch; will try again next time.")
                return
        try:
            old.rmdir()  # only if now empty
        except OSError:
            pass
    if not CONFIG_FILE.exists():
        return
    cfg = load_json(CONFIG_FILE, {})
    if not cfg.get("roms_dir"):
        return
    old_roms = roms_root(cfg)
    new_roms = APP_HOME / "roms"
    if old_roms == new_roms or old_roms not in OLD_ROMS_DIRS:
        return  # a ROM folder chosen on purpose is left alone
    if old_roms.is_dir() and not new_roms.exists():
        try:
            APP_HOME.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_roms), str(new_roms))
            say(dim(f"Moved your ROMs: {old_roms} → {new_roms}"))
        except OSError as exc:
            warn(f"Could not move {old_roms} ({exc}). Close RetroArch; will try again next time.")
            return
    if not old_roms.exists():
        cfg["roms_dir"] = "roms"
        save_json(CONFIG_FILE, cfg)


def roms_root(config: dict) -> Path:
    """The ROM folder; a relative "roms_dir" is inside the launcher folder. This system's
    section can have its own "roms_dir" (for an absolute path that only exists here)."""
    here = config.get(OS_KEY) if isinstance(config.get(OS_KEY), dict) else {}
    path = Path(here.get("roms_dir") or config.get("roms_dir") or "roms").expanduser()
    return path if path.is_absolute() else APP_HOME / path


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


def backup_bytes(data: bytes, rom_id: int, reason: str, suffix: str) -> Path:
    """Store a copy of a save in the backup folder and prune old backups."""
    folder = BACKUP_DIR / str(rom_id)
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


def core_id(core_file: str) -> str:
    """mupen64plus_next_libretro.dll -> mupen64plus_next (also sent to RomM as `emulator`)."""
    name = core_file
    for ext in CORE_EXTS:
        name = name.removesuffix(ext)
    return name.removesuffix("_libretro")


def find_core(cores_dir: Path, core: str) -> Path | None:
    """The installed core file for a core name like "mupen64plus_next", or None."""
    for name in (core, f"{core_id(core)}_libretro{CORE_EXT}"):
        if (cores_dir / name).is_file():
            return cores_dir / name
    return None


def installed_cores(cores_dir: Path) -> list[str]:
    return sorted(
        p.name for p in cores_dir.glob("*_libretro.*") if p.suffix.lower() in CORE_EXTS
    )


def read_retroarch_cfg(retroarch: str) -> dict[str, str]:
    """RetroArch's own settings (retroarch.cfg), so saves go where RetroArch expects them.

    The standalone Windows build keeps retroarch.cfg next to retroarch.exe, Linux in
    ~/.config/retroarch. Paths starting with ":" are relative to RetroArch's folder."""
    exe_dir = Path(retroarch).expanduser().parent
    candidates = [exe_dir / "retroarch.cfg"]
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "RetroArch" / "retroarch.cfg")
    candidates.append(Path.home() / ".config" / "retroarch" / "retroarch.cfg")
    for cfg in candidates:
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        values = {}
        for m in re.finditer(r'^\s*(\w+)\s*=\s*"(.*)"\s*$', text, re.M):
            key, value = m.groups()
            if value.startswith(":"):
                value = str(exe_dir / value[1:].lstrip("\\/"))
            values[key] = value
        return values
    return {}


def core_save_folder(saves_dir: str, cores_dir: str, core_file: str) -> str:
    """The folder RetroArch writes this core's saves into.

    RetroArch names it after the core's display name, which is in the core's
    .info file. Falls back to a few known names, then to the saves folder."""
    cid = core_id(core_file)
    name = None
    base = Path(cores_dir)
    for info in (
        base / f"{cid}_libretro.info",
        base.parent / "info" / f"{cid}_libretro.info",
        Path.home() / ".config" / "retroarch" / "cores" / f"{cid}_libretro.info",
        Path.home() / ".config" / "retroarch" / "info" / f"{cid}_libretro.info",
    ):
        try:
            match = re.search(r'^corename\s*=\s*"(.+)"', info.read_text(), re.M)
            if match:
                name = match.group(1)
                break
        except OSError:
            continue
    name = name or KNOWN_CORE_FOLDERS.get(cid)
    return str(Path(saves_dir) / name) if name else saves_dir


# --------------------------------------------------------------------------
# RomM API
# --------------------------------------------------------------------------


class RommError(Exception):
    pass


class LoginError(RommError):
    pass


def _origin(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme.lower(), parts.netloc.lower()


class _SameServerRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only within the RomM server, so the login never goes elsewhere."""

    def __init__(self, origin: tuple[str, str]):
        super().__init__()
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(newurl) != self.origin:
            raise RommError(f"Blocked: RomM redirected to another server ({newurl}).")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RommClient:
    TOKEN_SCOPES = "me.read roms.read platforms.read assets.read assets.write"
    # The launcher may only READ from RomM and UPLOAD saves. Every request is checked
    # against this before it's sent (see _check), so nothing in the launcher can delete
    # or change anything else on the server: no DELETE/PUT/PATCH, no POST except these
    # paths, and only with these query parameters (so e.g. RomM's "autocleanup" option
    # for uploads, which deletes older saves, can't be switched on).
    ALLOWED_POSTS = {"/api/saves": {"rom_id", "emulator"}, "/api/token": set()}

    def __init__(self, base_url: str, username: str, password: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.username, self.password = username, password
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.auth = f"Basic {token}"
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_SameServerRedirects(_origin(self.base)))

    def _url(self, path: str, params: dict | None) -> str:
        url = path if path.startswith("http") else f"{self.base}{path}"
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        return url

    def _check(self, method: str, url: str) -> None:
        """Refuse any request that isn't a read or a save upload to this RomM server."""
        if _origin(url) != _origin(self.base):
            raise RommError(f"Blocked: {url} is not on your RomM server ({self.base}).")
        if method == "GET":
            return
        parts = urllib.parse.urlsplit(url)
        prefix = urllib.parse.urlsplit(self.base).path.rstrip("/")
        path = parts.path[len(prefix):] if parts.path.startswith(prefix) else parts.path
        allowed = self.ALLOWED_POSTS.get(path) if method == "POST" else None
        params = set(urllib.parse.parse_qs(parts.query, keep_blank_values=True))
        if allowed is None or not params <= allowed:
            raise RommError(
                f"Blocked: {method} {path}{'?' + parts.query if parts.query else ''}. "
                "The launcher is only allowed to read from RomM and upload saves."
            )

    def _open(self, method, path, params=None, body=None, content_type=None, retry=True):
        url = self._url(path, params)
        self._check(method, url)
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", self.auth)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "romm-launcher")
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            return self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code == 403 and "csrf" in detail.lower() and retry and self.auth.startswith("Basic"):
                # Some RomM setups refuse Basic auth on writes; use an API token instead.
                self._get_token()
                return self._open(method, path, params, body, content_type, retry=False)
            if exc.code == 401:
                raise LoginError("RomM rejected the username or password.") from exc
            if exc.code == 403:
                raise RommError(f"RomM refused access to {path}: {detail}") from exc
            raise RommError(f"RomM answered {exc.code} for {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RommError(
                f"Can't reach RomM at {self.base} ({exc.reason}). Is the server up, and is this computer on its network (VPN)?"
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise RommError(f"RomM at {self.base} did not answer ({exc}).") from exc

    def _get_token(self) -> None:
        form = urllib.parse.urlencode(
            {
                "grant_type": "password",
                "username": self.username,
                "password": self.password,
                "scope": self.TOKEN_SCOPES,
            }
        ).encode()
        url = f"{self.base}/api/token"
        self._check("POST", url)
        req = urllib.request.Request(url, data=form, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                self.auth = f"Bearer {json.loads(resp.read())['access_token']}"
        except Exception as exc:
            raise RommError(f"RomM refused to issue an API token: {exc}") from exc

    def _request(self, method: str, path: str, **kw) -> bytes:
        try:
            with self._open(method, path, **kw) as resp:
                return resp.read()
        except OSError as exc:  # connection dropped mid-transfer
            raise RommError(f"Connection to RomM broke off ({exc}).") from exc

    def _json(self, method: str, path: str, **kw):
        return json.loads(self._request(method, path, **kw) or b"null")

    def check_login(self) -> str:
        """Return the logged-in username, or raise."""
        me = self._json("GET", "/api/users/me")
        if not isinstance(me, dict):
            raise LoginError("RomM rejected the username or password.")
        return me.get("username", "?")

    def list_roms(self, search: str | None = None) -> list[dict]:
        params = {
            "limit": 10000,
            "order_by": "name",
            "with_char_index": "false",
            "with_filter_values": "false",
            "with_rom_id_index": "false",
        }
        if search:
            params["search_term"] = search
        data = self._json("GET", "/api/roms", params=params)
        items = data.get("items", []) if isinstance(data, dict) else data
        return items or []

    def list_saves(self, rom_id: int) -> list[dict]:
        try:
            saves = self._json("GET", "/api/saves", params={"rom_id": rom_id})
            if isinstance(saves, list):
                return saves
        except RommError as exc:
            if " 404 " not in str(exc) and " 405 " not in str(exc):
                raise
        # Older RomM versions: saves are part of the ROM details.
        rom = self._json("GET", f"/api/roms/{rom_id}")
        return rom.get("user_saves", []) if isinstance(rom, dict) else []

    def newest_save(self, rom_id: int, extensions: list[str]) -> dict | None:
        exts = {e.lower().lstrip(".") for e in extensions}
        saves = [
            s
            for s in self.list_saves(rom_id)
            if not s.get("missing_from_fs")
            and (
                (s.get("file_extension") or "").lower().lstrip(".") in exts
                or Path(s.get("file_name") or "").suffix.lower().lstrip(".") in exts
            )
        ]
        if not saves:
            return None
        return max(
            saves,
            key=lambda s: parse_time(s.get("updated_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
        )

    def download_save(self, save: dict) -> bytes:
        try:
            return self._request("GET", f"/api/saves/{save['id']}/content")
        except RommError as exc:
            if not save.get("download_path") or (
                " 404 " not in str(exc) and " 405 " not in str(exc)
            ):
                raise
        # Older RomM versions: use the file's download path.
        path = save["download_path"]
        if not path.startswith("http"):
            origin = urllib.parse.urlsplit(self.base)
            path = f"{origin.scheme}://{origin.netloc}{path}"
        return self._request("GET", path)

    def upload_save(self, rom_id: int, filename: str, data: bytes, emulator: str) -> dict:
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="saveFile"; '
            f'filename="{filename.replace(chr(34), "")}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        result = self._json(
            "POST",
            "/api/saves",
            params={"rom_id": rom_id, "emulator": emulator},
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return result if isinstance(result, dict) else {}

    def download_rom(self, rom: dict, dest: Path, progress=None) -> None:
        """Download a ROM to `dest` (a .zip for multi-file ROMs), via a .part file."""
        name = rom["fs_name"] + (".zip" if rom.get("has_multiple_files") else "")
        path = f"/api/roms/{rom['id']}/content/{urllib.parse.quote(name)}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            with self._open("GET", path, params={"purpose": "play"}) as resp, open(part, "wb") as f:
                done = 0
                while chunk := resp.read(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done)
            part.replace(dest)
        except OSError as exc:  # connection dropped mid-transfer
            part.unlink(missing_ok=True)
            raise RommError(f"The download broke off ({exc}). Run it again to retry.") from exc
        except BaseException:
            part.unlink(missing_ok=True)
            raise


# --------------------------------------------------------------------------
# Games and sync logic (no user interaction here, so it can be tested)
# --------------------------------------------------------------------------


@dataclass
class Game:
    rom: dict  # RomM's ROM record
    config: dict

    @property
    def id(self) -> int:
        return int(self.rom["id"])

    @property
    def name(self) -> str:
        return self.rom.get("name") or self.rom.get("fs_name_no_ext") or self.rom["fs_name"]

    @property
    def platform(self) -> str:
        return self.rom.get("platform_fs_slug") or self.rom.get("platform_slug") or "other"

    @property
    def platform_name(self) -> str:
        return self.rom.get("platform_display_name") or self.platform.upper()

    @property
    def settings(self) -> dict:
        return self.config["platforms"].get(self.platform, {})

    @property
    def core(self) -> str | None:
        return self.settings.get("core")

    @property
    def syncs_saves(self) -> bool:
        return self.settings.get("sync_saves", True) and bool(self.save_exts)

    @property
    def save_exts(self) -> list[str]:
        return self.settings.get("save_exts", ["srm"])

    @property
    def multi_file(self) -> bool:
        return bool(self.rom.get("has_multiple_files"))

    @property
    def rom_dest(self) -> Path:
        """Where the ROM is downloaded to: <ROM folder>/<platform>/<file>."""
        return roms_root(self.config) / self.platform / self.rom["fs_name"]

    def find_local_rom(self, index: dict[str, Path] | None = None) -> Path | None:
        """The playable ROM file on this computer, or None.

        Looks in the platform folder first, then anywhere under the ROMs folder,
        so ROMs you copied there yourself are found too."""
        dest = self.rom_dest
        if self.multi_file:
            return pick_launch_file(dest) if dest.is_dir() else None
        if dest.is_file():
            return dest
        if index is None:
            index = index_roms(roms_root(self.config))
        return index.get(self.rom["fs_name"].lower())

    def save_dir(self, rom_file: Path | None = None) -> Path:
        """Where RetroArch keeps this game's save, following its "Saving" settings:
        Sort Saves into Folders by Core Name / by Content Directory, saves next to content."""
        ra = read_retroarch_cfg(self.config["retroarch"])
        if rom_file and ra.get("savefiles_in_content_dir") == "true":
            return rom_file.parent
        folder = Path(self.config["saves_dir"]).expanduser()
        by_core = ra.get("sort_savefiles_enable")
        by_core = by_core == "true" if by_core else self.config.get("sort_saves_by_core", True)
        if by_core and self.core:
            folder = Path(core_save_folder(str(folder), self.config["cores_dir"], self.core))
        if rom_file and ra.get("sort_savefiles_by_content_enable") == "true":
            folder = folder / rom_file.parent.name
        return folder

    def save(self, rom_file: Path) -> "FileSave | FolderSave | None":
        """This game's save on this computer, or None if it can't be synced."""
        if not self.syncs_saves:
            return None
        if self.settings.get("save_layout") == "citra":
            folder = citra_save_folder(self, rom_file)
            return FolderSave(folder, f"{rom_file.stem}.zip") if folder else None
        return FileSave(self.save_path(rom_file))

    def save_path(self, rom_file: Path) -> Path:
        """The save RetroArch uses: an existing one with any known extension,
        otherwise <ROM name>.<first extension>."""
        folder = self.save_dir(rom_file)
        for ext in self.save_exts:
            candidate = folder / f"{rom_file.stem}.{ext}"
            if candidate.exists():
                return candidate
        return folder / f"{rom_file.stem}.{(self.save_exts or ['srm'])[0]}"


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


def index_roms(roms_dir: Path) -> dict[str, Path]:
    """Every file under the ROMs folder, by lower-case file name."""
    index: dict[str, Path] = {}
    if roms_dir.is_dir():
        for p in roms_dir.rglob("*"):
            if p.is_file() and not p.name.endswith(".part"):
                index.setdefault(p.name.lower(), p)
    return index


def pick_launch_file(folder: Path) -> Path | None:
    """For a multi-file game: the .m3u playlist if there is one, else the largest file."""
    files = [p for p in folder.rglob("*") if p.is_file()]
    playlists = [p for p in files if p.suffix.lower() == ".m3u"]
    if playlists:
        return playlists[0]
    return max(files, key=lambda p: p.stat().st_size, default=None)


def unpack_zip(zip_path: Path, folder: Path) -> None:
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(folder)
    zip_path.unlink()


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


def filter_games(games: list[Game], query: str) -> list[Game]:
    """Games whose name or file name contains every word of the query."""
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    out = []
    for g in games:
        hay = " ".join(
            [g.name, g.rom.get("fs_name", ""), g.platform, g.platform_name]
            + list(g.rom.get("alternative_names") or [])
        ).lower()
        if all(w in hay for w in words):
            out.append(g)
    return out


# --------------------------------------------------------------------------
# The launcher (interactive)
# --------------------------------------------------------------------------


class Launcher:
    def __init__(self):
        self.config = load_config()
        self.state = load_json(STATE_FILE, {})
        self.client: RommClient | None = None
        self.offline = False

    # ----- setup and login --------------------------------------------

    def save_config(self) -> None:
        save_json(CONFIG_FILE, config_for_disk(self.config))

    def needs_setup(self) -> bool:
        return not CONFIG_FILE.exists() or not self.config.get("username") or not self.config.get("romm_url")

    def needs_retroarch_setup(self) -> bool:
        """True when config.json came from another system and has no paths for this one."""
        return OS_KEY not in self.config

    def setup(self) -> None:
        cfg = self.config
        print()
        say(bold("Setup") + dim("  (press Enter to keep the value in [brackets])"))
        print()
        url = ask("RomM address (e.g. https://romm.example.com)", cfg["romm_url"]).rstrip("/")
        while not url:
            url = ask("RomM address (required)").rstrip("/")
        if not re.match(r"^https?://", url):
            url = "https://" + url
        cfg["romm_url"] = url
        cfg["username"] = ask("RomM username", cfg["username"])
        while not cfg["username"]:
            cfg["username"] = ask("RomM username (required)")
        password = self.ask_password(force=True)
        client = RommClient(cfg["romm_url"], cfg["username"], password, timeout=15)
        try:
            user = client.check_login()
            ok(f"Connected to RomM as {bold(user)}.")
            self.client = client
        except RommError as exc:
            fail(str(exc))
            if not ask_yes("Save these settings anyway?", default=False):
                raise Quit
        if HAVE_KEYRING:
            if ask_yes("Remember the password in your system keyring?"):
                try:
                    keyring.set_password(KEYRING_SERVICE, cfg["username"], password)
                except Exception as exc:  # noqa: BLE001
                    warn(f"Could not use the keyring ({exc}); you'll be asked each time.")
        else:
            say(dim("Tip: install python3-keyring and the password is remembered securely."))

        print()
        current = roms_root(cfg)
        answer = ask("Folder for ROMs", str(current))
        if Path(answer).expanduser() != current:
            here = cfg.setdefault(OS_KEY, {})
            if Path(answer).expanduser().is_absolute():
                here["roms_dir"] = answer  # a full path only makes sense on this system
            else:
                here.pop("roms_dir", None)
                cfg["roms_dir"] = answer
        self.setup_retroarch()

    def setup_retroarch(self) -> None:
        """Ask for RetroArch's paths on this system and save them in its section."""
        cfg = self.config
        cfg.setdefault(OS_KEY, {})
        retroarch = self.find_retroarch()
        cfg["retroarch"] = ask("RetroArch program", retroarch)
        ra_dir = Path(cfg["retroarch"]).parent
        ra = read_retroarch_cfg(cfg["retroarch"])
        cfg["cores_dir"] = ask("RetroArch cores folder", ra.get("libretro_directory") or str(ra_dir / "cores"))
        saves = ra.get("savefile_directory")
        cfg["saves_dir"] = ask(
            "RetroArch saves folder",
            saves if saves and saves != "default" else str(ra_dir / "saves"),
        )
        if not ra.get("sort_savefiles_enable"):
            cfg["sort_saves_by_core"] = ask_yes(
                "Is RetroArch's \"Sort Saves into Folders by Core Name\" setting on?",
                cfg["sort_saves_by_core"],
            )
        self.save_config()
        print()
        ok(f"Settings saved to {CONFIG_FILE}")
        self.check_install()

    def find_retroarch(self) -> str:
        current = Path(self.config["retroarch"]).expanduser()
        if current.is_file():
            return str(current)
        for candidate in (
            DEFAULT_RETROARCH,
            STEAM_RETROARCH / "retroarch",
            Path.home() / ".local" / "share" / "Steam" / "steamapps" / "common" / "RetroArch" / "retroarch",
            Path(shutil.which("retroarch") or "/nonexistent"),
        ):
            if candidate.is_file():
                return str(candidate)
        return str(current)

    def check_install(self) -> None:
        """Point out missing RetroArch pieces right away instead of at play time."""
        cfg = self.config
        if not Path(cfg["retroarch"]).expanduser().is_file():
            warn(f"RetroArch not found at {cfg['retroarch']}")
        cores = Path(cfg["cores_dir"]).expanduser()
        for slug, p in cfg["platforms"].items():
            if p.get("core") and not find_core(cores, p["core"]):
                warn(f"{slug.upper()} core not installed yet: {core_id(p['core'])} (you'll be asked when you play)")

    def ask_password(self, force: bool = False) -> str:
        user = self.config["username"]
        if not force and HAVE_KEYRING:
            try:
                stored = keyring.get_password(KEYRING_SERVICE, user)
                if stored:
                    return stored
            except Exception:  # noqa: BLE001 - keyring backend trouble
                pass
        try:
            pw = getpass.getpass(f"  RomM password for {user}: ")
        except (EOFError, KeyboardInterrupt):
            print()
            raise Quit
        if not pw:
            raise Quit
        return pw

    def forget_password(self) -> None:
        if HAVE_KEYRING:
            try:
                keyring.delete_password(KEYRING_SERVICE, self.config["username"])
            except Exception:  # noqa: BLE001
                pass

    def connect(self) -> None:
        """Log in to RomM. On a network problem, offer to continue offline."""
        if self.client:
            return
        for attempt in range(3):
            client = RommClient(self.config["romm_url"], self.config["username"], self.ask_password(force=attempt > 0), timeout=15)
            try:
                client.check_login()
                self.client = client
                return
            except LoginError as exc:
                fail(str(exc))
                self.forget_password()
            except RommError as exc:
                fail(str(exc))
                if LIBRARY_CACHE.exists() and ask_yes(
                    "Continue offline? (games already on this computer only; saves sync next time)"
                ):
                    self.offline = True
                    return
                raise Quit
        raise Quit

    # ----- library ------------------------------------------------------

    def library(self) -> list[Game]:
        if self.client:
            say(dim("Loading your library…"))
            roms = self.client.list_roms()
            save_json(LIBRARY_CACHE, {"roms": roms, "fetched_at": datetime.now().isoformat()})
        else:
            roms = load_json(LIBRARY_CACHE, {"roms": []})["roms"]
        games = [Game(r, self.config) for r in roms if r.get("fs_name")]
        if self.offline:
            index = index_roms(roms_root(self.config))
            games = [g for g in games if g.find_local_rom(index)]
        return games

    def last_played(self, game: Game) -> str:
        return self.state.get(str(game.id), {}).get("last_played", "")

    def print_games(self, games: list[Game], numbered: list[Game]) -> None:
        index = index_roms(roms_root(self.config))
        width = min(max((len(g.name) for g in games), default=20), 52)

        def row(g: Game) -> None:
            numbered.append(g)
            here = green("● on this computer") if g.find_local_rom(index) else dim("○ will download")
            name = g.name if len(g.name) <= width else g.name[: width - 1] + "…"
            say(f"{len(numbered):>4}  {name:<{width}}  {here}")

        recent = sorted((g for g in games if self.last_played(g)), key=self.last_played, reverse=True)[:3]
        if recent and len(games) > 5:
            print()
            say(bold("Continue playing"))
            for g in recent:
                row(g)
        by_platform: dict[str, list[Game]] = {}
        for g in games:
            if g not in recent or len(games) <= 5:
                by_platform.setdefault(g.platform_name, []).append(g)
        for platform in sorted(by_platform):
            print()
            say(bold(platform))
            for g in sorted(by_platform[platform], key=lambda g: g.name.lower()):
                row(g)

    def pick_game(self, games: list[Game], query: str | None) -> Game | None:
        if not games:
            fail("Your RomM library is empty." if not self.offline else "No games on this computer yet.")
            return None
        while True:
            shown = filter_games(games, query) if query else games
            if query and not shown:
                warn(f"Nothing matches “{query}”.")
                query, shown = None, games
            elif query and len(shown) == 1:
                return shown[0]
            numbered: list[Game] = []
            self.print_games(shown, numbered)
            print()
            hint = "Type a number to play, or a few letters to search"
            hint += ". Enter shows everything, q quits." if query else ". q quits."
            say(dim(hint))
            answer = ask(">")
            if answer.lower() in ("q", "quit", "exit"):
                return None
            if answer.isdigit():
                n = int(answer)
                if 1 <= n <= len(numbered):
                    return numbered[n - 1]
                warn(f"Pick a number from 1 to {len(numbered)}.")
                continue
            query = answer or None

    # ----- play -----------------------------------------------------------

    def ensure_rom(self, game: Game) -> Path | None:
        local = game.find_local_rom()
        if local:
            ok(f"ROM: {local}")
            return local
        if not self.client:
            fail("The ROM isn't on this computer, and RomM can't be reached to download it.")
            return None
        size = game.rom.get("fs_size_bytes")
        say(f"The ROM isn't on this computer yet. Downloading {human_size(size) if size else ''}…")
        dest = game.rom_dest
        target = dest.with_name(dest.name + ".zip") if game.multi_file else dest
        progress = Progress("Downloading", size)
        try:
            self.client.download_rom(game.rom, target, progress)
        finally:
            progress.finish()
        if game.multi_file:
            unpack_zip(target, dest)
        local = game.find_local_rom()
        if not local:
            fail(f"Downloaded, but no playable file found in {dest}")
            return None
        ok(f"ROM downloaded to {local}")
        return local

    def ensure_core(self, game: Game) -> Path | None:
        cores_dir = Path(self.config["cores_dir"]).expanduser()
        core = game.core
        found = find_core(cores_dir, core) if core else None
        if found:
            return found
        installed = installed_cores(cores_dir)
        if core:
            warn(f"The core for {game.platform_name} ({core_id(core)}) isn't installed in {cores_dir}.")
            say(dim("Install it in RetroArch: Main Menu → Online Updater → Core Downloader,"))
            say(dim("or pick one of the installed cores below."))
        else:
            say(f"No RetroArch core is set for {game.platform_name} yet.")
        if not installed:
            fail(f"No cores found in {cores_dir}.")
            return None
        print()
        for i, name in enumerate(installed, 1):
            say(f"{i:>4}  {core_id(name)}")
        print()
        while True:
            answer = ask("Core number (q to cancel)")
            if answer.lower() == "q":
                return None
            if answer.isdigit() and 1 <= int(answer) <= len(installed):
                chosen = installed[int(answer) - 1]
                self.config["platforms"].setdefault(game.platform, {})["core"] = core_id(chosen)
                self.save_config()
                ok(f"{game.platform_name} will use {core_id(chosen)} from now on.")
                return cores_dir / chosen

    def mark_synced(self, game: Game, save_hash: str | None, pending: bool = False) -> None:
        entry = self.state.setdefault(str(game.id), {})
        entry.update({"hash": save_hash, "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
        entry["pending_upload"] = pending
        save_json(STATE_FILE, self.state)

    def sync_before_play(self, game: Game, save: FileSave | FolderSave) -> str | None:
        """Bring RomM's newest save to this computer if it's newer.

        Returns the hash of the save RomM has (None if it has none), or raises Quit."""
        say("Checking RomM for your latest save…")
        remote = fetch_remote(self.client, game, save)
        local_hash = save.hash()
        last = self.state.get(str(game.id), {}).get("hash")
        action = decide(local_hash, remote.hash, last)

        if action == "conflict":
            local_time = save.modified()
            print()
            warn("This save changed both here and in RomM since the last sync:")
            say(f"    r  RomM's save           {fmt_time(remote.updated)}")
            say(f"    l  this computer's save  {fmt_time(local_time)}")
            say(dim("    Both are backed up either way."))
            while True:
                choice = ask("Which one do you want to play with? (r/l, q to cancel)").lower()
                if choice in ("r", "l", "q"):
                    break
            if choice == "q":
                raise Quit
            backup_bytes(save.read(), game.id, "local-before-conflict", save.suffix)
            backup_bytes(remote.data or b"", game.id, "romm-before-conflict", save.suffix)
            action = "download" if choice == "r" else "keep"

        if action == "download":
            if local_hash is not None:
                backup_bytes(save.read(), game.id, "replaced-by-romm", save.suffix)
            save.write(remote.data or b"")
            self.mark_synced(game, remote.hash)
            ok(f"Save from RomM ({fmt_time(remote.updated)}) → {save.location}")
        elif action == "keep" and remote.hash is None:
            ok("RomM has no save for this game yet; using the one on this computer.")
            say(dim("It's uploaded to RomM when you close the game."))
        elif action == "keep":
            ok("The save on this computer is newer than RomM's; keeping it.")
        elif remote.hash is None:
            ok("RomM has no save for this game yet (and neither does this computer).")
            say(dim("Starting fresh; the save the game makes is uploaded to RomM when you close it."))
        else:
            self.mark_synced(game, remote.hash)
            ok("Save is already up to date.")
        return remote.hash

    def play(self, game: Game, launch: bool = True) -> None:
        print()
        say(bold(f"▶ {game.name}") + dim(f"  ({game.platform_name})"))
        rom_file = self.ensure_rom(game)
        if not rom_file:
            return
        core = self.ensure_core(game) if launch else None
        if launch and not core:
            return
        save = game.save(rom_file)
        remote_hash: str | None = None
        synced = False
        if not save:
            warn(f"This game's save can't be synced with RomM (unknown save location for {game.platform_name}).")
        elif self.client:
            remote_hash = self.sync_before_play(game, save)
            synced = True
        else:
            warn("Offline: playing with the save on this computer. It uploads next time you're online.")

        if not launch:
            return
        retroarch = Path(self.config["retroarch"]).expanduser()
        if not retroarch.is_file():
            fail(f"RetroArch not found at {retroarch}. Fix it with --setup.")
            return
        entry = self.state.setdefault(str(game.id), {})
        entry["last_played"] = datetime.now().isoformat(timespec="seconds")
        save_json(STATE_FILE, self.state)

        hash_before = save.hash() if save else None
        print()
        say(f"Starting RetroArch. When you're done: {bold('F1 → Close Content')}, then quit RetroArch.")
        try:
            proc = subprocess.Popen(
                [str(retroarch), "-L", str(core), str(rom_file)],
                cwd=str(retroarch.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Ctrl+C in this terminal must not kill the game.
                **(
                    {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                    if IS_WINDOWS
                    else {"start_new_session": True}
                ),
            )
        except OSError as exc:
            fail(f"Could not start RetroArch: {exc}")
            return
        self.wait_for(proc)

        if not save:
            return
        local_hash = save.hash()
        if local_hash is None:
            say("No save file was written, so there's nothing to upload.")
            return
        if local_hash == remote_hash and synced:
            ok("Save unchanged; RomM already has it.")
            return
        if local_hash == hash_before:
            say("The save didn't change while playing, but RomM doesn't have it yet.")
        self.upload(game, save)

    def wait_for(self, proc: subprocess.Popen) -> None:
        say(dim("Waiting for RetroArch to close…"))
        while True:
            try:
                proc.wait()
                return
            except KeyboardInterrupt:
                print()
                warn("RetroArch is still running. The save is uploaded when it closes.")
                if ask_yes("Stop waiting? (then upload later with --upload)", default=False):
                    raise Quit
                say(dim("Still waiting…"))

    def upload(self, game: Game, save: FileSave | FolderSave) -> bool:
        local_hash = save.hash()
        data = save.read()
        if not self.client:
            self.mark_pending(game)
            warn("Offline: the save stays on this computer and uploads next time.")
            return False
        say(f"Uploading your save to RomM ({human_size(len(data))})…")
        try:
            self.backup_romm_copy(game, save, local_hash)
            self.client.upload_save(game.id, save.upload_name, data, core_id(game.core or "unknown"))
        except RommError as exc:
            self.mark_pending(game)
            print()
            fail(red(bold("YOUR SAVE WAS NOT UPLOADED TO RomM.")))
            fail(str(exc))
            say(f"It is safe on this computer: {save.location}")
            say("The launcher will try again next time it starts.")
            return False
        self.mark_synced(game, local_hash)
        ok("Save uploaded to RomM.")
        return True

    def backup_romm_copy(self, game: Game, save: FileSave | FolderSave, new_hash: str | None) -> None:
        """An upload replaces RomM's save of the same name, so keep a copy of that first.
        If the copy can't be made, raise, and nothing is uploaded."""
        try:
            for info in self.client.list_saves(game.id):
                if info.get("file_name") != save.upload_name or info.get("missing_from_fs"):
                    continue
                data = self.client.download_save(info)
                try:
                    same = save.hash_of(data) == new_hash
                except RommError:  # e.g. a broken zip: keep it anyway
                    same = False
                if not same:
                    backup_bytes(data, game.id, "romm-before-upload", save.suffix)
        except (RommError, OSError) as exc:
            raise RommError(f"Couldn't back up the save RomM has now, so it wasn't replaced ({exc})") from exc

    def mark_pending(self, game: Game) -> None:
        entry = self.state.setdefault(str(game.id), {})
        entry["pending_upload"] = True
        entry["pending_name"] = game.name
        save_json(STATE_FILE, self.state)

    def retry_pending(self, games: list[Game]) -> None:
        pending = [g for g in games if self.state.get(str(g.id), {}).get("pending_upload")]
        for game in pending:
            rom_file = game.find_local_rom()
            save = game.save(rom_file) if rom_file else None
            if not save or save.hash() is None:
                continue
            warn(f"The last save of {game.name} never reached RomM. Uploading it now.")
            self.upload(game, save)

    def upload_now(self, game: Game) -> None:
        rom_file = game.find_local_rom()
        if not rom_file:
            fail(f"{game.name} isn't on this computer, so there's no save to upload.")
            return
        save = game.save(rom_file)
        if not save:
            fail(f"Saves for {game.name} can't be synced with RomM.")
            return
        if save.hash() is None:
            fail(f"No save found at {save.location}")
            return
        say(f"{save.location}  ({fmt_time(save.modified())}, {human_size(len(save.read()))})")
        if ask_yes(f"Upload this as the newest save of {game.name}?"):
            self.upload(game, save)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="romm_launcher.py",
        description="Play games from your RomM library in RetroArch, with saves synced to RomM.",
    )
    parser.add_argument("game", nargs="*", help="part of a game's name, e.g. zelda")
    parser.add_argument("--list", action="store_true", help="show your library and exit")
    parser.add_argument("--upload", action="store_true", help="upload this computer's save of a game to RomM")
    parser.add_argument("--no-launch", action="store_true", help="download ROM and save, but don't start RetroArch")
    parser.add_argument("--setup", action="store_true", help="change RomM address, login and RetroArch paths")
    parser.add_argument("--paths", "--backups", action="store_true", dest="paths",
                        help="show where settings, ROMs and save backups are kept")
    args = parser.parse_args(argv)
    query = " ".join(args.game) or None

    migrate_legacy_dirs()
    migrate_flat_config()
    if args.paths:
        cfg = load_config()
        source = f"from {HOME_ENV}" if os.environ.get(HOME_ENV) else "the script's folder"
        print(f"  {'Launcher folder:':<17}{APP_HOME}  ({source})")
        for label, path in (
            ("Settings", CONFIG_FILE),
            ("ROMs", roms_root(cfg)),
            ("Save backups", BACKUP_DIR),
            ("RetroArch", Path(cfg["retroarch"]).expanduser()),
            ("RetroArch saves", Path(cfg["saves_dir"]).expanduser()),
        ):
            print(f"  {label + ':':<17}{path}")
        if OS_KEY not in cfg:
            print(f"  RetroArch isn't set up on {OS_NAMES[OS_KEY]} yet; you'll be asked at the next start.")
        return 0

    app = Launcher()
    print()
    say(bold(APP_NAME))
    try:
        if args.setup or app.needs_setup():
            if not args.setup:
                say("First run: let's connect to your RomM.")
            app.setup()
            if args.setup:
                return 0
        elif app.needs_retroarch_setup():
            print()
            say(f"First start on {OS_NAMES[OS_KEY]} with these settings: where is RetroArch on this computer?")
            app.setup_retroarch()
        app.connect()
        host = urllib.parse.urlsplit(app.config["romm_url"]).hostname
        say(dim(f"{'offline' if app.offline else 'connected to ' + str(host)} · {app.config['username']}"))
        games = app.library()
        if app.client:
            app.retry_pending(games)
        if args.list:
            app.print_games(games, [])
            print()
            return 0
        game = app.pick_game(games, query)
        if not game:
            return 0
        if args.upload:
            if not app.client:
                fail("Can't upload while offline.")
                return 1
            app.upload_now(game)
        else:
            app.play(game, launch=not args.no_launch)
        print()
        return 0
    except Quit:
        say(dim("Bye."))
        return 0
    except RommError as exc:
        fail(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
