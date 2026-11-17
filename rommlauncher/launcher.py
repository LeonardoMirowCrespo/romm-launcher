"""The launcher itself: login, the library, playing a game (download the ROM, sync the
save, start RetroArch or a PC port like Ship of Harkinian, upload the save), and setup.
Used by both the terminal (cli) and the window (gui); it talks to the player only
through `ui`.
"""

from __future__ import annotations

import getpass
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from . import paths
from . import system
from .config import config_for_disk, load_config, roms_root
from .core_downloads import CoreInstallError, core_info_dir, install_core, install_core_info
from .dualscreen import SCREEN_CHOICES, SCREEN_LAYOUTS, TOUCH_CONTROLS
from .games import Game, filter_games, guess_platform, index_roms, unpack_zip
from .ports import (
    PLAYERS,
    RETROARCH,
    SOH,
    SOH_SITE,
    SohInstallError,
    config_path,
    find_soh,
    install_soh,
    latest_soh_release,
    player_choices,
    soh_archive,
    soh_data_dir,
    soh_install_dir,
    soh_installed,
    soh_launch,
    soh_save,
)
from .retroarch import (
    DEFAULT_RETROARCH,
    FLATPAK_RETROARCH,
    STEAM_RETROARCH,
    core_display_name,
    core_id,
    core_options_file,
    find_core,
    flatpak_id,
    installed_cores,
    read_retroarch_cfg,
    retroarch_command,
    retroarch_home,
    retroarch_installed,
    set_core_options,
)
from .romm import LoginError, RommClient, RommError
from .saves import backup_bytes, decide, fetch_remote
from .system import HAVE_KEYRING, IS_WINDOWS, KEYRING_SERVICE, keyring
from .ui import (
    Quit,
    ask,
    ask_yes,
    bold,
    choose,
    dim,
    fail,
    green,
    human_size,
    ok,
    progress_bar,
    red,
    say,
    warn,
)
from .util import fmt_time, load_json, save_json

if TYPE_CHECKING:
    from .ports import SohSave
    from .saves import FileSave, FolderSave

    Save = FileSave | FolderSave | SohSave


