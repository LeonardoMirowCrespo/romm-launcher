"""The command line: argument parsing and the terminal flow (main).
"""

from __future__ import annotations

import argparse
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import paths
from . import system
from .config import load_config, migrate_flat_config, migrate_legacy_dirs, roms_root
from .gui import run_gui
from .launcher import Launcher
from .retroarch import retroarch_command
from .romm import RommError
from .system import APP_NAME, OS_NAMES
from .ui import Quit, bold, dim, fail, say


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="romm_launcher.py",
        description="Play games from your RomM library in RetroArch, with saves synced to RomM.",
    )
    parser.add_argument("game", nargs="*", help="part of a game's name, e.g. zelda")
    parser.add_argument("--list", action="store_true", help="show your library and exit")
    parser.add_argument("--upload", action="store_true", help="upload this computer's save of a game to RomM")
    parser.add_argument("--no-launch", action="store_true", help="download ROM and save, but don't start RetroArch")
    parser.add_argument("--screen", choices=("main", "both", "corner"),
                        help="for two-screen games (DS, 3DS), without asking: main screen only, both "
                             "screens, or main screen + small touch screen in the bottom-right corner")
    parser.add_argument("--gui", action="store_true", help="open the launcher in a window instead of the terminal")
    parser.add_argument("--setup", action="store_true", help="change RomM address, login and RetroArch paths")
    parser.add_argument("--paths", "--backups", action="store_true", dest="paths",
                        help="show where settings, ROMs and save backups are kept")
    args = parser.parse_args(argv)
    query = " ".join(args.game) or None

    migrate_legacy_dirs()
    migrate_flat_config()
    if args.gui:
        return run_gui()
    if args.paths:
        cfg = load_config()
        source = f"from {paths.HOME_ENV}" if os.environ.get(paths.HOME_ENV) else "the script's folder"
        print(f"  {'Launcher folder:':<17}{paths.APP_HOME}  ({source})")
        for label, path in (
            ("Settings", paths.CONFIG_FILE),
            ("ROMs", roms_root(cfg)),
            ("Save backups", paths.BACKUP_DIR),
            ("RetroArch", " ".join(retroarch_command(cfg["retroarch"]))),
            ("RetroArch saves", Path(cfg["saves_dir"]).expanduser()),
        ):
            print(f"  {label + ':':<17}{path}")
        if system.OS_KEY not in cfg:
            print(f"  RetroArch isn't set up on {OS_NAMES[system.OS_KEY]} yet; you'll be asked at the next start.")
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
            say(f"First start on {OS_NAMES[system.OS_KEY]} with these settings: where is RetroArch on this computer?")
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
            app.play(game, launch=not args.no_launch, screen=args.screen)
        print()
        return 0
    except Quit:
        say(dim("Bye."))
        return 0
    except RommError as exc:
        fail(str(exc))
        return 1
