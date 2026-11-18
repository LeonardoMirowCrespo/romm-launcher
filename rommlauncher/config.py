"""config.json: defaults, loading and saving, the per-system sections (windows /
linux / macos) for RetroArch's paths, the ROM folder, and moving things from where
older versions kept them.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import paths
from . import system
from .retroarch import DEFAULT_RETROARCH, retroarch_home
from .system import OS_KEYS
from .ui import dim, say, warn
from .util import load_json, save_json


# config.json is shared between computers: the RomM login, ROM folder and cores are the
# same everywhere, but RetroArch's paths differ per system. Those live in a section per
# system ("windows", "linux", "macos"), and the section for this system is used.
# The PC ports' programs ("soh", "2ship", "sm64coopdx") and data folders ("<port>_data") are
# per system too.
MACHINE_KEYS = ("retroarch", "cores_dir", "saves_dir", "sort_saves_by_core", "soh", "soh_data", "2ship", "2ship_data",
                "sm64coopdx", "sm64coopdx_data")


# RomM platform folder name -> RetroArch core and the save file extensions it writes.
# Cores are named without "_libretro.dll/.so", so the same config works on any OS.
DEFAULT_PLATFORMS = {
    "n64": {"core": "mupen64plus_next", "save_exts": ["srm"]},
    "gba": {"core": "mgba", "save_exts": ["srm", "sav"]},
    "nds": {"core": "melondsds", "save_exts": ["srm", "sav"]},
    "nintendo-dsi": {"core": "melondsds", "save_exts": ["srm", "sav"]},
    # Citra keeps a 3DS save as a folder inside its virtual SD card. It's synced with
    # RomM as one zip of that folder (see FolderSave).
    "3ds": {"core": "citra", "save_exts": ["zip"], "save_layout": "citra"},
    "new-nintendo-3ds": {"core": "citra", "save_exts": ["zip"], "save_layout": "citra"},
    # More platforms. Saves are synced where the core keeps a game's save as one
    # <game>.srm file; cores that keep saves their own way (memory card images, save
    # folders, shared backup RAM) get the core only, and their saves aren't synced.
    "nes": {"core": "fceumm", "save_exts": ["srm"]},
    "famicom": {"core": "fceumm", "save_exts": ["srm"]},
    "snes": {"core": "snes9x", "save_exts": ["srm"]},
    "sfam": {"core": "snes9x", "save_exts": ["srm"]},
    "gb": {"core": "gambatte", "save_exts": ["srm"]},
    "gbc": {"core": "gambatte", "save_exts": ["srm"]},
    "virtualboy": {"core": "mednafen_vb", "save_exts": ["srm"]},
    "genesis": {"core": "genesis_plus_gx", "save_exts": ["srm"]},
    "sms": {"core": "genesis_plus_gx", "save_exts": ["srm"]},
    "gamegear": {"core": "genesis_plus_gx", "save_exts": ["srm"]},
    "sg1000": {"core": "genesis_plus_gx", "save_exts": ["srm"]},
    "sega32": {"core": "picodrive", "save_exts": ["srm"]},
    "tg16": {"core": "mednafen_pce_fast", "save_exts": ["srm"]},
    "wonderswan": {"core": "mednafen_wswan", "save_exts": ["srm"]},
    "wonderswan-color": {"core": "mednafen_wswan", "save_exts": ["srm"]},
    "neo-geo-pocket": {"core": "mednafen_ngp", "save_exts": ["srm"]},
    "neo-geo-pocket-color": {"core": "mednafen_ngp", "save_exts": ["srm"]},
    "psx": {"core": "pcsx_rearmed", "save_exts": ["srm"]},  # memory card 1
    "segacd": {"core": "genesis_plus_gx", "save_exts": []},
    "turbografx-cd": {"core": "mednafen_pce_fast", "save_exts": []},
    "pokemon-mini": {"core": "pokemini", "save_exts": []},
    "atari2600": {"core": "stella", "save_exts": []},
    "atari7800": {"core": "prosystem", "save_exts": []},
    "lynx": {"core": "handy", "save_exts": []},
    "psp": {"core": "ppsspp", "save_exts": []},
    "dc": {"core": "flycast", "save_exts": []},
    "ngc": {"core": "dolphin", "save_exts": []},
    "wii": {"core": "dolphin", "save_exts": []},
    "saturn": {"core": "mednafen_saturn", "save_exts": []},
    "arcade": {"core": "fbneo", "save_exts": []},
    "neogeoaes": {"core": "fbneo", "save_exts": []},
    "neogeomvs": {"core": "fbneo", "save_exts": []},
}
# Platform settings older versions wrote into config.json; dropped when loading.
OLD_PLATFORM_DEFAULTS = {"3ds": {"save_exts": [], "sync_saves": False}}


def default_config() -> dict:
    return {
        "use_romm": True,  # False: no RomM at all; the library is the ROM folder, saves stay here
        "romm_url": "",
        "username": "",
        "roms_dir": "roms",  # relative paths are inside APP_HOME
        "retroarch": DEFAULT_RETROARCH,
        "cores_dir": str(retroarch_home(DEFAULT_RETROARCH) / "cores"),
        "saves_dir": str(retroarch_home(DEFAULT_RETROARCH) / "saves"),
        "sort_saves_by_core": True,
        "auto_install_cores": True,  # download missing cores from libretro
        "show_touch_pointer": True,  # DS/3DS: show the controller's touch pointer
        "platforms": json.loads(json.dumps(DEFAULT_PLATFORMS)),
    }


def guess_os(config: dict) -> str:
    """Which system a config without per-system sections was written on, from its paths."""
    path = next((str(config[k]) for k in ("retroarch", "cores_dir", "saves_dir") if config.get(k)), "")
    if re.match(r"^[A-Za-z]:[\\/]", path) or "\\" in path or path.lower().endswith(".exe"):
        return "windows"
    if path.startswith("/Applications/") or ".app/" in path:
        return "macos"
    return "linux" if path else system.OS_KEY


def load_config() -> dict:
    """Settings for this computer. The section for this system ("windows", "linux",
    "macos") overrides the RetroArch paths; the sections themselves are kept so they can
    be written back. An older config with RetroArch paths at the top level is read as
    the section of the system those paths belong to."""
    raw = load_json(paths.CONFIG_FILE, {})
    flat = {k: raw.pop(k) for k in MACHINE_KEYS if k in raw}
    if flat:
        raw.setdefault(guess_os(flat), flat)
    cfg = default_config()
    cfg.update(raw)
    for key in OS_KEYS:
        if not isinstance(cfg.get(key), dict):
            cfg.pop(key, None)
    cfg.update({k: v for k, v in (cfg.get(system.OS_KEY) or {}).items() if k in MACHINE_KEYS})
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
    if system.OS_KEY in out:
        out[system.OS_KEY].update({k: cfg[k] for k in MACHINE_KEYS if k in cfg})
    return out


def migrate_flat_config() -> None:
    """Rewrite an older config.json (RetroArch paths at the top level) with per-system sections."""
    if not paths.CONFIG_FILE.exists():
        return
    flat = {k: v for k, v in load_json(paths.CONFIG_FILE, {}).items() if k in MACHINE_KEYS}
    if not flat:
        return
    save_json(paths.CONFIG_FILE, config_for_disk(load_config()))
    say(dim(f"Settings: RetroArch paths moved into the \"{guess_os(flat)}\" section of {paths.CONFIG_FILE.name}."))


def migrate_legacy_dirs() -> None:
    """Move settings, data and ROMs into APP_HOME from where they used to be kept
    (older versions, or the app-data folder before ROMM_LAUNCHER_HOME was set).

    Anything that can't be moved (e.g. a ROM open in RetroArch) is left where it was,
    and the move is tried again next time. Nothing in APP_HOME is ever overwritten."""
    for old in paths.OLD_LOCATIONS:
        if old.resolve() == paths.APP_HOME.resolve() or not old.is_dir():
            continue
        for item in sorted(old.iterdir()):
            dest = paths.APP_HOME / item.name
            if dest.exists():
                warn(f"Not moving {item}: {dest} already exists.")
                continue
            try:
                paths.APP_HOME.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item), str(dest))
                say(dim(f"Moved {item} → {dest}"))
            except OSError as exc:
                warn(f"Could not move {item} ({exc}). Close RetroArch; will try again next time.")
                return
        try:
            old.rmdir()  # only if now empty
        except OSError:
            pass
    if not paths.CONFIG_FILE.exists():
        return
    cfg = load_json(paths.CONFIG_FILE, {})
    if not cfg.get("roms_dir"):
        return
    old_roms = roms_root(cfg)
    new_roms = paths.APP_HOME / "roms"
    if old_roms == new_roms or old_roms not in paths.OLD_ROMS_DIRS:
        return  # a ROM folder chosen on purpose is left alone
    if old_roms.is_dir() and not new_roms.exists():
        try:
            paths.APP_HOME.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_roms), str(new_roms))
            say(dim(f"Moved your ROMs: {old_roms} → {new_roms}"))
        except OSError as exc:
            warn(f"Could not move {old_roms} ({exc}). Close RetroArch; will try again next time.")
            return
    if not old_roms.exists():
        cfg["roms_dir"] = "roms"
        save_json(paths.CONFIG_FILE, cfg)


def roms_root(config: dict) -> Path:
    """The ROM folder; a relative "roms_dir" is inside the launcher folder. This system's
    section can have its own "roms_dir" (for an absolute path that only exists here)."""
    here = config.get(system.OS_KEY) if isinstance(config.get(system.OS_KEY), dict) else {}
    path = Path(here.get("roms_dir") or config.get("roms_dir") or "roms").expanduser()
    return path if path.is_absolute() else paths.APP_HOME / path
