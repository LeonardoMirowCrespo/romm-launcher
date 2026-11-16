# RomM Launcher

Play the games in your self-hosted [RomM](https://github.com/rommapp/romm) library on your
own computer in [RetroArch](https://www.retroarch.com/), with RomM keeping your saves.

RomM can run games in the browser, but that's often too slow (N64, 3DS). This launcher
downloads the game, puts your newest save from RomM in place, starts RetroArch, and uploads
the save back to RomM when you quit. Play on one computer, then carry on from the same save
on another.

- Plain Python, standard library only (nothing to install). Runs on **Windows** and **Linux**.
- Your saves are never thrown away: anything that gets replaced is backed up first.
- Works offline with games you've already downloaded; saves upload the next time you're online.

## What you need

- **Python 3.9 or newer.** On Windows, get it from [python.org](https://www.python.org/downloads/).
- **RetroArch**. On Windows, the standalone build from retroarch.com. On Linux, the
  **Flatpak** from Flathub (`flatpak install flathub org.libretro.RetroArch`). Missing cores
  are downloaded automatically when you first play a game that needs them.
- A **RomM** server (tested with RomM 5.4) and an account on it.
- Optional: the `keyring` package (`pip install keyring`, or `python3-keyring` on
  Debian/Ubuntu) so your RomM password is remembered securely. Without it you're asked
  for the password each time. The password is never written to disk.

## Getting started

Download or clone this repository into a folder of its own, then run:

```bash
python3 romm_launcher.py
```

(On Windows: `python romm_launcher.py` or `py romm_launcher.py`.)

The first run asks for your RomM address, username and password, and where RetroArch is.
After that you get a list of your library: type a number or part of a name to play.

## The window (`--gui`)

```bash
python3 romm_launcher.py --gui
```

Opens the launcher as a window, like a game store's library:

- **Sign in** with your RomM address, username and password (the same as in the
  terminal; with `keyring` installed you can have the password remembered).
- **Your whole RomM library** as covers, with the platform in the corner. Each one says
  whether the game is **installed** (on this computer) or **in the cloud** (downloaded the
  first time you play it).
- **Search** at the top (Ctrl+F; Escape clears it), and chips to show **all games**, only
  the **installed** ones, or **one platform** (N64, DS, …).
- Click a game for its details on the right: **Play** (or double-click the cover),
  **Download only**, **Upload save**, for DS/3DS games the **screens** (main, both, corner),
  and when you last played and synced. The game you played last is selected at the start.
- The bar at the bottom shows what's happening (downloads, save sync, RetroArch running);
  click **Activity** for the full log, which also opens by itself when something fails.
  Questions (like a save that changed in two places) come up as dialogs.
- The icons on the left: library, **refresh**, **settings** (RetroArch's location, the ROM
  folder, automatic core downloads and the touch pointer) and **log out**. On the first
  start the launcher finds RetroArch by itself.

Covers are downloaded from RomM once and kept in `covers/`. The window needs Tkinter, which
comes with Python on Windows; on Debian/Ubuntu install `python3-tk`. To start it without a
terminal window on Windows, use `pythonw romm_launcher.py --gui`.

## Usage

| Command | What it does |
| --- | --- |
| `romm_launcher.py` | Pick a game from your library and play it |
| `romm_launcher.py --gui` | Open the launcher as a window |
| `romm_launcher.py zelda` | Play the game matching "zelda" (asks if several match) |
| `romm_launcher.py --list` | Show your library |
| `romm_launcher.py --upload zelda` | Upload this computer's save of a game to RomM |
| `romm_launcher.py --no-launch zelda` | Download the ROM and save, but don't start RetroArch |
| `romm_launcher.py --screen main mario` | DS/3DS: pick the screen layout without asking (`main`, `both` or `corner`) |
| `romm_launcher.py --setup` | Change the RomM address, login or RetroArch paths |
| `romm_launcher.py --paths` | Show where settings, ROMs and save backups are kept |

**When you're done playing:** press F1 → **Close Content** (this makes RetroArch write the
save), then quit RetroArch. The launcher waits for RetroArch to close and then uploads the save.

## How saves are synced

Before a game starts, the launcher compares the save on this computer with the newest one
in RomM and the one it last synced:

- Only RomM's save changed → it's downloaded.
- Only this computer's save changed (e.g. a failed upload last time) → it's kept, and
  uploaded after you play.
- Both changed since the last sync → you're asked which one to keep.

Every save that gets replaced is copied to `backups/<game id>/` first (the last 20 per game
are kept). That includes RomM's copy: uploading replaces RomM's save of the same name, so
the launcher downloads and backs up that save first, and doesn't upload if it can't. If an
upload fails, you're told, the save on your computer is left alone, and the upload is
retried the next time you start the launcher.

## What it does on your RomM server

Only reading, and uploading saves. It can't delete or change anything else: every request
is checked before it's sent, and anything other than a read or a save upload to your RomM
server (deleting, editing, other servers) is refused.

## Platforms

These work out of the box:

| RomM platform | RetroArch core | Save |
| --- | --- | --- |
| `n64` | Mupen64Plus-Next | `.srm` |
| `gba` | mGBA | `.srm` / `.sav` |
| `nds`, `nintendo-dsi` | melonDS DS | `.srm` / `.sav` |
| `3ds`, `new-nintendo-3ds` | Citra | Citra's save folder, synced as a `.zip` |

**Two-screen games (DS, DSi, 3DS):** before the game starts, you choose a layout:

```
  This game has two screens. Which layout?
      1  Main screen only
      2  Both screens  (last time)
      3  Main screen + small touch screen in the bottom-right corner
  Layout [2]:
```

Enter keeps the choice from last time (both screens the first time). The launcher sets
this in RetroArch's options for the core (melonDS DS, melonDS, DeSmuME or Citra), so you
can still change it while playing (F1 → Core Options). With "main screen only", games that
need the touch screen are hard to play; pick 2 or 3 for those.

Choice 3 is offered for melonDS DS and Citra. With melonDS DS the small screen is a third
of the size of the main one and the whole picture is almost exactly 16:9, so in fullscreen
on a 16:9 monitor the small screen sits right in the monitor's corner. Citra's small
screen is a quarter of the size, with thin bars above and below on a 16:9 monitor. You can
touch the small screen with the mouse.

**Touch screen with a controller:** the right stick moves a pointer over the touch screen,
and a button touches: **R2** on DS (melonDS DS; hold L2 to move faster), **R3** on 3DS
(Citra). When you pick a layout that shows the touch screen (2 or 3), the launcher prints
these controls and makes sure the pointer is visible. To hide the pointer instead, set
`"show_touch_pointer": false` in `config.json`.

If a core isn't installed, the launcher downloads it from libretro (the same place
RetroArch's Core Downloader gets cores) into RetroArch's cores folder and starts the game.
It never replaces a core you already have. If the download fails, it lists the cores you
do have and remembers your pick. To turn automatic downloads off, set
`"auto_install_cores": false` in `config.json`.

To add another platform, add it to `platforms` in `config.json`, using the
platform's folder name in RomM and the core's name without `_libretro.dll`/`.so`:

```json
"platforms": {
  "snes": { "core": "snes9x", "save_exts": ["srm"] }
}
```

The launcher follows RetroArch's own save settings from `retroarch.cfg`, including "Sort
Saves into Folders by Core Name".

## Using it on more than one computer

Everything the launcher keeps is in its own folder, next to the script:

```
romm-launcher/
  romm_launcher.py   start the launcher with this
  rommlauncher/      the launcher's code
  config.json     settings (RomM address, username, RetroArch paths, cores)
  state.json      what was last synced, per game
  library.json    your library, cached for offline play
  backups/        replaced saves
  roms/           downloaded games (RomM has the real copies)
  covers/         cover art for the window
```

You can copy the whole folder to another computer, including between Windows and Linux.
`config.json` keeps RetroArch's paths in a section per system, so one file works on both:

```json
{
  "romm_url": "https://romm.example.com",
  "username": "me",
  "roms_dir": "roms",
  "platforms": { "...": "..." },
  "windows": { "retroarch": "C:\\RetroArch-Win64\\retroarch.exe", "cores_dir": "...", "saves_dir": "..." },
  "linux": { "retroarch": "flatpak:org.libretro.RetroArch", "cores_dir": "...", "saves_dir": "..." }
}
```

`"flatpak:org.libretro.RetroArch"` means the Flatpak RetroArch: the launcher starts it with
`flatpak run org.libretro.RetroArch`, and its cores and saves are in
`~/.var/app/org.libretro.RetroArch/config/retroarch/`. Setup finds it by itself; you can
also type `flatpak` when it asks for the RetroArch program. Other Linux builds (Steam,
your distribution's package) work too: give the path to the `retroarch` program instead.

The first time you start it on a system that has no section yet, it asks only where
RetroArch is. You can also just install it separately on each computer; the saves meet in
RomM either way.

Set the `ROMM_LAUNCHER_HOME` environment variable to keep the launcher's data in another folder.

## Known limits

- **macOS** may work but hasn't been tested.
- One save per game: save states and multiple save slots aren't synced.
- If you use Steam's RetroArch, turn off Steam Cloud for it so it doesn't fight RomM over
  the saves folder.

## Troubleshooting

- **N64 shows a black screen but has sound:** RetroArch's video driver must suit the core's
  graphics plugin. Try `glcore` or `vulkan` (Settings → Drivers → Video).
- **Login fails:** check the address with `--setup` and try it in a browser.
  RomM's API docs are at `https://<your-romm>/api/docs`.
- **Where did my save go?** `--paths` shows the folders; `backups/` has the earlier versions.

## Development

`romm_launcher.py` only starts the launcher; the code is in the `rommlauncher/` package,
one file per topic:

| File | What's in it |
| --- | --- |
| `system.py` | This computer: operating system, core file extension, optional keyring |
| `paths.py` | Where the launcher's files are (`config.json`, `state.json`, `backups/`, …) |
| `ui.py` | Messages, questions and progress bars, for the terminal or the window |
| `util.py` | Small helpers: JSON files, hashes, times, safe file writes |
| `retroarch.py` | RetroArch on this computer: where it is, `retroarch.cfg`, cores, core options |
| `config.py` | `config.json`: defaults, per-system sections, moving old settings |
| `dualscreen.py` | DS/3DS screen layouts and touch-screen controls |
| `core_downloads.py` | Downloading missing cores from libretro |
| `romm.py` | The RomM API, with its safety lock (only reads and save uploads) |
| `saves.py` | Save files, 3DS save folders, backups, deciding which save is newer |
| `games.py` | A game from the library: ROM and save locations, search, covers |
| `launcher.py` | Logging in, the library, and playing a game (download, sync, run, upload) |
| `gui.py` | The window (`--gui`) |
| `cli.py` | The command line |

You can also start it with `python3 -m rommlauncher`. To run the tests:

```bash
python3 tests/test_launcher.py
```

The tests run the launcher against a fake RomM server in a temporary folder. On Windows,
set `PYTHONIOENCODING=utf-8` first. Starting RetroArch isn't covered by the tests.

## License

[MIT](LICENSE)
