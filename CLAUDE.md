# RomM Launcher

A Python app for playing games from a self-hosted **RomM** library locally in RetroArch,
with RomM as the home of the saves, or (since 2026-11-18, at the user's request) without
RomM at all: games from the ROM folder, saves kept on the computer. It runs on Windows and Linux, in the terminal
(default) or as a window (`--gui`, Tkinter, a game-store style library; work on the `gui`
branch since 2026-11-16). `romm_launcher.py` is only the start script; the code is the
`rommlauncher/` package (split from one file on 2026-11-16, at the user's request, to
make it easier to read and work on).

It's published as a public GitHub repo; `README.md` is the user-facing guide (keep it in
step with behaviour changes). Notes about a particular setup (RomM address, PCs, paths)
go in `CLAUDE.local.md`, which is git-ignored. Nothing personal (server addresses,
usernames) belongs in the committed files.

Usage: `romm_launcher.py` (pick from library), `romm_launcher.py zelda` (straight to a match),
`--list`, `--upload <game>`, `--add <file>...` (add game files to RomM), `--no-launch`, `--screen main|both|corner`,
`--with soh|2ship|sm64coopdx|retroarch`, `--status finished|100|none <game>`, `--favorite`/`--unfavorite <game>`,
`--gui`, `--setup`, `--paths` (alias `--backups`).
First run asks for setup, starting with "Do you want to connect to a RomM server?".

**Without RomM** (config `use_romm: false`, `Launcher.uses_romm`; default true, so older
configs keep RomM). `needs_setup` then needs no address/login, `connect` does nothing,
`library` is `games.local_roms(roms_root)`: `<roms>/<platform>/<file>` (folder name =
RomM fs_slug; another folder name or a file straight in `roms/` → `guess_platform` by
extension; a subfolder = a multi-file game; files a `.cue`/`.m3u`/`.gdi`/`.ccd` names and
`NOT_GAMES` extensions are skipped). Those ROM records have `id` `"local-<sha1 of the
relative path>"` (so `Game.id` is `int | str`), `local_path` (`find_local_rom` uses it) and
`platform_display_name` from `PLATFORM_NAMES`; `library.json` isn't written. `play`
doesn't sync, upload or mark anything pending (says "Game saved on this computer");
`upload`, `retry_pending` and `upload_now` refuse; `add_rom` → `add_local_rom` (copies
into `<roms>/<platform>/` via `.part`, never replaces). Setup: `Launcher.setup` asks
first (`setup_romm` for the address/login), `use_romm(on)` switches. GUI: `start()`
shows `show_welcome` (Connect to RomM / Play without RomM) when there's no config yet,
goes straight to the library without RomM, else `show_login` (which has a "No RomM?"
link → `use_local`); without RomM the rail's ☁ (`connect_romm`) replaces Log out, a
successful login sets `use_romm` true, and there's no "Installed" chip
(`library_filters(show_local=False)`). Favorites and Finished / 100% are kept in state
(`favorite`, `status`) instead of RomM.

Flow when a game is picked:
1. Make sure the ROM is in `<ROM folder>/<platform fs_slug>/<fs_name>` (also accepts the
   same file name anywhere under the ROM folder); download it from RomM if not. The ROM
   folder is a cache the launcher manages; RomM holds the real copies.
2. Fetch the newest save for the game from RomM, decide whether it's newer than the local one,
   and put it into RetroArch's saves folder (backing up whatever it replaces).
3. Launch RetroArch with the platform's core and the ROM (or Ship of Harkinian, see
   below), and wait for the process to exit.
4. If the local save changed, upload it to RomM. A failed upload is marked `pending_upload`
   in state and retried at the next start.

