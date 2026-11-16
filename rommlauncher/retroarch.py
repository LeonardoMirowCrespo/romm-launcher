"""RetroArch on this computer: where it is (Windows build, Flatpak, Steam), its
retroarch.cfg, its installed cores, and its core options files (.opt).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from .system import CORE_EXT, CORE_EXTS, IS_WINDOWS
from .util import write_new_file


STEAM_RETROARCH = (
    Path.home() / ".steam" / "debian-installation" / "steamapps" / "common" / "RetroArch"
)
# RetroArch from Flathub. In config, "retroarch" is then "flatpak:<app id>" instead of a
# program path; it runs as `flatpak run <app id>`, and keeps its settings, cores and saves
# in ~/.var/app/<app id>/config/retroarch. It may read the whole computer
# (--filesystem=host), so ROMs can stay in the launcher's folder.
FLATPAK_PREFIX = "flatpak:"
FLATPAK_RETROARCH = "flatpak:org.libretro.RetroArch"
FLATPAK_APPS = Path.home() / ".var" / "app"
# Where RetroArch lives by default: the standalone Windows build, or Flatpak on Linux.
DEFAULT_RETROARCH = (
    "C:/RetroArch-Win64/retroarch.exe" if IS_WINDOWS
    else "/Applications/RetroArch.app/Contents/MacOS/RetroArch" if sys.platform == "darwin"
    else FLATPAK_RETROARCH
)


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


def flatpak_id(retroarch: str) -> str | None:
    """The Flatpak app id if RetroArch is set to "flatpak:<app id>", else None."""
    value = str(retroarch)
    if not value.startswith(FLATPAK_PREFIX):
        return None
    return value[len(FLATPAK_PREFIX):].strip() or None


_flatpak_locations: dict[str, Path | None] = {}


def flatpak_location(app: str) -> Path | None:
    """Where an installed Flatpak app's files are (its /app is <this>/files), or None if
    it isn't installed (or there's no flatpak command)."""
    if app not in _flatpak_locations:
        try:
            result = subprocess.run(["flatpak", "info", "--show-location", app],
                                    capture_output=True, text=True, timeout=30)
            location = result.stdout.strip() if result.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            location = ""
        _flatpak_locations[app] = Path(location) if location else None
    return _flatpak_locations[app]


def retroarch_home(retroarch: str) -> Path:
    """RetroArch's own folder: where its retroarch.cfg, cores and saves are by default."""
    app = flatpak_id(retroarch)
    if app:
        return FLATPAK_APPS / app / "config" / "retroarch"
    return Path(retroarch).expanduser().parent


def retroarch_installed(retroarch: str) -> bool:
    app = flatpak_id(retroarch)
    return flatpak_location(app) is not None if app else Path(retroarch).expanduser().is_file()


def retroarch_command(retroarch: str) -> list[str]:
    """The command that starts RetroArch (arguments go after it)."""
    app = flatpak_id(retroarch)
    return ["flatpak", "run", app] if app else [str(Path(retroarch).expanduser())]


def read_retroarch_cfg(retroarch: str) -> dict[str, str]:
    """RetroArch's own settings (retroarch.cfg), so saves go where RetroArch expects them.

    The standalone Windows build keeps retroarch.cfg next to retroarch.exe, Linux in
    ~/.config/retroarch, Flatpak in ~/.var/app/<app id>/config/retroarch. Paths starting
    with ":" are relative to RetroArch's program folder. For Flatpak, paths inside the
    app (/app/...) are turned into where those files really are on this computer."""
    app = flatpak_id(retroarch)
    if app:
        exe_dir = Path("/app/bin")
        candidates = [retroarch_home(retroarch) / "retroarch.cfg"]
    else:
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
                value = exe_dir / value[1:].lstrip("\\/")
                value = value.as_posix() if app else str(value)
            if app and value.startswith("/app/") and flatpak_location(app):
                value = str(flatpak_location(app) / "files" / value[len("/app/"):])
            values[key] = value
        return values
    return {}


def core_save_folder(saves_dir: str, cores_dir: str, core_file: str, info_dir: str | None = None) -> str:
    """The folder RetroArch writes this core's saves into.

    RetroArch names it after the core's display name, which is in the core's
    .info file. Falls back to a few known names, then to the saves folder."""
    name = core_display_name(cores_dir, core_file, info_dir)
    return str(Path(saves_dir) / name) if name else saves_dir


def core_display_name(cores_dir: str, core_file: str, info_dir: str | None = None) -> str | None:
    """The core's name as RetroArch shows it ("melonDS DS"), from its .info file, else a
    known name. RetroArch names the core's save and settings folders after it."""
    cid = core_id(core_file)
    name = None
    base = Path(cores_dir)
    for info in (
        *([Path(info_dir) / f"{cid}_libretro.info"] if info_dir and info_dir != "default" else []),
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
    return name or KNOWN_CORE_FOLDERS.get(cid)


def retroarch_config_dir(retroarch: str, ra_cfg: dict) -> Path:
    """RetroArch's "config" folder, where core options (.opt files) are kept."""
    configured = ra_cfg.get("rgui_config_directory")
    if configured and configured != "default":
        return Path(configured).expanduser()
    home = retroarch_home(retroarch)
    if IS_WINDOWS or flatpak_id(retroarch) or (home / "retroarch.cfg").exists():
        return home / "config"
    return Path.home() / ".config" / "retroarch" / "config"


def core_options_file(retroarch: str, ra_cfg: dict, core_name: str, rom_file: Path) -> Path:
    """The core options file RetroArch will use for this game, in its own order: this
    game's file, then the content folder's, then the core's (or one file for all cores
    if "global_core_options" is on)."""
    folder = retroarch_config_dir(retroarch, ra_cfg) / core_name
    if ra_cfg.get("game_specific_options", "true") == "true":
        for name in (rom_file.stem, rom_file.parent.name):
            if (folder / f"{name}.opt").is_file():
                return folder / f"{name}.opt"
    if ra_cfg.get("global_core_options") == "true":
        configured = ra_cfg.get("core_options_path")
        if configured:
            return Path(configured).expanduser()
        return folder.parent.parent / "retroarch-core-options.cfg"
    return folder / f"{core_name}.opt"


def set_core_options(path: Path, values: dict[str, str]) -> None:
    """Set some options in a RetroArch options file, leaving every other line as it is."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    todo = dict(values)
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([\w.-]+)\s*=", line)
        if m and m.group(1) in todo:
            lines[i] = f'{m.group(1)} = "{todo.pop(m.group(1))}"'
    lines += [f'{k} = "{v}"' for k, v in todo.items()]
    write_new_file(path, ("\n".join(lines) + "\n").encode("utf-8"))