class Launcher:
    def __init__(self):
        self.config = load_config()
        self.state = load_json(paths.STATE_FILE, {})
        self.client: RommClient | None = None
        self.offline = False

    # ----- setup and login --------------------------------------------

    def save_config(self) -> None:
        save_json(paths.CONFIG_FILE, config_for_disk(self.config))

    def needs_setup(self) -> bool:
        return not paths.CONFIG_FILE.exists() or not self.config.get("username") or not self.config.get("romm_url")

    def needs_retroarch_setup(self) -> bool:
        """True when config.json came from another system and has no paths for this one."""
        return system.OS_KEY not in self.config

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
            here = cfg.setdefault(system.OS_KEY, {})
            if Path(answer).expanduser().is_absolute():
                here["roms_dir"] = answer  # a full path only makes sense on this system
            else:
                here.pop("roms_dir", None)
                cfg["roms_dir"] = answer
        self.setup_retroarch()
        print()
        self.setup_soh()

    def setup_retroarch(self) -> None:
        """Ask for RetroArch's paths on this system and save them in its section."""
        cfg = self.config
        cfg.setdefault(system.OS_KEY, {})
        retroarch = self.find_retroarch()
        hint = "" if IS_WINDOWS else " (\"flatpak\" for RetroArch from Flathub)"
        answer = ask(f"RetroArch program{hint}", retroarch)
        cfg["retroarch"] = FLATPAK_RETROARCH if answer.strip().lower() == "flatpak" else answer
        ra_dir = retroarch_home(cfg["retroarch"])
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
        ok(f"Settings saved to {paths.CONFIG_FILE}")
        self.check_install()

    def auto_setup_retroarch(self) -> bool:
        """Set RetroArch's paths for this system without asking (the GUI's first start):
        the RetroArch found by find_retroarch, folders from its retroarch.cfg.
        Returns whether RetroArch was found."""
        cfg = self.config
        cfg.setdefault(system.OS_KEY, {})
        cfg["retroarch"] = self.find_retroarch()
        ra = read_retroarch_cfg(cfg["retroarch"])
        home = retroarch_home(cfg["retroarch"])
        cores = ra.get("libretro_directory")
        cfg["cores_dir"] = cores if cores and cores != "default" else str(home / "cores")
        saves = ra.get("savefile_directory")
        cfg["saves_dir"] = saves if saves and saves != "default" else str(home / "saves")
        if ra.get("sort_savefiles_enable"):
            cfg["sort_saves_by_core"] = ra["sort_savefiles_enable"] == "true"
        self.save_config()
        return retroarch_installed(cfg["retroarch"])

    def find_retroarch(self) -> str:
        """The configured RetroArch if it's there, else the first one found: the Windows
        build, Flatpak (Linux), Steam, or `retroarch` on the PATH."""
        current = self.config["retroarch"]
        if retroarch_installed(current):
            return current
        candidates = [DEFAULT_RETROARCH]
        if not IS_WINDOWS:
            candidates += [FLATPAK_RETROARCH, str(STEAM_RETROARCH / "retroarch"),
                           str(Path.home() / ".local/share/Steam/steamapps/common/RetroArch/retroarch")]
        candidates.append(shutil.which("retroarch") or "")
        for candidate in dict.fromkeys(c for c in candidates if c):
            if retroarch_installed(candidate):
                return candidate
        return current

    def setup_soh(self) -> bool:
        """Set up Ship of Harkinian (optional: q / Enter skips): download it, or ask where it
        is. Returns whether it's set."""
        cfg = self.config
        say(dim(f"Ship of Harkinian plays Ocarina of Time (N64/GameCube) as a PC game ({SOH_SITE})."))
        found = find_soh(cfg)
        if not found:
            release = self.soh_release()
            if release:
                size = f", {human_size(release['size'])}" if release.get("size") else ""
                answer = choose(
                    "Ship of Harkinian isn't on this computer yet.",
                    [("1", f"Download it for me: version {release['version']}{size}, from its GitHub page"),
                     ("2", "I have it already: tell the launcher where")],
                    "Choice (q to skip)", default="1", cancel=True,
                    note=f"It's installed in {soh_install_dir()}, with its settings and saves.",
                )
                if answer is None:
                    return False
                if answer == "1":
                    return self.download_soh(release)
        say(dim("(Windows: unzip it to a folder of its own, e.g. C:\\Games\\SoH; Linux: the .appimage)."))
        program = "soh.exe" if IS_WINDOWS else ".appimage"
        while True:
            answer = ask(f"Ship of Harkinian program ({program}; Enter to skip)", found).strip().strip('"')
            if not answer:
                return False
            path = Path(answer).expanduser()
            if not path.is_absolute():
                path = paths.APP_HOME / path
            if path.is_dir() and (path / "soh.exe").is_file():
                path = path / "soh.exe"
            if path.is_file():
                break
            warn(f"Not found: {path}")
        self.use_soh(path)
        return True

    def use_soh(self, program: Path) -> None:
        cfg = self.config
        cfg.setdefault(system.OS_KEY, {})
        cfg["soh"] = config_path(program)
        self.save_config()
        ok(f"Ship of Harkinian: {program}  (its saves: {soh_data_dir(cfg) / 'Save'})")

    def soh_release(self) -> dict | None:
        """SoH's latest release for this computer from GitHub, or None (with a note why)."""
        say(dim("Asking GitHub for Ship of Harkinian's latest version…"))
        try:
            return latest_soh_release()
        except SohInstallError as exc:
            warn(str(exc))
            return None

    def download_soh(self, release: dict | None = None) -> bool:
        """Download and install Ship of Harkinian, and use it from now on."""
        release = release or self.soh_release()
        if not release:
            return False
        try:
            program = install_soh(release, label=f"Downloading Ship of Harkinian {release['version']}")
        except (SohInstallError, OSError) as exc:
            fail(f"Couldn't install Ship of Harkinian: {exc}")
            return False
        checked = "checked against GitHub's checksum" if release.get("sha256") else "GitHub lists no checksum for it"
        ok(f"Ship of Harkinian {release['version']} installed in {program.parent} ({checked}).")
        self.use_soh(program)
        return True

    def check_install(self) -> None:
        """Point out missing RetroArch pieces right away instead of at play time."""
        cfg = self.config
        if cfg.get("soh") and not soh_installed(cfg):
            warn(f"Ship of Harkinian not found at {cfg['soh']}")
        if not retroarch_installed(cfg["retroarch"]):
            if flatpak_id(cfg["retroarch"]):
                warn(f"The Flatpak {flatpak_id(cfg['retroarch'])} isn't installed "
                     f"(flatpak install flathub {flatpak_id(cfg['retroarch'])}).")
            else:
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
                if paths.LIBRARY_CACHE.exists() and ask_yes(
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
            save_json(paths.LIBRARY_CACHE, {"roms": roms, "fetched_at": datetime.now().isoformat()})
        else:
            roms = load_json(paths.LIBRARY_CACHE, {"roms": []})["roms"]
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
        progress = progress_bar("Downloading", size)
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
        if core and self.config.get("auto_install_cores", True):
            found = self.download_core(game, core, cores_dir)
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
        answer = choose(f"Which installed core should {game.platform_name} use?",
                        [(str(i), core_id(name)) for i, name in enumerate(installed, 1)],
                        "Core number (q to cancel)", cancel=True)
        if answer is None:
            return None
        chosen = installed[int(answer) - 1]
        self.config["platforms"].setdefault(game.platform, {})["core"] = core_id(chosen)
        self.save_config()
        ok(f"{game.platform_name} will use {core_id(chosen)} from now on.")
        return cores_dir / chosen

    def download_core(self, game: Game, core: str, cores_dir: Path) -> Path | None:
        """Install a missing core from libretro. None (with a warning) if that fails."""
        ra = read_retroarch_cfg(self.config["retroarch"])
        say(f"The {game.platform_name} core ({core_id(core)}) isn't installed. Downloading it from libretro…")
        try:
            path = install_core(core, cores_dir, ra, label="Downloading core")
        except (CoreInstallError, OSError) as exc:
            warn(f"Couldn't download the core automatically: {exc}")
            return None
        ok(f"Core installed: {path}")
        # The Flatpak ships every core's .info file, inside the app (never write there).
        if not flatpak_id(self.config["retroarch"]):
            try:
                install_core_info(core, core_info_dir(ra, cores_dir), ra)
            except (CoreInstallError, OSError) as exc:
                warn(f"Couldn't get the core's .info file ({exc}); RetroArch will still run it.")
        if "steamapps" in str(self.config["retroarch"]).lower():
            say(dim("Tip: with Steam's RetroArch, also install this core as a Steam add-on;"))
            say(dim("Steam may remove cores it didn't install when it checks its files."))
        return path

    @staticmethod
    def hash_key(save: "Save | None") -> str:
        """Where state.json keeps the last synced hash: "hash" for RetroArch's saves,
        "hash_<emulator>" for a PC port's (they're separate saves in RomM)."""
        return f"hash_{save.emulator}" if save is not None and save.emulator else "hash"

    def mark_synced(self, game: Game, save_hash: str | None, pending: bool = False, save: "Save | None" = None) -> None:
        entry = self.state.setdefault(str(game.id), {})
        entry.update({self.hash_key(save): save_hash, "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M")})
        entry["pending_upload"] = pending
        save_json(paths.STATE_FILE, self.state)

    def sync_before_play(self, game: Game, save: "Save") -> str | None:
        """Bring RomM's newest save to this computer if it's newer.

        Returns the hash of the save RomM has (None if it has none), or raises Quit."""
        say("Checking RomM for your latest save…")
        remote = fetch_remote(self.client, game, save)
        local_hash = save.hash()
        last = self.state.get(str(game.id), {}).get(self.hash_key(save))
        action = decide(local_hash, remote.hash, last)

        if action == "conflict":
            choice = choose(
                "This save changed both here and in RomM since the last sync:",
                [("r", f"RomM's save           {fmt_time(remote.updated)}"),
                 ("l", f"this computer's save  {fmt_time(save.modified())}")],
                "Which one do you want to play with? (r/l, q to cancel)",
                cancel=True, kind="warn", note="Both are backed up either way.",
            )
            if choice is None:
                raise Quit
            backup_bytes(save.read(), game.id, "local-before-conflict", save.suffix)
            backup_bytes(remote.data or b"", game.id, "romm-before-conflict", save.suffix)
            action = "download" if choice == "r" else "keep"

        if action == "download":
            if local_hash is not None:
                backup_bytes(save.read(), game.id, "replaced-by-romm", save.suffix)
            save.write(remote.data or b"")
            self.mark_synced(game, remote.hash, save=save)
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
            self.mark_synced(game, remote.hash, save=save)
            ok("Save is already up to date.")
        return remote.hash

    def apply_screen_layout(self, game: Game, core: Path, rom_file: Path, screen: str | None = None) -> None:
        """For two-screen games: show the main screen only, both, or the main screen with a
        small touch screen in the corner (where the core can). Asks (default: the choice
        from last time, else both) unless `screen` is given, then sets the core's options
        in RetroArch's options file before RetroArch starts."""
        layouts = SCREEN_LAYOUTS[core_id(core.name)]
        choices = [c for c in SCREEN_CHOICES if c in layouts]
        entry = self.state.setdefault(str(game.id), {})
        last = entry.get("screen_layout") if entry.get("screen_layout") in choices else "both"
        if screen not in choices:
            if screen:
                warn(f"\"{screen}\" isn't available for this core.")
            answer = choose(
                "This game has two screens. Which layout?",
                [(str(n), SCREEN_CHOICES[c] + ("  (last time)" if c == last else ""))
                 for n, c in enumerate(choices, 1)],
                "Layout", default=str(choices.index(last) + 1),
            )
            screen = choices[int(answer) - 1]
        ra = read_retroarch_cfg(self.config["retroarch"])
        name = core_display_name(self.config["cores_dir"], core.name, ra.get("libretro_info_path"))
        if not name:
            warn("Couldn't tell where this core keeps its options; the layout is unchanged.")
            return
        path = core_options_file(self.config["retroarch"], ra, name, rom_file)
        values = dict(layouts[screen])
        touch = TOUCH_CONTROLS.get(core_id(core.name)) if screen != "main" else None
        if touch:
            key, shown, hidden = touch["pointer"]
            values[key] = shown if self.config.get("show_touch_pointer", True) else hidden
        try:
            set_core_options(path, values)
        except OSError as exc:
            warn(f"Couldn't set the screen layout in {path} ({exc}); RetroArch keeps its own setting.")
            return
        entry["screen_layout"] = screen
        save_json(paths.STATE_FILE, self.state)
        say(dim(f"{SCREEN_CHOICES[screen]}. To change it while playing: F1 → Core Options."))
        if touch:
            say(dim(touch["help"]))

    # ----- what plays a game: RetroArch, or a PC port (Ship of Harkinian) ------

    def default_player(self, game: Game) -> str:
        """What plays this game unless the player picks: the choice from last time, else
        Ship of Harkinian when it's set up, else RetroArch."""
        choices = player_choices(game)
        last = self.state.get(str(game.id), {}).get("player")
        if last in choices:
            return last
        return SOH if choices and soh_installed(self.config) else RETROARCH

    def choose_player(self, game: Game, player: str | None = None, launch: bool = True) -> str:
        """For games a PC port can play: RetroArch or the port? Asks (default: last time's)
        unless `player` is given; without launching, last time's is used."""
        choices = player_choices(game)
        if not choices:
            if player and player != RETROARCH:
                warn(f"{PLAYERS.get(player, player)} can't play {game.name}; using RetroArch.")
            return RETROARCH
        last = self.default_player(game)
        played = self.state.get(str(game.id), {}).get("player") == last
        if player not in choices:
            if player:
                warn(f"\"{player}\" can't play this game.")
            if not launch:
                return last
            labels = {
                SOH: "Ship of Harkinian: the PC version, with widescreen, high resolution and 60 fps"
                     + ("" if soh_installed(self.config) else "  (not set up yet)"),
                RETROARCH: f"RetroArch ({game.core or 'its N64 core'}): the original game",
            }
            answer = choose(
                f"{game.name} can be played two ways. Which one?",
                [(str(n), labels[c] + ("  (last time)" if c == last and played else "")) for n, c in enumerate(choices, 1)],
                "Play with", default=str(choices.index(last) + 1),
                note="The two keep separate saves (they can't read each other's).",
            )
            player = choices[int(answer) - 1]
        entry = self.state.setdefault(str(game.id), {})
        entry["player"] = player
        save_json(paths.STATE_FILE, self.state)
        return player

    def ensure_soh(self) -> bool:
        if soh_installed(self.config):
            return True
        if self.config.get("soh"):
            warn(f"Ship of Harkinian isn't at {self.config['soh']} any more.")
        else:
            say("Ship of Harkinian isn't set up on this computer yet.")
        return self.setup_soh()

    def save_for(self, game: Game, rom_file: Path, player: str | None = None) -> "Save | None":
        """This game's save on this computer for what plays it (default: last time's)."""
        if (player or self.default_player(game)) == SOH:
            return soh_save(self.config, rom_file)
        return game.save(rom_file)

    def play(self, game: Game, launch: bool = True, screen: str | None = None, player: str | None = None) -> None:
        print()
        say(bold(f"▶ {game.name}") + dim(f"  ({game.platform_name})"))
        rom_file = self.ensure_rom(game)
        if not rom_file:
            return
        player = self.choose_player(game, player, launch)
        core = None
        if player == SOH:
            if launch and not self.ensure_soh():
                return
        elif launch:
            core = self.ensure_core(game)
            if not core:
                return
        save = self.save_for(game, rom_file, player)
        remote_hash: str | None = None
        synced = False
        if not save and player == SOH:
            warn("Ship of Harkinian isn't set up on this computer, so its save isn't synced.")
        elif not save:
            warn(f"This game's save can't be synced with RomM (unknown save location for {game.platform_name}).")
        elif self.client:
            remote_hash = self.sync_before_play(game, save)
            synced = True
        else:
            warn("Offline: playing with the save on this computer. It uploads next time you're online.")

        if not launch:
            return
        env = None
        if player == SOH:
            name = PLAYERS[SOH]
            try:
                command, cwd, env = soh_launch(self.config, game, rom_file)
            except OSError as exc:
                fail(f"Could not prepare Ship of Harkinian's folder: {exc}")
                return
            if len(command) > 1:
                say(f"Ship of Harkinian first turns the ROM into {soh_archive(game)} (only this once; it takes a minute),")
                say("then asks \"Run SoH?\": answer Yes.")
            done_hint = "save in the game (pause menu), then close its window"
        else:
            retroarch = self.config["retroarch"]
            if not retroarch_installed(retroarch):
                if flatpak_id(retroarch):
                    fail(f"The RetroArch Flatpak isn't installed: flatpak install flathub {flatpak_id(retroarch)}")
                else:
                    fail(f"RetroArch not found at {retroarch}. Fix it with --setup.")
                return
            if core_id(core.name) in SCREEN_LAYOUTS:
                self.apply_screen_layout(game, core, rom_file, screen)
            name = "RetroArch"
            command = retroarch_command(retroarch) + ["-L", str(core), str(rom_file)]
            cwd = None if flatpak_id(retroarch) else Path(retroarch).expanduser().parent
            done_hint = f"{bold('F1 → Close Content')}, then quit RetroArch"
        entry = self.state.setdefault(str(game.id), {})
        entry["last_played"] = datetime.now().isoformat(timespec="seconds")
        save_json(paths.STATE_FILE, self.state)

        hash_before = save.hash() if save else None
        print()
        say(f"Starting {name}. When you're done: {done_hint}.")
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(cwd) if cwd else None,
                env=env,
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
            fail(f"Could not start {name}: {exc}")
            return
        self.wait_for(proc, name)

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

    def wait_for(self, proc: subprocess.Popen, name: str = "RetroArch") -> None:
        say(dim(f"Waiting for {name} to close…"))
        while True:
            try:
                proc.wait()
                return
            except KeyboardInterrupt:
                print()
                warn(f"{name} is still running. The save is uploaded when it closes.")
                if ask_yes("Stop waiting? (then upload later with --upload)", default=False):
                    raise Quit
                say(dim("Still waiting…"))

    def upload(self, game: Game, save: "Save") -> bool:
        local_hash = save.hash()
        data = save.read()
        if not self.client:
            self.mark_pending(game, save)
            warn("Offline: the save stays on this computer and uploads next time.")
            return False
        say(f"Uploading your save to RomM ({human_size(len(data))})…")
        try:
            self.backup_romm_copy(game, save, local_hash)
            self.client.upload_save(game.id, save.upload_name, data, save.emulator or core_id(game.core or "unknown"))
        except RommError as exc:
            self.mark_pending(game, save)
            print()
            fail(red(bold("YOUR SAVE WAS NOT UPLOADED TO RomM.")))
            fail(str(exc))
            say(f"It is safe on this computer: {save.location}")
            say("The launcher will try again next time it starts.")
            return False
        self.mark_synced(game, local_hash, save=save)
        ok("Save uploaded to RomM.")
        return True

    def backup_romm_copy(self, game: Game, save: "Save", new_hash: str | None) -> None:
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

    def mark_pending(self, game: Game, save: "Save | None" = None) -> None:
        entry = self.state.setdefault(str(game.id), {})
        entry["pending_upload"] = True
        entry["pending_name"] = game.name
        entry["pending_player"] = SOH if save is not None and save.emulator == SOH else RETROARCH
        save_json(paths.STATE_FILE, self.state)

    def retry_pending(self, games: list[Game]) -> None:
        pending = [g for g in games if self.state.get(str(g.id), {}).get("pending_upload")]
        for game in pending:
            rom_file = game.find_local_rom()
            player = self.state[str(game.id)].get("pending_player")
            save = self.save_for(game, rom_file, player) if rom_file else None
            if not save or save.hash() is None:
                continue
            warn(f"The last save of {game.name} never reached RomM. Uploading it now.")
            self.upload(game, save)

    def upload_now(self, game: Game) -> None:
        rom_file = game.find_local_rom()
        if not rom_file:
            fail(f"{game.name} isn't on this computer, so there's no save to upload.")
            return
        save = self.save_for(game, rom_file)
        if not save:
            fail(f"Saves for {game.name} can't be synced with RomM.")
            return
        if save.hash() is None:
            fail(f"No save found at {save.location}")
            return
        say(f"{save.location}  ({fmt_time(save.modified())}, {human_size(len(save.read()))})")
        if ask_yes(f"Upload this as the newest save of {game.name}?"):
            self.upload(game, save)

    # ----- adding games to RomM -------------------------------------------

    def add_rom(self, path: Path, platform: str | None = None, name: str | None = None) -> bool:
        """Upload a game file to RomM as a new ROM, then have RomM scan for it.

        Without `platform` the player picks one of the platforms RomM supports, theirs first
        (the file's extension suggests one); this is also their confirmation. A platform that
        isn't on their RomM yet is created for it. Nothing on RomM is replaced: a file of the
        same name is refused. The file is also put in the ROM folder, so the game
        doesn't have to be downloaded again to play it."""
        if not self.client:
            fail("Adding games needs a connection to RomM.")
            return False
        name = name or path.name
        size = path.stat().st_size
        mine = {p["fs_slug"]: p for p in self.client.list_platforms() if p.get("fs_slug") and p.get("id", 0) > 0}
        known = {p.get("fs_slug") or p.get("slug"): p for p in self.client.list_supported_platforms()}
        known = {slug: p for slug, p in known.items() if slug and slug not in mine}

        def label(p: dict) -> str:
            return p.get("display_name") or p.get("custom_name") or p.get("name") or p.get("fs_slug") or p.get("slug")

        if platform:
            if platform not in mine and platform not in known:
                fail(f"RomM doesn't know a platform called {platform}.")
                return False
        else:
            # Your RomM's platforms first, then every other platform RomM supports.
            options = [(slug, label(p)) for slug, p in sorted(mine.items(), key=lambda kv: label(kv[1]).lower())]
            options += [(slug, f"{label(p)}  (new in your RomM)")
                        for slug, p in sorted(known.items(), key=lambda kv: label(kv[1]).lower())]
            if not options:
                fail("RomM didn't list any platforms to add the game to.")
                return False
            guess = guess_platform(name)
            platform = choose(f"Add {name} ({human_size(size)}) to RomM as a game for:", options, "Platform",
                              default=guess if guess in mine or guess in known else "", cancel=True,
                              note="It's uploaded as a new game; nothing on RomM is replaced. A platform "
                                   "that's new in your RomM is created for it.")
            if platform is None:
                return False
        chosen = mine.get(platform) or {"fs_slug": platform, "name": label(known[platform])}
        slug = chosen["fs_slug"]
        cached = load_json(paths.LIBRARY_CACHE, {"roms": []}).get("roms", [])
        if any(r.get("platform_fs_slug") == slug and (r.get("fs_name") or "").lower() == name.lower() for r in cached):
            warn(f"{name} is already in your RomM library.")
            return False
        if "id" not in chosen:
            chosen = self.client.add_platform(slug)
            ok(f"{label(chosen)} added to your RomM's platforms.")
        progress = progress_bar(f"Uploading {name}", size)
        try:
            self.client.upload_rom(chosen["id"], path, name, progress)
        except RommError as exc:
            if "already exists" in str(exc):
                warn(f"RomM already has a file called {name} for that platform; nothing was changed.")
                return False
            raise
        finally:
            progress.finish()
        ok(f"{name} uploaded to RomM.")
        dest = roms_root(self.config) / slug / name
        if path.resolve() != dest.resolve() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            shutil.copyfile(path, part)
            part.replace(dest)
        try:
            task = self.client.start_scan(chosen["id"])
            say(dim("RomM is adding it to your library…"))
            status = self.client.wait_for_task(task["task_id"]) if task.get("task_id") else "finished"
        except RommError as exc:
            warn(f"RomM didn't start a scan ({exc}). The game appears after RomM's next scan; you can "
                 "start one in RomM's web page.")
            return True
        if status == "finished":
            ok(f"{name} is in your library.")
        else:
            warn(f"RomM's scan ended as \"{status}\". If the game doesn't appear, scan in RomM's web page.")
        return True