**Play time, Finished / 100%, favorites** (2026-11-19, at the user's request; they wanted
only total play time, not per machine, and of RomM's statuses only Finished and 100%).
`play` times the game with `time.monotonic()` from start to exit (`finally`, so also when
the player stops waiting) and adds it to `play_seconds` in state (`add_play_time`,
`play_time`; `fmt_duration` prints it). RomM has no play-time field, so it stays local.
Status and favorites are RomM's own: `Launcher.status` reads `rom_user.status` from the
library item (only `STATUS_NAMES` count; RomM's other statuses show as none), favorites
are RomM's collection marked `is_favorite` owned by the user (`RommClient.favorites`, read
with the library into `Launcher.favorites` and cached in `library.json` as `favorites`).
`set_status`/`set_favorite` need the connection (`can_mark`; offline they fail) and update
`library.json`. GUI: a ★ rail icon opens the Favorites page (`GuiApp.view`, `show_view`,
`view_games`; chips and search work on it), the details panel has "Your progress"
(`_marks`: ☆ Favorite, Finished, 100%, click the chosen one again to clear;
`mark_chips` for tests) and a "Played" row; tiles get a ★ tag and "✓ Finished". Terminal:
the picker lists Favorites after Continue playing and `game_marks` per row.

## Constraints

- **Standard library only.** No pip dependencies. `keyring` is optional
  (`python3-keyring` / `pip install keyring`) and the app must keep working without it.
- **Never store the RomM password in plain text.** It goes in the system keyring if
  available, otherwise it's asked with `getpass` each run and kept in memory only.
- **Never lose a save.** Anything that overwrites a save backs it up first. A failed upload
  must be loud and leave the local save untouched.
- **Never delete or change anything on RomM.** The launcher only reads, uploads saves,
  (since 2026-11-16, at the user's request) adds new ROM files, and (since 2026-11-19, at
  the user's request) sets a game's Finished / 100% status and adds or removes a game in
  the user's Favorites collection.
  `RommClient._check` enforces it on every request: GET only to the RomM server, writes only
  as listed in `RommClient.ALLOWED_WRITES` (method, path pattern, query parameters, body
  check): POST `/api/saves` with `rom_id`/`emulator`, POST `/api/token`, the chunked ROM
  upload (POST `/api/roms/upload/start` with a body of only `filename`, so never `rom_id`,
  `folder` or `overwrite`; PUT `/api/roms/upload/<uuid>`; POST `.../<uuid>/complete|cancel`
  without a body), POST `/api/tasks/scan` only as `{"type": "quick", "platforms": [ids]}`,
  and POST `/api/platforms` only as `{"fs_slug": "<plain slug>"}` (creates a platform the
  user picked that isn't on their RomM yet; RomM makes the folder and entry).
  Since 2026-11-19, at the user's request (favorites and Finished / 100% synced with
  RomM's own): PUT `/api/roms/<id>/props` only as `{"status": "finished"|"completed_100"|null}`
  (RomM applies only the fields sent, `exclude_unset`); POST `/api/collections?is_favorite=true`
  (+ optional `is_public=false`) with a multipart body of only `name=Favorites` (what RomM's
  web UI makes when there's none); POST and DELETE `/api/collections/<id>/roms` with
  `{"rom_ids": [one int]}`, and only for the id `favorites()` found (`client.favorites_id`;
  paths with `(?P<fav>…)` check it). That DELETE (out of the collection) is the only one;
  never PATCH; redirects to other servers are refused. Don't widen that list without
  a very good reason. An upload replaces
  RomM's save of the same name, so `Launcher.backup_romm_copy` downloads and backs up
  that save first (`romm-before-upload`); if that fails, nothing is uploaded.
- The launcher folder (`romm_launcher.py` + `rommlauncher/`, plus the data) is what gets
  copied between machines (Windows and Linux), so keep everything inside it.

## Code layout (`rommlauncher/`)

One module per topic; `rommlauncher/__init__.py` has the same map. Dependencies only go
downwards in this list (no import cycles; type-only imports use `TYPE_CHECKING`):

| Module | Contents |
| --- | --- |
| `system.py` | `APP_NAME`, `IS_WINDOWS`, `OS_KEY`/`OS_KEYS`/`OS_NAMES`, `CORE_EXT(S)`, optional `keyring` (`HAVE_KEYRING`) |
| `paths.py` | `APP_HOME` and the data files (`CONFIG_FILE`, `STATE_FILE`, `LIBRARY_CACHE`, `BACKUP_DIR`, `COVER_DIR`), `OLD_LOCATIONS` |
| `ui.py` | `TerminalUI`, `set_ui`, `say/ok/warn/fail`, `ask`, `ask_yes`, `choose`, `progress_bar`, `Progress`, `Quit`, colours, `human_size` |
| `util.py` | `load_json`, `save_json`, `sha256`, `file_hash`, `parse_time`, `fmt_time`, `fmt_duration`, `write_new_file` |
| `retroarch.py` | Flatpak/Steam/default RetroArch, `read_retroarch_cfg`, `core_id`, `find_core`, `installed_cores`, `core_display_name`, `core_save_folder`, `KNOWN_CORE_FOLDERS`, core options (`retroarch_config_dir`, `core_options_file`, `set_core_options`) |
| `config.py` | `DEFAULT_PLATFORMS`, `MACHINE_KEYS`, `default_config`, `load_config`, `config_for_disk`, `guess_os`, `roms_root`, `migrate_flat_config`, `migrate_legacy_dirs` |
| `dualscreen.py` | `SCREEN_CHOICES`, `SCREEN_LAYOUTS`, `TOUCH_CONTROLS`, `screen_choices(game)` |
| `core_downloads.py` | buildbot: `install_core`, `install_core_info`, `core_download_url`, `http_download`, `CoreInstallError` |
| `romm.py` | `RommClient` (+ safety lock `_check`, `ALLOWED_WRITES`; `upload_rom`, `start_scan`, `wait_for_task`, `list_platforms`, `set_status`, `favorites`, `set_favorite`), `STATUSES`, `RommError`, `LoginError` |
| `saves.py` | `FileSave`, `FolderSave`, 3DS title ID helpers, `backup_bytes`/`backup_file`, `RemoteSave`, `fetch_remote`, `decide` |
| `ports.py` | PC ports: `Port` (`plays`, `archive_for`, `program`, `installed`, `data_dir`, `saves_dir`, `launch`, `cleanup`, `save`, `find`; download: `install_dir`, `latest_release`, `install`), `SHIP_OF_HARKINIAN`, `TWO_SHIP`, `COOP_DX`, `PORTS`, `port_for`, `player_choices`, `PortSave`, `unpacked_rom`, `place_rom`/`big_endian_n64`/`RomMismatch`, `PortInstallError`, `GITHUB_API`/`GITHUB`, `SOH`/`TWOSHIP`/`SM64COOPDX`/`RETROARCH`/`PLAYERS` |
| `games.py` | `Game`, `STATUS_NAMES`, `index_roms`, `unpack_zip`, `filter_games`, without RomM `local_roms`/`game_entries`/`PLATFORM_NAMES`/`platform_name`, GUI library helpers (`library_filters`, `filter_library`, `local_game_ids`), `fetch_cover`, `ROM_PLATFORMS`/`guess_platform`, `game_facts` |
| `launcher.py` | `Launcher` (`uses_romm`/`use_romm`, setup, `setup_romm`, login, library, picker, `play`, `upload`, `retry_pending`, `add_rom`, `add_local_rom`, `play_time`, `status`/`set_status`, `is_favorite`/`set_favorite`, ...) |
| `gui.py` | `GuiUI`, `GuiProgress`, `GuiApp`, `run_gui` |
| `cli.py` | `main()` (argparse; `--gui` goes to `run_gui`) |

**Names that tests replace at runtime** (`paths.*`, `system.OS_KEY`, `retroarch.FLATPAK_APPS`,
`core_downloads.BUILDBOT`, `ui.input`) must be read as `paths.CONFIG_FILE`,
`system.OS_KEY`, ... from other modules, never `from .paths import CONFIG_FILE`, or the
replacement isn't seen.

Everything up to `games.py` has **no user interaction**, so it can be imported and tested.

- Talking to the player: `say/ok/warn/fail`, `ask`, `ask_yes`, `choose` (pick one of
  (key, label) options), `progress_bar`. They all go to the `UI` object: `TerminalUI` by
  default (prints, `input()`, `Progress`), or the GUI's `GuiUI` (`set_ui`). So Launcher
  code never prints questions or reads input itself; new questions use `choose`/`ask`.
  `Quit` is raised on q / Ctrl+C / Ctrl+D at a prompt (or Cancel in a GUI dialog).
- Folders: `paths.APP_HOME` (see "Files on disk"; the folder above the package);
  `migrate_legacy_dirs()` is called from `main()` and leaves things in place if a file is
  in use.
- Config and state: `load_config` (merges `DEFAULT_PLATFORMS`, drops
  `OLD_PLATFORM_DEFAULTS`, applies this system's section), `config_for_disk` (what
  `Launcher.save_config` writes), `migrate_flat_config`, `load_json`, `save_json`, `backup_bytes`/`backup_file`,
  `core_save_folder` (save folder name from the core's `.info`, else `KNOWN_CORE_FOLDERS`).
- RetroArch: `read_retroarch_cfg` (`retroarch.cfg` next to the exe, `%APPDATA%\RetroArch`,
  or `~/.config/retroarch`; values starting with `:` are relative to RetroArch's folder),
  `find_core`, `installed_cores`, `core_id`. Core names in config are OS-neutral
  (`mupen64plus_next`), files are `<id>_libretro.dll/.so/.dylib`.
- **Linux uses the Flatpak RetroArch by default** (`DEFAULT_RETROARCH`; Windows: the
  standalone build). Config `retroarch` is then `"flatpak:org.libretro.RetroArch"`
  (`FLATPAK_RETROARCH`) instead of a program path. Never treat `retroarch` as a path
  directly; use `retroarch_home` (folder with retroarch.cfg, cores, saves; Flatpak:
  `~/.var/app/<id>/config/retroarch`), `retroarch_installed` (Flatpak: `flatpak info
  --show-location` succeeds), `retroarch_command` (`flatpak run <id>`) and `flatpak_id`.
  The Flatpak has `--filesystem=host`, so ROMs and cores are passed with their normal
  paths. `read_retroarch_cfg` turns `/app/...` values (e.g. `libretro_info_path`, inside
  the sandbox) into `<flatpak location>/files/...`; the Flatpak ships all core `.info`
  files, so `download_core` doesn't add one. Steam and distro builds still work as paths.
- `RommClient`: RomM REST API over `urllib`, HTTP Basic auth; on a 403 mentioning CSRF it
  switches to an OAuth token from `POST /api/token`.
- `Game` (dataclass wrapping RomM's ROM dict + config): `platform` (= `platform_fs_slug`),
  `core`, `save_exts`, `syncs_saves`, `rom_dest`, `find_local_rom()`, `save_dir(rom_file)`,
  `save_path(rom_file)`, `save(rom_file)`.
- `fetch_remote`, `RemoteSave`, `filter_games`
- `decide(local_hash, remote_hash, last_synced)` → `"none" | "download" | "keep" | "conflict"`.
  Whichever side still matches the last-synced hash is the stale one; if neither does, ask.

`Launcher` holds the interactive parts (setup, login, picker, `play`, `upload`,
`retry_pending`); `main()` is the argparse entry point. RetroArch runs via `subprocess.Popen`
in its own session / process group (`CREATE_NEW_PROCESS_GROUP` on Windows) so Ctrl+C in
the terminal doesn't kill the game.

**The GUI** (`gui.py`: `run_gui`, `--gui`). Tkinter is imported at the top of `gui.py`
in a try (`HAVE_TK`), so the CLI works without it. `GuiApp` builds a
login screen (`show_login`: username, password, keyring checkbox, "Play offline" if
`library.json` exists; the RomM address is a field only until one is saved, after that a
line at the bottom, `show_address`, that turns into a field on click, `edit_address`) and the library screen (`show_library`, the "Shelf"
design chosen 2026-11-16: an icon rail on the left (library, refresh, settings, log out,
avatar with tooltip), "Library" title + rounded search, filter chips from `library_filters`
(`PLATFORM_SHORT` names, wrapped by `_flow_chips`), a cover grid on a Canvas filtered
by `filter_library`: each cover sits whole and unscaled in the middle of a 204 px box (RomM's
small covers are 204 px wide, up to 204 tall), the rest of the box see-through and outlined
on hover/selection, a platform tag over its corner, a details panel with Play / Download only /
Upload save, `screen_choices` as chips and an info list, and a status bar whose "Activity"
toggle shows the log, which opens by itself on a failure). Rounded buttons and chips are
Canvas drawings from `GuiApp.pill` (`.text`, `.invoke()`, `.set_enabled()`); `fit_image`
scales covers down by a/b fractions to fit whole (Tk has no free scaling without Pillow).
"+ Add game" in the header (only when online) picks files (`add_files` → `Launcher.add_rom`).
A "Discover" page of free homebrew was built and then removed at the user's request
(2026-11-16): the user doesn't want a game store / download source in the launcher. The details panel is a scrolling canvas (`details_canvas`;
`_wheel_area` routes the mouse wheel) and shows `game_facts` + RomM's `summary`. Games
that RomM only identified by hash (no `igdb_id`, empty `metadatum`, e.g. Ocarina of Time
3D) have no facts: fix that with RomM's Match, not in the launcher. It drives
the same `Launcher` (`play(game, launch, screen)`, `upload_now`, `library`,
`retry_pending`, `auto_setup_retroarch` on first start) on worker threads (`run_task`).
Tk is only touched on the main thread: `GuiUI` hands calls to `GuiApp.calls` (a queue
emptied by `_pump` every 50 ms) and waits for answers to questions. Covers come from
RomM's `path_cover_small` via `fetch_cover` (`RommClient.download_asset`, a GET, so the
safety lock allows it) into `covers/<rom id>.png`; only PNG/GIF are kept (Tk can't show
JPEG without Pillow), others get a coloured placeholder. Pixel sizes go through
`GuiApp.S()` for high-DPI screens. The tests drive a real (hidden) window when a display
is available.

Save paths follow RetroArch's own settings, read from `retroarch.cfg` at runtime:
`sort_savefiles_enable`, `sort_savefiles_by_content_enable`, `savefiles_in_content_dir`
(`savefile_directory` pre-fills setup).

Platforms → cores (`DEFAULT_PLATFORMS`, overridable in config `platforms`):
n64 → `mupen64plus_next`, gba → `mgba`, nds and nintendo-dsi → `melondsds`, 3ds and
new-nintendo-3ds → `citra`; since 2026-11-16 also NES/Famicom (fceumm), SNES (snes9x),
GB/GBC (gambatte), Virtual Boy, Genesis/SMS/Game Gear/SG-1000 (genesis_plus_gx), 32X
(picodrive), TG16, WonderSwan, Neo Geo Pocket, PSX (pcsx_rearmed, memory card 1 as .srm)
with `.srm` save sync, and core-only (`save_exts: []`, saves not synced because the core
keeps them its own way) for Sega CD, TG-CD, Pokémon mini, Atari 2600/7800, Lynx, PSP, DC,
GameCube, Wii, Saturn, arcade/Neo Geo (fbneo). Core names checked against libretro's
core-info repo and the buildbot (Windows and Linux); each core's save-folder name is in
`KNOWN_CORE_FOLDERS` (its `.info` corename).

**PC ports** (`ports.py`). Ship of Harkinian (SoH, Ocarina of Time; added 2026-11-17 at
the user's request, checked against SoH 9.3's source) and 2 Ship 2 Harkinian (2S2H,
Majora's Mask; added 2026-11-17 at the user's request, checked against 2S2H 5.0.1's source
and libultraship), plus sm64coopdx (Super Mario 64, see its item below). SoH and 2S2H are
HarbourMasters ports on libultraship and behave the same; each port is one `Port` value
(`SHIP_OF_HARKINIAN`, `TWO_SHIP`, `COOP_DX`, in `PORTS` by key). A port's
`key` (`soh`, `2ship`, `sm64coopdx`) is everything: the `--with` choice, the config key of its program
(`<key>_data` for its data folder; both `MACHINE_KEYS`), RomM's `emulator` for its saves,
`hash_<key>` in state and its download folder `<key>/<OS_KEY>/` in APP_HOME. A game is a
port's when it's on one of its `platforms` (`n64`/`ngc`; sm64coopdx `n64` only) and its name
has the port's `words` ("ocarina"+"time", "majora"+"mask", "super"+"mario"+"64") and not
"3d" (`Port.plays`, `port_for`); it can then be played in the
port or RetroArch: `Launcher.choose_player` asks (default `default_player`: state `player`,
else the port if set up), `--with` or the GUI's "Play with" chips skip it.

- Archive: the port turns the ROM into `oot.o2r` (`oot-mq.o2r` for Master Quest) / `mm.o2r`
  once; `Port.launch` passes the ROM on the command line only while that file is missing
  (with it, the port would extract again). Neither reads a zipped ROM (2S2H's extractor
  refuses the `PK` header; RomM has the user's Majora's Mask as a `.zip`), so
  `unpacked_rom` puts the zip's biggest file into `<data>/.romm-launcher-rom/` and
  `Port.cleanup` removes that after the game closes.
- Data folder: the cwd on Windows and `$SHIP_HOME` on Linux (libultraship; the AppImages
  default to `~/.local/share/soh` / `~/.local/share/2ship`), so the launcher starts the port
  in `Port.data_dir` and sets `SHIP_HOME`.
- Saves: `PortSave` (a `FolderSave`) syncs the files in `<data>/<save_folder>` that match
  `save_files`, as `<ROM stem> (<port name>).zip`. SoH: `Save/*.sav` (`file1-3.sav`,
  `global.sav`; its `fileN-<time>.bak` and `.temp` files are never touched). 2S2H:
  `saves/fileN.json` and `fileNbackup.json` (the game's backup copies); its
  `fileN_invalid_<time>.json` and the old `global.json` (2S2H moves its contents into
  `2ship2harkinian.json` and deletes it) stay out. Save objects carry `emulator`/`exts`
  (None for RetroArch's), so `fetch_remote` looks only at that port's zips, and
  `pending_player` says which save a pending upload was. No new RomM writes were needed.
- Downloading (2026-11-17, at the user's request): `Launcher.setup_port` (from
  `ensure_port` and `--setup`, which asks for each port) offers "Download it for me" when
  `Port.find` finds nothing; the GUI asks with a Yes/No/Cancel dialog (`GuiApp.pick_port`)
  and runs `Launcher.download_port` before `play` on the worker thread. The GUI's settings
  page (`show_settings`) has a field per port (`port_entries`, Browse…, a Download button in
  `port_downloads` only while it isn't installed, status line `port_notes` from
  `port_status`); Download closes the settings and runs `GuiApp.download_port`; a cleared
  field removes the key from the config and this system's section. `Port.latest_release`
  reads GitHub's `releases/latest` for the port's `repo` (`HarbourMasters/Shipwright`;
  2S2H moved to `2ship2harkinian/2ship2harkinian`) and takes the asset ending in the
  port's `downloads` suffix for this system (SoH: `-win64.zip` / `.appimage`; 2S2H:
  `-win64.zip` / `-linux.zip`, a zip with `2ship.appimage` in it; x86_64 only, nothing for
  macOS/ARM); the file must come from `https://github.com/<repo>/releases/download/` and
  match the asset's `digest` (sha256) when GitHub lists one. `Port.install` writes an
  AppImage via `.part`; a zip is checked for unsafe paths, unpacked into `<os>.part`
  without `.pdb` debug symbols (2S2H's is 138 MB), and the folder holding the program
  (`soh.exe`, `2ship.exe`, or the AppImage, made executable) is moved in without replacing
  anything. An installed port is never replaced (no updates). Its config path is then
  relative (`soh/windows/soh.exe`, `config_path`; `Port.program` resolves it against
  APP_HOME), and its data folder is its own folder on Linux too. No RomM login is sent (it
  uses `core_downloads.http_download`).
- **sm64coopdx** (`COOP_DX`, Super Mario 64 with online co-op; added 2026-11-17 at the
  user's request, checked against sm64coopdx 1.5.1's source: `cliopts.c`,
  `rom_checker.cpp`, `platform.c`, `fs/fs.h`). Not libultraship, so two `Port` fields
  switch to its way. `savepath_arg="--savepath"`: the data folder is passed as
  `--savepath <data>` (no `SHIP_HOME`; it's started in its program folder), and its default
  is sm64coopdx's own per-user folder (`%APPDATA%\sm64coopdx`, `~/.local/share/sm64coopdx`)
  unless the launcher installed it. `rom_md5`: it takes no ROM argument but scans its data
  folder (and exe folder) for a `.z64` with the US MD5 `20b854b2…` and copies it to
  `baserom.us.z64`, so `place_rom` writes the ROM there as `baserom.us.z64` (zip unpacked,
  `.v64`/`.n64` byte order fixed by `big_endian_n64`) only if the MD5 matches; otherwise
  `RomMismatch`, the launcher warns and starts it without a ROM (it then asks for one to be
  dropped on its window). The user's RomM copy (`Super Mario 64 (USA).z64` in a zip)
  matches. Save: `sm64_save_file.bin` (512-byte EEPROM) in the data folder itself
  (`save_folder=""`), as `<ROM stem> (sm64coopdx).zip`, emulator `sm64coopdx`. Downloads:
  `_windows.zip` (`sm64coopdx.exe`) / `_linux.zip` (a plain `sm64coopdx` program, not an
  AppImage: `linux_exe`/`linux_find` say what the Linux program is called); there's also a
  `_steamos.zip`, which isn't used. It ships its own `coopdx_updater`; the launcher never
  updates it.

**Two-screen games.** If the core is in `SCREEN_LAYOUTS` (melondsds, melonds, desmume,
citra; option names/values checked against each core's source), `Launcher.apply_screen_layout`
asks before launch: 1 = main screen only, 2 = both, 3 = main + small touch screen in the
bottom-right corner (`SCREEN_CHOICES`; "corner" only for cores whose code puts it there:
melonDS DS hybrid-top + `melonds_hybrid_small_screen = "one"` + ratio 3, Citra "Large
Screen, Small Screen"). Default: the game's last choice (`screen_layout` in state), else
both; `--screen main|both|corner` skips the question. For layouts that show the touch
screen (both, corner), `TOUCH_CONTROLS` also sets the controller pointer's visibility
from config `show_touch_pointer` (default true; melonDS DS `melonds_show_cursor`
timeout/disabled, Citra `citra_render_touchscreen` enabled/disabled, which is off by
default in Citra, so the pointer is invisible without it) and prints the controls:
right stick + R2 on melonDS DS, right stick + R3 on Citra. "main" leaves it alone. It writes
the core option with `set_core_options` (changes only those lines) into the file
RetroArch will load, found by `core_options_file`: `<config>/<core name>/<rom stem>.opt`,
then `<content folder>.opt`, then `<core name>.opt` (or the single global file if
`global_core_options` is on). `<config>` is `rgui_config_directory` from `retroarch.cfg`;
`<core name>` is `core_display_name` (from the `.info`).

**Missing cores are downloaded automatically** (`Launcher.ensure_core` → `download_core`,
config `auto_install_cores`, default true) from libretro's buildbot, where RetroArch's own
Core Downloader gets them: `install_core` fetches `<id>_libretro<ext>.zip` from
`core_updater_buildbot_cores_url` in `retroarch.cfg` (forced to https for
buildbot.libretro.com; other servers only if https), else
`BUILDBOT/nightly/<buildbot_platform()>/latest/`. Only that one file is taken from the
zip, written via `.part` + rename, and an existing core is never replaced.
`install_core_info` adds the core's `.info` from `assets/frontend/info.zip` into
`core_info_dir` (`libretro_info_path`), since the `.info` names the save folder. No RomM
login is sent to these downloads (`http_download` is separate from `RommClient`). If the
download fails, the launcher lists installed cores and saves the pick, as before.

Two kinds of save (both have `hash()`, `hash_of(data)`, `read()`, `write(data)`, `modified()`,
`location`, `upload_name`); `Game.save(rom_file)` picks one:
- `FileSave`: one file, `<saves>/<core>/<ROM stem>.srm` (n64, gba, nds).
- `FolderSave` (3DS, `save_layout: "citra"`): Citra's folder
  `<saves>/Citra/Citra/sdmc/Nintendo 3DS/<id0>/<id1>/title/<high>/<low>/data`, synced with
  RomM as `<ROM stem>.zip`. The title folder comes from RomM's `save_target`
  (e.g. `00040000/00033500`), else `title_id`, else the .3ds/.cxi header. Its hash covers
  file names + contents only, so folder and zip hash the same. Zips are built
  reproducibly, unsafe paths are refused, and writes unpack beside the folder then swap.

## Files on disk

Everything lives in **one folder**, `APP_HOME` = **the folder the script is in**
(`$ROMM_LAUNCHER_HOME` overrides it). Code and data sit side by side, so the whole folder
can be copied to another machine; `.gitignore` keeps the data out of git. `--paths`
prints it all.

```
romm-launcher/
  romm_launcher.py, rommlauncher/, tests/, README.md, CLAUDE.md, LICENSE, .gitignore   the code (in git)
  CLAUDE.local.md  notes about your own setup (git-ignored)
  config.json      use_romm, RomM URL, username, ROM folder, platform cores; RetroArch paths per system (mode 600)
  state.json       per RomM ROM id: last synced hash/time, last_played, play_seconds, pending_upload
                   (without RomM also status, favorite)
  library.json     cached library (+ favorites ids), for offline play
  backups/<rom_id>/  last 20 replaced saves (3DS saves as .zip)
  roms/<platform>/   downloaded ROMs (cache; RomM has the real copies)
  covers/          cover art for the GUI, <rom id>.png (cache)
  soh/<system>/    Ship of Harkinian when the launcher downloaded it (+ its settings, .o2r, Save/)
  2ship/<system>/  2 Ship 2 Harkinian likewise (+ mm.o2r, saves/)
  sm64coopdx/<system>/  sm64coopdx likewise (+ baserom.us.z64, sm64_save_file.bin)
```

`roms_dir` in config is relative to `APP_HOME` (default `"roms"`; resolve it with
`roms_root(cfg)`), so moving or renaming the folder doesn't break it. An absolute path
still works for a ROM folder elsewhere.

**One `config.json` for Windows and Linux.** The RomM login, `roms_dir` and `platforms`
are shared at the top level. RetroArch's paths (`MACHINE_KEYS`: `retroarch`, `cores_dir`,
`saves_dir`, `sort_saves_by_core`) live in a section per system, `"windows"`, `"linux"` or
`"macos"` (`OS_KEY` is the current one). `load_config` copies this system's section onto
the top-level keys, so the rest of the code just reads `cfg["retroarch"]` etc., and
`config_for_disk` puts them back into the section when saving (other systems' sections
are kept untouched). A section may also hold its own `roms_dir` for an absolute path
that only exists on that system. If there's no section for this system, the next start
asks only for RetroArch's paths (`Launcher.setup_retroarch`). An older config with the
paths at the top level is read as the section of the system those paths belong to
(`guess_os`) and rewritten once by `migrate_flat_config()` from `main()`.

`migrate_legacy_dirs()` moves the contents of older locations (`~/.config/romm-launcher`,
`~/.local/share/romm-launcher`, `%APPDATA%`/`%LOCALAPPDATA%\romm-launcher`) into
`APP_HOME`, plus a ROM folder that was an old default (`~/Documents/Roms`, `<old>/roms`).
It never overwrites anything already in `APP_HOME`.

## RomM API notes

Checked against RomM 5.4.1's `/openapi.json` (Nov 2026). Check `https://<romm>/api/docs`
when something fails.

- Auth: HTTP Basic works on API routes.
- `GET /api/users/me`: used for "Test connection".
- `GET /api/roms?limit=10000&with_char_index=false&with_filter_values=false&with_rom_id_index=false`
  returns a page `{"items": [...]}` with `id`, `name`, `fs_name`, `fs_size_bytes`,
  `platform_fs_slug`, `platform_display_name`, `has_multiple_files`, `title_id`,
  `save_target`, …
- `GET /api/roms/{id}/content/{fs_name}?purpose=play` downloads the ROM (a zip for
  multi-file ROMs, which the launcher unpacks into a folder).
- `GET /api/saves?rom_id=…` lists saves (`id`, `file_name`, `file_extension`,
  `updated_at`, `download_path`, `missing_from_fs`, `emulator`, …). Fallback for older
  versions: `GET /api/roms/{id}` → `user_saves`.
- Adding ROMs (RomM 5.4.1, `backend/endpoints/roms/upload.py`): `POST /api/roms/upload/start`
  with headers `x-upload-platform` (platform id), `x-upload-filename`, `x-upload-total-size`,
  `x-upload-total-chunks` and an optional JSON body `{"filename": …}` (wins over the Latin-1
  header) → `{"upload_id": <uuid>}`; a platform-folder upload refuses an existing file name
  (400 "already exists"). Then `PUT /api/roms/upload/{id}` per chunk (raw body, header
  `x-chunk-index`, equal chunks of ceil(size/chunks), last shorter, ≤ 64 MB), `POST
  .../complete` (201), or `.../cancel`. The file is only listed after a scan: `POST
  /api/tasks/scan` `{"type": "quick", "platforms": [id]}` → `{"task_id": …}`, followed with
  `GET /api/tasks/{task_id}` (`status` finished/failed/…). Token scopes: `roms.write`,
  `tasks.run`, `platforms.write`.
- Platforms: `GET /api/platforms` (this RomM's), `GET /api/platforms/supported` (every
  platform RomM knows, ~470; the ones not on this RomM have `id: -1`), `POST /api/platforms`
  `{"fs_slug": …}` creates one (folder + entry, 201 with the platform). The add-game
  question lists all of them (`Launcher.add_rom`); long choices are searched (`ui.filter_options`,
  `TerminalUI._choose_by_search`, `GuiApp.ask_from_list`).
- Status and favorites (RomM 5.4.1, `backend/endpoints/roms/__init__.py` `update_rom_user`,
  `collections.py`, web UI `useFavoriteToggle.ts`): each item of `GET /api/roms` has
  `rom_user` (`status`: incomplete/finished/completed_100/retired/never_playing or null;
  also `backlogged`, `now_playing`, `rating`, ... which the launcher leaves alone). `PUT
  /api/roms/{id}/props` with JSON sets only the fields given (scope `roms.user.write`).
  Favorites: `GET /api/collections` (each with `is_favorite`, `rom_ids`, `owner_username`);
  the web UI creates `POST /api/collections?is_favorite=true&is_public=false` with form
  `name=Favorites` when missing; `POST`/`DELETE /api/collections/{id}/roms`
  `{"rom_ids": [...]}` add/remove (scope `collections.write`).
- `GET /api/saves/{id}/content` downloads the raw file. Fallback: the save's
  `download_path`. (Newer RomM can convert saves between cores via a `core` query param.
  Not used yet.)
- `POST /api/saves?rom_id=…&emulator=…` with multipart field **`saveFile`** uploads.
  Re-uploading the same file name and emulator updates that save. Newer RomM also supports
  `device_id`, `slot`, `overwrite`, and `autocleanup`, and can return 409 conflicts when
  `device_id` is used. The launcher doesn't use those yet.

## Testing

- `python3 tests/test_launcher.py` runs the API client, `decide()`, the
  download-ROM → place-save → upload flow (`play(..., launch=False)`), 3DS folder saves,
  `retroarch.cfg` handling and folder migration against a fake RomM HTTP server, in a temp
  folder. Run it after any change, and add cases for new behaviour.
  (On Windows set `PYTHONIOENCODING=utf-8`.) It imports the package's modules by name
  (`from rommlauncher import config, games, ...`) and swaps things like `paths.CONFIG_FILE`
  or `system.OS_KEY` on those modules. It also drives a hidden GUI window when a display
  is available.
- `python3 -m py_compile romm_launcher.py rommlauncher/*.py` for a quick syntax check.
- Launching RetroArch has no automated test. Test it by running the script for real.

## Ideas / possible next steps

- A short `romm` command (`~/.local/bin/romm` symlink on Linux, a `.cmd` on Windows).
- An option to delete downloaded ROMs to free space (they can always be re-downloaded).
- Use RomM's `device_id` / save-sync features for server-side conflict detection.
- Support more than one save per game (other cores, slots).
