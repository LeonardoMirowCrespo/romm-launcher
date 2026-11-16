# RomM Launcher

`romm_launcher.py` is a single-file Python **command-line** app for playing games from a
self-hosted **RomM** library locally in RetroArch, with RomM as the home of the saves.
It runs on Windows and Linux. (It used to be a Tkinter GUI; replaced by the CLI in Nov 2026.)

It's published as a public GitHub repo; `README.md` is the user-facing guide (keep it in
step with behaviour changes). Notes about a particular setup (RomM address, PCs, paths)
go in `CLAUDE.local.md`, which is git-ignored. Nothing personal (server addresses,
usernames) belongs in the committed files.

Usage: `romm_launcher.py` (pick from library), `romm_launcher.py zelda` (straight to a match),
`--list`, `--upload <game>`, `--no-launch`, `--screen main|both|corner`, `--setup`, `--paths` (alias `--backups`).
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
- **Never delete or change anything on RomM.** The launcher only reads, and uploads saves.
  `RommClient._check` enforces it on every request: GET only to the RomM server, POST only
  to the paths and query parameters in `RommClient.ALLOWED_POSTS` (`/api/saves` with
  `rom_id`/`emulator`, `/api/token`), never DELETE/PUT/PATCH; redirects to other servers
  are refused. Don't widen that list without a very good reason. An upload replaces
  RomM's save of the same name, so `Launcher.backup_romm_copy` downloads and backs up
  that save first (`romm-before-upload`); if that fails, nothing is uploaded.
- Must stay a single file that can be copied between machines (Windows and Linux).

## Code layout (`romm_launcher.py`)

The top half has **no user interaction**, so it can be imported and tested:

- Terminal helpers: `say/ok/warn/fail`, `ask`, `ask_yes`, `Progress`. `Quit` is raised on
  q / Ctrl+C / Ctrl+D at a prompt.
- Folders: `APP_HOME` (see "Files on disk"); `migrate_legacy_dirs()` is called from
  `main()` and leaves things in place if a file is in use.
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

Save paths follow RetroArch's own settings, read from `retroarch.cfg` at runtime:
`sort_savefiles_enable`, `sort_savefiles_by_content_enable`, `savefiles_in_content_dir`
(`savefile_directory` pre-fills setup).

Platforms → cores (`DEFAULT_PLATFORMS`, overridable in config `platforms`):
n64 → `mupen64plus_next`, gba → `mgba`, nds and nintendo-dsi → `melondsds`, 3ds and
new-nintendo-3ds → `citra`.

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
  romm_launcher.py, tests/, CLAUDE.md, .gitignore   the code (in git)
  CLAUDE.local.md  notes about your own setup (git-ignored)
  config.json      RomM URL, username, ROM folder, platform cores; RetroArch paths per system (mode 600)
  state.json       per RomM ROM id: last synced hash/time, last_played, pending_upload
  library.json     cached library, for offline play
  backups/<rom_id>/  last 20 replaced saves (3DS saves as .zip)
  roms/<platform>/   downloaded ROMs (cache; RomM has the real copies)
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
  (On Windows set `PYTHONIOENCODING=utf-8`.)
- `python3 -m py_compile romm_launcher.py` for a quick syntax check.
- Launching RetroArch has no automated test. Test it by running the script for real.

## Ideas / possible next steps

- A short `romm` command (`~/.local/bin/romm` symlink on Linux, a `.cmd` on Windows).
- An option to delete downloaded ROMs to free space (they can always be re-downloaded).
- Use RomM's `device_id` / save-sync features for server-side conflict detection.
- Support more than one save per game (other cores, slots).
