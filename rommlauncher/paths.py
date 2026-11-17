"""Where the launcher keeps its files: everything in one folder (APP_HOME), next to
romm_launcher.py, or in $ROMM_LAUNCHER_HOME.

Other modules use these as `paths.CONFIG_FILE` etc. (not imported by name), so tests
can point them at a temporary folder.
"""

from __future__ import annotations

import os
from pathlib import Path

from .system import IS_WINDOWS


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
# folder: the launcher's folder (where romm_launcher.py and this package are), or
# $ROMM_LAUNCHER_HOME if that is set.
SCRIPT_DIR = Path(__file__).resolve().parent.parent
APP_HOME = Path(os.environ[HOME_ENV]).expanduser() if os.environ.get(HOME_ENV) else SCRIPT_DIR
CONFIG_FILE = APP_HOME / "config.json"
STATE_FILE = APP_HOME / "state.json"
LIBRARY_CACHE = APP_HOME / "library.json"
BACKUP_DIR = APP_HOME / "backups"
COVER_DIR = APP_HOME / "covers"  # cover art for the GUI, downloaded from RomM
# PC ports the launcher downloads go in APP_HOME/<port>/<system>/ (soh/windows/, 2ship/linux/, ...).
# Where older versions kept things; their contents are moved into APP_HOME on start.
OLD_LOCATIONS = list(dict.fromkeys([
    Path.home() / ".config" / "romm-launcher",
    Path.home() / ".local" / "share" / "romm-launcher",
    *_os_app_dirs(),
]))
OLD_ROMS_DIRS = [Path.home() / "Documents" / "Roms"] + [d / "roms" for d in OLD_LOCATIONS]
