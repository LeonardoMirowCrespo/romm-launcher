"""RomM Launcher: play the games in your RomM library in RetroArch, with saves kept in RomM.

Start it with `romm_launcher.py` (or `python -m rommlauncher`). The code, roughly in the
order things depend on each other:

    system.py          this computer: OS, core file extension, optional keyring
    paths.py           where the launcher's files are (config.json, state.json, backups/, ...)
    ui.py              messages, questions, progress bars (terminal, or the window)
    util.py            small helpers: JSON files, hashes, times, safe file writes
    retroarch.py       RetroArch on this computer: install, retroarch.cfg, cores, core options
    config.py          config.json: defaults, per-system sections, migrations
    dualscreen.py      DS/3DS screen layouts and touch-screen controls
    core_downloads.py  downloading missing cores from libretro
    romm.py            the RomM API client, with its safety lock (read + upload saves only)
    saves.py           save files, 3DS save folders, backups, deciding which save is newer
    games.py           a game from the library: ROM and save locations, search, covers
    launcher.py        the launcher: login, library, play (download, sync, run, upload)
    gui.py             the window (--gui)
    cli.py             the command line (main)
"""
