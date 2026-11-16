# RomM Launcher

Play the games in your self-hosted [RomM](https://github.com/rommapp/romm) library on your
own computer in [RetroArch](https://www.retroarch.com/), with RomM keeping your saves.

RomM can run games in the browser, but that's often too slow (N64, 3DS). This launcher
downloads the game, puts your newest save from RomM in place, starts RetroArch, and uploads
the save back to RomM when you quit. Play on one computer, then carry on from the same save
on another.

- One Python file, standard library only. Runs on **Windows** and **Linux**.
- Your saves are never thrown away: anything that gets replaced is backed up first.
- Works offline with games you've already downloaded; saves upload the next time you're online.

## What you need

- **Python 3.9 or newer.** On Windows, get it from [python.org](https://www.python.org/downloads/).
- **RetroArch** with the cores for your platforms (Main Menu → Online Updater → Core Downloader).
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

## Usage

| Command | What it does |
| --- | --- |
| `romm_launcher.py` | Pick a game from your library and play it |
| `romm_launcher.py zelda` | Play the game matching "zelda" (asks if several match) |
| `romm_launcher.py --list` | Show your library |
| `romm_launcher.py --upload zelda` | Upload this computer's save of a game to RomM |
| `romm_launcher.py --no-launch zelda` | Download the ROM and save, but don't start RetroArch |
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
are kept). If an upload fails, you're told, the save on your computer is left alone, and
the upload is retried the next time you start the launcher.

## Platforms

These work out of the box:

| RomM platform | RetroArch core | Save |
| --- | --- | --- |
| `n64` | Mupen64Plus-Next | `.srm` |
| `gba` | mGBA | `.srm` / `.sav` |
| `nds` | melonDS DS | `.srm` / `.sav` |
| `3ds` | Citra | Citra's save folder, synced as a `.zip` |

If a core isn't installed, the launcher lists the cores you do have and remembers your
pick. To add another platform, add it to `platforms` in `config.json`, using the
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
  romm_launcher.py
  config.json     settings (RomM address, username, RetroArch paths, cores)
  state.json      what was last synced, per game
  library.json    your library, cached for offline play
  backups/        replaced saves
  roms/           downloaded games (RomM has the real copies)
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
  "linux": { "retroarch": "~/.steam/steam/steamapps/common/RetroArch/retroarch", "cores_dir": "...", "saves_dir": "..." }
}
```

The first time you start it on a system that has no section yet, it asks only where
RetroArch is. You can also just install it separately on each computer; the saves meet in
RomM either way.

Set the `ROMM_LAUNCHER_HOME` environment variable to keep the launcher's data in another folder.

## Known limits

- **Flatpak RetroArch** (Linux) isn't supported yet; use the Steam version, the AppImage,
  or your distribution's package.
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

```bash
python3 tests/test_launcher.py
```

The tests run the launcher against a fake RomM server in a temporary folder. On Windows,
set `PYTHONIOENCODING=utf-8` first. Starting RetroArch isn't covered by the tests.

## License

[MIT](LICENSE)
