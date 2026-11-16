# RomM Launcher

A Python app for playing games from a self-hosted **RomM** library locally in RetroArch,
with RomM as the home of the saves. It runs on Windows and Linux, in the terminal
(default) or as a window (`--gui`, Tkinter, a game-store style library; work on the `gui`
branch since 2026-11-16). `romm_launcher.py` is only the start script; the code is the
`rommlauncher/` package (split from one file on 2026-11-16, at the user's request, to
make it easier to read and work on).

It's published as a public GitHub repo; `README.md` is the user-facing guide (keep it in
step with behaviour changes). Notes about a particular setup (RomM address, PCs, paths)
go in `CLAUDE.local.md`, which is git-ignored. Nothing personal (server addresses,
usernames) belongs in the committed files.

Usage: `romm_launcher.py` (pick from library), `romm_launcher.py zelda` (straight to a match),
`--list`, `--upload <game>`, `--add <file>...` (add game files to RomM), `--no-launch`, `--screen main|both|corner`, `--gui`, `--setup`, `--paths` (alias `--backups`).
First run asks for setup.

Flow when a game is picked:
1. Make sure the ROM is in `<ROM folder>/<platform fs_slug>/<fs_name>` (also accepts the
   same file name anywhere under the ROM folder); download it from RomM if not. The ROM
   folder is a cache the launcher manages; RomM holds the real copies.
2. Fetch the newest save for the game from RomM, decide whether it's newer than the local one,
   and put it into RetroArch's saves folder (backing up whatever it replaces).
3. Launch RetroArch with the platform's core and the ROM, and wait for the process to exit.
4. If the local save changed, upload it to RomM. A failed upload is marked `pending_upload`
   in state and retried at the next start.

## Constraints

- **Standard library only.** No pip dependencies. `keyring` is optional
  (`python3-keyring` / `pip install keyring`) and the app must keep working without it.
- **Never store the RomM password in plain text.** It goes in the system keyring if
  available, otherwise it's asked with `getpass` each run and kept in memory only.
- **Never lose a save.** Anything that overwrites a save backs it up first. A failed upload
  must be loud and leave the local save untouched.
- **Never delete or change anything on RomM.** The launcher only reads, uploads saves, and
  (since 2026-11-16, at the user's request) adds new ROM files.
  `RommClient._check` enforces it on every request: GET only to the RomM server, writes only
  as listed in `RommClient.ALLOWED_WRITES` (method, path pattern, query parameters, body
  check): POST `/api/saves` with `rom_id`/`emulator`, POST `/api/token`, the chunked ROM
  upload (POST `/api/roms/upload/start` with a body of only `filename`, so never `rom_id`,
  `folder` or `overwrite`; PUT `/api/roms/upload/<uuid>`; POST `.../<uuid>/complete|cancel`
  without a body), POST `/api/tasks/scan` only as `{"type": "quick", "platforms": [ids]}`,
  and POST `/api/platforms` only as `{"fs_slug": "<plain slug>"}` (creates a platform the
  user picked that isn't on their RomM yet; RomM makes the folder and entry).
  Never DELETE/PATCH; redirects to other servers are refused. Don't widen that list without
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
| `util.py` | `load_json`, `save_json`, `sha256`, `file_hash`, `parse_time`, `fmt_time`, `write_new_file` |
| `retroarch.py` | Flatpak/Steam/default RetroArch, `read_retroarch_cfg`, `core_id`, `find_core`, `installed_cores`, `core_display_name`, `core_save_folder`, `KNOWN_CORE_FOLDERS`, core options (`retroarch_config_dir`, `core_options_file`, `set_core_options`) |
| `config.py` | `DEFAULT_PLATFORMS`, `MACHINE_KEYS`, `default_config`, `load_config`, `config_for_disk`, `guess_os`, `roms_root`, `migrate_flat_config`, `migrate_legacy_dirs` |
| `dualscreen.py` | `SCREEN_CHOICES`, `SCREEN_LAYOUTS`, `TOUCH_CONTROLS`, `screen_choices(game)` |
| `core_downloads.py` | buildbot: `install_core`, `install_core_info`, `core_download_url`, `http_download`, `CoreInstallError` |
| `romm.py` | `RommClient` (+ safety lock `_check`, `ALLOWED_WRITES`; `upload_rom`, `start_scan`, `wait_for_task`, `list_platforms`), `RommError`, `LoginError` |
| `saves.py` | `FileSave`, `FolderSave`, 3DS title ID helpers, `backup_bytes`/`backup_file`, `RemoteSave`, `fetch_remote`, `decide` |
| `games.py` | `Game`, `index_roms`, `unpack_zip`, `filter_games`, GUI library helpers (`library_filters`, `filter_library`, `local_game_ids`), `fetch_cover`, `ROM_PLATFORMS`/`guess_platform`, `game_facts` |
| `launcher.py` | `Launcher` (setup, login, library, picker, `play`, `upload`, `retry_pending`, `add_rom`, ...) |
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
  config.json      RomM URL, username, ROM folder, platform cores; RetroArch paths per system (mode 600)
  state.json       per RomM ROM id: last synced hash/time, last_played, pending_upload
  library.json     cached library, for offline play
  backups/<rom_id>/  last 20 replaced saves (3DS saves as .zip)
  roms/<platform>/   downloaded ROMs (cache; RomM has the real copies)
  covers/          cover art for the GUI, <rom id>.png (cache)
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
