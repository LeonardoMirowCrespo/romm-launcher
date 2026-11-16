#!/usr/bin/env python3
"""RomM Launcher - play your RomM library in RetroArch, with saves kept in RomM.

    romm_launcher.py               pick a game from your library and play it
    romm_launcher.py zelda         play the game matching "zelda"
    romm_launcher.py --list        show your library
    romm_launcher.py --upload oot  push this computer's save of a game to RomM
    romm_launcher.py --gui         open the launcher as a window
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

The code is in the rommlauncher/ package next to this file; see
rommlauncher/__init__.py for a map of its modules.
"""

import sys

from rommlauncher.cli import main

if __name__ == "__main__":
    sys.exit(main())
