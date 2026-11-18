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
- Add games you own (your own cartridge dumps) to RomM from the launcher.
- **No RomM server? It works without one too:** your games come from a folder on your
  computer and your saves stay there (see [Without RomM](#without-romm)).
- Plays Ocarina of Time in [Ship of Harkinian](https://www.shipofharkinian.com), Majora's
  Mask in [2 Ship 2 Harkinian](https://github.com/2ship2harkinian/2ship2harkinian) and Super
  Mario 64 in [sm64coopdx](https://github.com/coop-deluxe/sm64coopdx) (with online co-op),
  the PC ports, if you like (widescreen, high resolution, 60 fps), with their saves in RomM too.

## What you need

- **Python 3.9 or newer.** On Windows, get it from [python.org](https://www.python.org/downloads/).
- **RetroArch**. On Windows, the standalone build from retroarch.com. On Linux, the
  **Flatpak** from Flathub (`flatpak install flathub org.libretro.RetroArch`). Missing cores
  are downloaded automatically when you first play a game that needs them.
- Optional: a **RomM** server (tested with RomM 5.4) and an account on it. Without one,
  the launcher plays games from a folder on your computer.
- Optional: the `keyring` package (`pip install keyring`, or `python3-keyring` on
  Debian/Ubuntu) so your RomM password is remembered securely. Without it you're asked
  for the password each time. The password is never written to disk.

## Getting started

Download or clone this repository into a folder of its own, then run:

```bash
python3 romm_launcher.py
```

(On Windows: `python romm_launcher.py` or `py romm_launcher.py`.)

The first run asks whether you want to connect to a RomM server. If you do, it asks for
your RomM address, username and password; if not, which folder your games are in (and
nothing is ever sent anywhere). Then it asks where RetroArch is.
After that you get a list of your library: type a number or part of a name to play.

## Without RomM

Answer **No** to "Do you want to connect to a RomM server?" (in the window: **Play without
RomM**) and the launcher works on its own:

- Your library is the ROM folder (`roms/` in the launcher's folder unless you pick
  another). Put each game in a folder named after its platform, the way RomM names them:
  `roms/n64/`, `roms/gba/`, `roms/snes/`, `roms/psx/`, … (see [Platforms](#platforms)).
  A file straight in `roms/`, or in a folder with another name, gets its platform from its
  extension (`.z64` → N64, `.gba` → GBA, …). A folder inside a platform folder is one game
  of several files (it plays the `.m3u` in it, else the biggest file); a `.cue` and the
  `.bin` tracks it names are one game.
- `--add game.gba` (in the window: **+ Add game**) copies files you own into the right
  platform folder for you.
- Saves stay where RetroArch (or the PC port) writes them. Nothing is synced, uploaded or
  backed up by the launcher, and `--upload` isn't available.
- Everything else works the same: cores are downloaded when needed, the PC ports, the
  DS/3DS screen layouts.

To connect to a RomM server later, run `--setup` (or, in the window, the ☁ button at the
bottom left). Your saves on this computer are then uploaded the first time you play each
game with RomM. To go back, answer No in `--setup`, or use "No RomM?" on the window's
login screen.

## The window (`--gui`)

```bash
python3 romm_launcher.py --gui
```

Opens the launcher as a window, like a game store's library:

- **The first time**, choose **Connect to RomM** or **Play without RomM** (see
  [Without RomM](#without-romm)). Without RomM the window opens straight on your games
  from then on, and the ☁ button at the bottom left connects to a RomM server later.
- **Sign in** with your RomM address, username and password (the same as in the
  terminal; with `keyring` installed you can have the password remembered). After the
  first time, only the username and password are asked; the address is shown under the
  box, and a click on it changes it.
- **Your whole RomM library** as covers, each shown whole at its own size, with the
  platform in the corner. Each one says
  whether the game is **installed** (on this computer) or **in the cloud** (downloaded the
  first time you play it).
- **Search** at the top (Ctrl+F; Escape clears it), and chips to show **all games**, only
  the **installed** ones, or **one platform** (N64, DS, …).
- Click a game for its details on the right: **Play** (or double-click the cover),
  **Download only**, **Upload save**, for DS/3DS games the **screens** (main, both, corner),
  for Ocarina of Time, Majora's Mask and Super Mario 64 **Play with** (the PC port or RetroArch),
  when you last played and synced, and what RomM knows about the game: release year, genre,
  rating, modes, series, region and its description. The game you played last is selected
  at the start.
- **+ Add game** (next to the search) uploads game files you own to RomM, such as dumps of
  your own cartridges: pick the files, pick the platform (the file type suggests one; the
  list has every platform RomM supports, yours first, with a search field), and the
  launcher uploads them, asks RomM to scan for them, and they appear in your
  library, already installed on this computer.
- The bar at the bottom shows what's happening (downloads, save sync, RetroArch running);
  click **Activity** for the full log, which also opens by itself when something fails.
  Questions (like a save that changed in two places) come up as dialogs.
- The icons on the left: library, **refresh**, **settings** (RetroArch's location, the ROM
  folder, automatic core downloads, the touch pointer, and the PC ports: where each one is,
  or **Download** to have the launcher install it) and **log out**. On the first
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
| `romm_launcher.py --add game.gba` | Add game files you own to RomM, or without RomM to the ROM folder (asks for the platform) |
| `romm_launcher.py --no-launch zelda` | Download the ROM and save, but don't start RetroArch |
| `romm_launcher.py --screen main mario` | DS/3DS: pick the screen layout without asking (`main`, `both` or `corner`) |
| `romm_launcher.py --with soh ocarina` | Ocarina of Time: play it in Ship of Harkinian (`soh`) or RetroArch (`retroarch`) without asking |
| `romm_launcher.py --with 2ship majora` | Majora's Mask: play it in 2 Ship 2 Harkinian (`2ship`) or RetroArch without asking |
| `romm_launcher.py --with sm64coopdx mario 64` | Super Mario 64: play it in sm64coopdx (`sm64coopdx`) or RetroArch without asking |
| `romm_launcher.py --setup` | Choose RomM or no RomM; change the RomM address, login, RetroArch paths or the PC ports |
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

Reading, uploading saves, and adding new games when you ask it to. It can't delete or
change anything else: every request is checked before it's sent, and anything else
(deleting, editing, other servers) is refused. A new game is only ever added as a new file
in a platform's folder (RomM refuses a name that's already there, and the launcher never
asks it to overwrite), followed by a "quick" scan of that platform, which only picks up
files RomM hasn't seen yet. If you add a game for a platform your RomM doesn't have yet,
that platform is created first (an empty folder and its entry in RomM). Adding games needs
a RomM user that's allowed to upload ROMs, create platforms and start scans; if the scan
isn't allowed, the game shows up after RomM's next scan.

## Adding games

**+ Add game** in the window, or `--add <file>` in the terminal, uploads game files from
your computer to RomM: dumps of cartridges you own, or homebrew you're allowed to share.
The launcher doesn't download games from anywhere else.

You can add games for any platform RomM supports. To **play** them, the launcher needs a
RetroArch core for the platform: the platforms under "Platforms" below are set up already;
for any other platform it asks which installed core to use the first time, and saves
aren't synced for it until the platform has save settings in `config.json` (`platforms`).

**A game has no description, genre or release year?** Those come from RomM's metadata
(mostly IGDB). If RomM recognised the file but didn't match it to IGDB, open the game in
RomM's web page, use **Match** to search IGDB for it and apply the right result, then press
refresh (⟳) in the launcher.

## Platforms

These work out of the box:

| RomM platform | RetroArch core | Save |
| --- | --- | --- |
| `n64` | Mupen64Plus-Next | `.srm` |
| `gba` | mGBA | `.srm` / `.sav` |
| `nds`, `nintendo-dsi` | melonDS DS | `.srm` / `.sav` |
| `3ds`, `new-nintendo-3ds` | Citra | Citra's save folder, synced as a `.zip` |
| `nes`, `famicom` | FCEUmm | `.srm` |
| `snes`, `sfam` | Snes9x | `.srm` |
| `gb`, `gbc` | Gambatte | `.srm` |
| `virtualboy` | Beetle VB | `.srm` |
| `genesis`, `sms`, `gamegear`, `sg1000` | Genesis Plus GX | `.srm` |
| `sega32` | PicoDrive | `.srm` |
| `tg16` | Beetle PCE Fast | `.srm` |
| `wonderswan`, `wonderswan-color` | Beetle WonderSwan | `.srm` |
| `neo-geo-pocket`, `neo-geo-pocket-color` | Beetle NeoPop | `.srm` |
| `psx` | PCSX-ReARMed | memory card 1, `.srm` |
| `segacd`, `turbografx-cd`, `pokemon-mini`, `atari2600`, `atari7800`, `lynx`, `psp`, `dc` (Dreamcast), `ngc` (GameCube), `wii`, `saturn`, `arcade`, `neogeoaes`, `neogeomvs` | Genesis Plus GX, Beetle PCE Fast, PokeMini, Stella, ProSystem, Handy, PPSSPP, Flycast, Dolphin, Beetle Saturn, FinalBurn Neo | not synced (these cores keep saves their own way) |

Missing cores are downloaded automatically. Some of these platforms also need BIOS files
in RetroArch's `system` folder to run (PlayStation, Sega CD, TurboGrafx-CD, Saturn,
Dreamcast); RetroArch tells you when one is missing. Any other platform: the first time
you play, the launcher asks which installed core to use.

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

## Ocarina of Time in Ship of Harkinian

[Ship of Harkinian](https://www.shipofharkinian.com) (SoH) is Ocarina of Time rebuilt as
a PC game from the decompiled original: widescreen, any resolution, 60 fps, HD texture
packs and many options. It needs the game's ROM, which is where your RomM library comes in.
SoH supports the N64 and GameCube releases (NTSC and PAL, and Master Quest); check yours at
[ship.equipment](https://ship.equipment).

1. Have Ocarina of Time in your RomM library as an `n64` (or `ngc`) game. If you only have
   the ROM file: `romm_launcher.py --add "Ocarina of Time.z64"`.
2. Play it. The launcher asks how:

   ```
     The Legend of Zelda: Ocarina of Time can be played two ways. Which one?
         1  Ship of Harkinian: the PC version, with widescreen, high resolution and 60 fps
         2  RetroArch (mupen64plus_next): the original game
     Play with [1]:
   ```

   `--with soh` skips the question, and Enter keeps last time's choice.
3. The first time, the launcher offers to **download SoH for you**:

   ```
     Ship of Harkinian isn't on this computer yet.
         1  Download it for me: version 9.3.1, 69.6 MB, from its GitHub page
         2  I have it already: tell the launcher where
     Choice (q to skip) [1]:
   ```

   It takes the latest release from SoH's GitHub page (HarbourMasters/Shipwright): the
   Windows zip or the Linux `.appimage`, checked against the SHA-256 checksum GitHub lists
   for it, and installs it in `soh/windows/` or `soh/linux/` in the launcher's folder. In the
   window, a dialog asks the same when you press Play (No lets you pick a `soh.exe` you
   already have), and **Settings** has a Ship of Harkinian field with **Browse…** and
   **Download** (clear the field to stop using it). `--setup` offers it too. Nothing already in that folder is ever replaced, so an installed SoH
   isn't updated by the launcher: to get a newer version, delete `soh/<system>/soh.exe` (or
   the `.appimage`; your saves in `Save/` stay) and play again.

   If you'd rather install it yourself: on Windows, unzip it into a folder of its own (e.g.
   `C:\Games\SoH`, not inside OneDrive); on Linux, take the `.appimage`. Then pick "I have
   it already". The AppImage needs FUSE (`libfuse2` on Debian/Ubuntu).

The first start of SoH turns the ROM into `oot.o2r` (`oot-mq.o2r` for Master Quest): the
launcher hands it the ROM, SoH works for a minute and asks "Run SoH?" (answer Yes). After
that, SoH starts straight away. **When you're done:** save in the game, then close SoH's
window; the launcher uploads the save.

SoH keeps its own saves (the three file slots in its `Save` folder: `file1.sav` …
`file3.sav` and `global.sav`). The launcher syncs those with RomM as one zip, the same way
as other saves (backed up before anything is replaced). They're separate from RetroArch's
`.srm` save: the two can't read each other's, so progress doesn't carry over between them.
SoH's data folder is the one it's in on Windows and for a SoH the launcher downloaded, and
otherwise `~/.local/share/soh` on Linux (or SoH's folder, if its files are there already);
set `"soh_data"` in this system's section of `config.json` to use another one.

## Majora's Mask in 2 Ship 2 Harkinian

[2 Ship 2 Harkinian](https://github.com/2ship2harkinian/2ship2harkinian) (2S2H) is the same
for Majora's Mask, from the same team: widescreen, any resolution, 60 fps, texture packs and
many options. It takes the US N64 release and the GameCube Collector's Edition; check
yours at [2ship.equipment](https://2ship.equipment).

It works just like Ship of Harkinian above: play Majora's Mask and pick 2 Ship 2 Harkinian
(or `--with 2ship`), and the first time the launcher offers to download it from its GitHub
page into `2ship/windows/` or `2ship/linux/` (on Linux the release is a zip with the
`.appimage` in it), checked against GitHub's checksum. **Settings** in the window has a
field for it too.

The first start turns the ROM into `mm.o2r`, which takes a minute; after that it starts
straight away. 2S2H can't read a zipped ROM, so if RomM has the game as a `.zip`, the
launcher unpacks it into a temporary folder for that first start and removes it when the
game closes. **When you're done:** save in the game (an owl statue, or the Song of Time),
then close its window; the launcher uploads the save.

Its saves are in its `saves` folder: `file1.json` … `file3.json` and the game's own backup
copies `file1backup.json` …; they're synced with RomM as one zip, separate from
RetroArch's `.srm`. Copies of saves 2S2H couldn't read (`file1_invalid_….json`) stay out of
it. The data folder works as for SoH: its own folder on Windows and for a 2S2H the launcher
downloaded, otherwise `~/.local/share/2ship` on Linux; `"2ship_data"` in `config.json`
overrides it.

## Super Mario 64 in sm64coopdx

[sm64coopdx](https://github.com/coop-deluxe/sm64coopdx) (Super Mario 64 Coop Deluxe) is
Super Mario 64 rebuilt for the PC from the decompiled game, with **online co-op** (host or
join a game, or use its CoopNet server list), widescreen, 60 fps and Lua mods. It takes
only the **US** N64 release, unmodified.

Play Super Mario 64 and pick sm64coopdx (or `--with sm64coopdx`). The first time, the
launcher offers to download it from its GitHub page into `sm64coopdx/windows/` or
`sm64coopdx/linux/`, checked against GitHub's checksum, like the ports above; **Settings**
in the window has a field for it too.

sm64coopdx has no way to be handed a ROM, so the launcher puts the game's ROM into
sm64coopdx's folder as `baserom.us.z64` (unzipped, and turned into `.z64` byte order if
it's a `.v64` or `.n64`) before the first start. It only does that if the ROM is the US
release sm64coopdx takes (it checks the same MD5); otherwise it says so and sm64coopdx asks
you to drop the right ROM onto its window. **When you're done:** the game saves when you
get a star (pick "Save & Continue"); close its window and the launcher uploads the save.

The save is one file, `sm64_save_file.bin` (all four save files of the game), synced with
RomM as a zip, separate from RetroArch's `.srm`. sm64coopdx keeps it in its data folder:
its own folder for a copy the launcher downloaded; for one you installed yourself, the
folder sm64coopdx normally uses, `%APPDATA%\sm64coopdx` on Windows or
`~/.local/share/sm64coopdx` on Linux (so your existing save there is the one that's
synced). The launcher starts it with `--savepath` pointing there; `"sm64coopdx_data"` in
`config.json` picks another folder.

## Using it on more than one computer

Everything the launcher keeps is in its own folder, next to the script:

```
romm-launcher/
  romm_launcher.py   start the launcher with this
  rommlauncher/      the launcher's code
  config.json     settings (RomM or not, RomM address, username, RetroArch paths, cores)
  state.json      what was last synced, per game
  library.json    your library, cached for offline play
  backups/        replaced saves
  roms/           downloaded games (RomM has the real copies); without RomM, your games
  covers/         cover art for the window
  soh/            Ship of Harkinian, if the launcher downloaded it (with its settings and saves)
  2ship/          2 Ship 2 Harkinian, likewise
  sm64coopdx/     sm64coopdx, likewise (with its baserom.us.z64)
```

You can copy the whole folder to another computer, including between Windows and Linux.
`config.json` keeps RetroArch's paths in a section per system, so one file works on both:

```json
{
  "romm_url": "https://romm.example.com",
  "username": "me",
  "roms_dir": "roms",
  "platforms": { "...": "..." },
  "windows": { "retroarch": "C:\\RetroArch-Win64\\retroarch.exe", "cores_dir": "...", "saves_dir": "...",
               "soh": "C:\\Games\\SoH\\soh.exe" },
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
- One save per game: save states and multiple save slots aren't synced (the PC ports'
  three file slots are synced together, as one save).
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
| `romm.py` | The RomM API, with its safety lock (reads, save uploads, new ROMs only) |
| `saves.py` | Save files, 3DS save folders, backups, deciding which save is newer |
| `ports.py` | PC ports that play a game instead of RetroArch: Ship of Harkinian, 2 Ship 2 Harkinian, sm64coopdx |
| `games.py` | A game from the library: ROM and save locations, search, covers, facts |
| `launcher.py` | Logging in, the library, and playing a game (download, sync, run, upload) |
| `gui.py` | The window (`--gui`) |
| `cli.py` | The command line |

You can also start it with `python3 -m rommlauncher`. To run the tests:

```bash
python3 tests/test_launcher.py
```

The tests run the launcher against a fake RomM server in a temporary folder. On Windows,
set `PYTHONIOENCODING=utf-8` first. Starting RetroArch or the PC ports for real isn't
covered by the tests.

## License

[MIT](LICENSE)
