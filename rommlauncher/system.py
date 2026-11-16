"""Facts about this computer and the app: name, operating system, core file
extension, and the optional keyring.
"""

from __future__ import annotations

import os
import sys

APP_NAME = "RomM Launcher"
IS_WINDOWS = os.name == "nt"

# RetroArch cores are <name>_libretro.dll on Windows, .so on Linux, .dylib on macOS.
CORE_EXT = ".dll" if IS_WINDOWS else ".dylib" if sys.platform == "darwin" else ".so"
CORE_EXTS = (".dll", ".so", ".dylib")

# This system's section in config.json (see config.py). Other modules read it as
# `system.OS_KEY`, so tests can pretend to be on another system.
OS_KEY = "windows" if IS_WINDOWS else "macos" if sys.platform == "darwin" else "linux"
OS_KEYS = ("windows", "linux", "macos")
OS_NAMES = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}

# The RomM password can be remembered in the system keyring, if `keyring` is installed.
KEYRING_SERVICE = "romm-launcher"
try:
    import keyring  # type: ignore

    HAVE_KEYRING = True
except Exception:  # not installed, or no keyring backend available
    keyring = None
    HAVE_KEYRING = False
