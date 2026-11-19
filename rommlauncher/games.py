"""Games from the RomM library (Game): where their ROM and save go on this computer,
searching and filtering the library, and cover art for the window. Without RomM, the
library is the ROM folder itself (local_roms).
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .config import roms_root
from .retroarch import core_save_folder, read_retroarch_cfg
from .saves import FileSave, FolderSave, citra_save_folder


@dataclass
class Game:
    rom: dict  # RomM's ROM record (or one made by local_roms, without RomM)
    config: dict

    @property
    def id(self) -> int | str:
        """RomM's ROM id; "local-<hash>" for a game found in the ROM folder without RomM."""
        rom_id = self.rom["id"]
        return rom_id if isinstance(rom_id, str) else int(rom_id)

    @property
    def name(self) -> str:
        return self.rom.get("name") or self.rom.get("fs_name_no_ext") or self.rom["fs_name"]

    @property
    def platform(self) -> str:
        return self.rom.get("platform_fs_slug") or self.rom.get("platform_slug") or "other"

    @property
    def platform_name(self) -> str:
        return self.rom.get("platform_display_name") or self.platform.upper()

    @property
    def settings(self) -> dict:
        return self.config["platforms"].get(self.platform, {})

    @property
    def core(self) -> str | None:
        return self.settings.get("core")

    @property
    def syncs_saves(self) -> bool:
        return self.settings.get("sync_saves", True) and bool(self.save_exts)

    @property
    def save_exts(self) -> list[str]:
        return self.settings.get("save_exts", ["srm"])

    @property
    def multi_file(self) -> bool:
        return bool(self.rom.get("has_multiple_files"))

    @property
    def rom_dest(self) -> Path:
        """Where the ROM is downloaded to: <ROM folder>/<platform>/<file>."""
        return roms_root(self.config) / self.platform / self.rom["fs_name"]

    def find_local_rom(self, index: dict[str, Path] | None = None) -> Path | None:
        """The playable ROM file on this computer, or None.

        Looks in the platform folder first, then anywhere under the ROMs folder,
        so ROMs you copied there yourself are found too."""
        if self.rom.get("local_path"):  # found in the ROM folder (no RomM): exactly there
            path = roms_root(self.config) / self.rom["local_path"]
            if path.is_dir():
                return pick_launch_file(path)
            return path if path.is_file() else None
        dest = self.rom_dest
        if self.multi_file:
            return pick_launch_file(dest) if dest.is_dir() else None
        if dest.is_file():
            return dest
        if index is None:
            index = index_roms(roms_root(self.config))
        return index.get(self.rom["fs_name"].lower())

    def save_dir(self, rom_file: Path | None = None) -> Path:
        """Where RetroArch keeps this game's save, following its "Saving" settings:
        Sort Saves into Folders by Core Name / by Content Directory, saves next to content."""
        ra = read_retroarch_cfg(self.config["retroarch"])
        if rom_file and ra.get("savefiles_in_content_dir") == "true":
            return rom_file.parent
        folder = Path(self.config["saves_dir"]).expanduser()
        by_core = ra.get("sort_savefiles_enable")
        by_core = by_core == "true" if by_core else self.config.get("sort_saves_by_core", True)
        if by_core and self.core:
            folder = Path(core_save_folder(
                str(folder), self.config["cores_dir"], self.core, ra.get("libretro_info_path")
            ))
        if rom_file and ra.get("sort_savefiles_by_content_enable") == "true":
            folder = folder / rom_file.parent.name
        return folder

    def save(self, rom_file: Path) -> "FileSave | FolderSave | None":
        """This game's save on this computer, or None if it can't be synced."""
        if not self.syncs_saves:
            return None
        if self.settings.get("save_layout") == "citra":
            folder = citra_save_folder(self, rom_file)
            return FolderSave(folder, f"{rom_file.stem}.zip") if folder else None
        return FileSave(self.save_path(rom_file))

    def save_path(self, rom_file: Path) -> Path:
        """The save RetroArch uses: an existing one with any known extension,
        otherwise <ROM name>.<first extension>."""
        folder = self.save_dir(rom_file)
        for ext in self.save_exts:
            candidate = folder / f"{rom_file.stem}.{ext}"
            if candidate.exists():
                return candidate
        return folder / f"{rom_file.stem}.{(self.save_exts or ['srm'])[0]}"


def index_roms(roms_dir: Path) -> dict[str, Path]:
    """Every file under the ROMs folder, by lower-case file name."""
    index: dict[str, Path] = {}
    if roms_dir.is_dir():
        for p in roms_dir.rglob("*"):
            if p.is_file() and not p.name.endswith(".part"):
                index.setdefault(p.name.lower(), p)
    return index


def pick_launch_file(folder: Path) -> Path | None:
    """For a multi-file game: the .m3u playlist if there is one, else the largest file."""
    files = [p for p in folder.rglob("*") if p.is_file()]
    playlists = [p for p in files if p.suffix.lower() == ".m3u"]
    if playlists:
        return playlists[0]
    return max(files, key=lambda p: p.stat().st_size, default=None)


def unpack_zip(zip_path: Path, folder: Path) -> None:
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(folder)
    zip_path.unlink()


def filter_games(games: list[Game], query: str) -> list[Game]:
    """Games whose name or file name contains every word of the query."""
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    out = []
    for g in games:
        hay = " ".join(
            [g.name, g.rom.get("fs_name", ""), g.platform, g.platform_name]
            + list(g.rom.get("alternative_names") or [])
        ).lower()
        if all(w in hay for w in words):
            out.append(g)
    return out


# ----- without RomM: the games in the ROM folder -----------------------------------

PLATFORM_NAMES = {  # platform folder (RomM's fs_slug) → name, for games found without RomM
    "n64": "Nintendo 64", "gba": "Game Boy Advance", "nds": "Nintendo DS", "nintendo-dsi": "Nintendo DSi",
    "3ds": "Nintendo 3DS", "new-nintendo-3ds": "New Nintendo 3DS", "nes": "Nintendo Entertainment System",
    "famicom": "Famicom", "snes": "Super Nintendo", "sfam": "Super Famicom", "gb": "Game Boy",
    "gbc": "Game Boy Color", "virtualboy": "Virtual Boy", "genesis": "Sega Genesis / Mega Drive",
    "sms": "Sega Master System", "gamegear": "Sega Game Gear", "sg1000": "Sega SG-1000", "sega32": "Sega 32X",
    "tg16": "TurboGrafx-16 / PC Engine", "wonderswan": "WonderSwan", "wonderswan-color": "WonderSwan Color",
    "neo-geo-pocket": "Neo Geo Pocket", "neo-geo-pocket-color": "Neo Geo Pocket Color",
    "psx": "PlayStation", "segacd": "Sega CD", "turbografx-cd": "TurboGrafx-CD", "pokemon-mini": "Pokémon mini",
    "atari2600": "Atari 2600", "atari7800": "Atari 7800", "lynx": "Atari Lynx", "psp": "PlayStation Portable",
    "dc": "Dreamcast", "ngc": "GameCube", "wii": "Wii", "saturn": "Sega Saturn", "arcade": "Arcade",
    "neogeoaes": "Neo Geo AES", "neogeomvs": "Neo Geo MVS",
}
# Files in the ROM folder that aren't games.
NOT_GAMES = {".txt", ".nfo", ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".srm", ".sav", ".state", ".bak",
             ".ini", ".cfg", ".opt", ".json", ".xml", ".dat", ".db", ".sfv", ".md5", ".sha1", ".part", ".tmp"}
# Text files that name the other files of a game (a .cue names its .bin tracks).
LISTS = {".cue", ".gdi", ".m3u", ".ccd"}


def platform_name(slug: str) -> str:
    return PLATFORM_NAMES.get(slug, slug.upper())


def local_roms(roms_dir: Path, config: dict) -> list[dict]:
    """Without RomM, the library is the ROM folder: <ROM folder>/<platform>/<game file>,
    named after RomM's platform folders (n64, gba, snes, ...; see DEFAULT_PLATFORMS). A
    folder inside a platform folder is one game of several files. A platform folder with
    another name (e.g. "Nintendo 64") and a file straight in the ROM folder get their
    platform from the file's extension. Returns ROM records like RomM's, with
    "local_path" (relative to the ROM folder) and a stable "local-<hash>" id."""
    out: list[dict] = []
    if not roms_dir.is_dir():
        return out
    known = config.get("platforms", {})
    for entry in sorted(roms_dir.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        folder = entry.name.lower()
        for item in game_entries(entry):
            guess = guess_platform(item.name) if item.is_file() else None
            slug = folder if folder in known else guess or folder
            out.append(local_rom(roms_dir, item, slug))
    for item in game_entries(roms_dir, folders=False):
        if guess_platform(item.name):
            out.append(local_rom(roms_dir, item, guess_platform(item.name)))
    return out


def game_entries(folder: Path, folders: bool = True) -> list[Path]:
    """The games in one folder: files that look like games, minus the ones a .cue or .m3u
    names (they belong to that game), plus subfolders (games of several files)."""
    items = sorted((p for p in folder.iterdir() if not p.name.startswith(".")), key=lambda p: p.name.lower())
    files = [p for p in items if p.is_file() and p.suffix.lower() not in NOT_GAMES]
    named: set[str] = set()
    for p in files:
        if p.suffix.lower() in LISTS and p.stat().st_size < 256 * 1024:
            text = p.read_text(encoding="utf-8", errors="replace").lower()
            named.update(q.name.lower() for q in files if q is not p and q.name.lower() in text)
    games = [p for p in files if p.name.lower() not in named]
    if folders:
        games += [p for p in items if p.is_dir()]
    return games


def local_rom(roms_dir: Path, path: Path, slug: str) -> dict:
    rel = path.relative_to(roms_dir).as_posix()
    name = path.name
    if path.is_file():
        name = path.stem
        if path.suffix.lower() in (".zip", ".7z") and Path(name).suffix.lower() in ROM_PLATFORMS:
            name = Path(name).stem  # "Game.z64.zip" -> "Game"
    size = path.stat().st_size if path.is_file() else sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return {
        "id": "local-" + hashlib.sha1(rel.lower().encode("utf-8")).hexdigest()[:12],
        "name": name,
        "fs_name": path.name,
        "fs_size_bytes": size,
        "platform_fs_slug": slug,
        "platform_display_name": platform_name(slug),
        "has_multiple_files": path.is_dir(),
        "local_path": rel,
    }


# ----- for the GUI's library view (no Tk here, so it can be tested) ---------------

ALL_GAMES, LOCAL_GAMES = "all", "local"
# The two RomM statuses the launcher shows and sets (RomM's others are left to its web UI).
STATUS_NAMES = {"finished": "Finished", "completed_100": "100%"}


def library_filters(games: list[Game], local_ids: set, show_local: bool = True) -> list[tuple[str, str, int]]:
    """The sidebar: (key, label, number of games): all, on this computer (not without
    RomM, where every game is), then each platform."""
    platforms: dict[str, list[Game]] = {}
    for g in games:
        platforms.setdefault(g.platform, []).append(g)
    out = [(ALL_GAMES, "All games", len(games))]
    if show_local:
        out.append((LOCAL_GAMES, "On this computer", sum(g.id in local_ids for g in games)))
    for slug, members in sorted(platforms.items(), key=lambda kv: kv[1][0].platform_name.lower()):
        out.append((f"platform:{slug}", members[0].platform_name, len(members)))
    return out


def filter_library(games: list[Game], key: str, query: str, local_ids: set) -> list[Game]:
    """The games shown for a sidebar choice and a search, sorted by name."""
    if key == LOCAL_GAMES:
        games = [g for g in games if g.id in local_ids]
    elif key.startswith("platform:"):
        games = [g for g in games if g.platform == key.split(":", 1)[1]]
    if query.strip():
        games = filter_games(games, query)
    return sorted(games, key=lambda g: g.name.lower())


def local_game_ids(games: list[Game], roms_dir: Path) -> set:
    index = index_roms(roms_dir)
    return {g.id for g in games if g.find_local_rom(index)}


def cover_file(game: Game) -> Path:
    return paths.COVER_DIR / f"{game.id}.png"


def fetch_cover(client: "RommClient", game: Game) -> Path | None:
    """Download a game's cover from RomM (once; it's kept in COVER_DIR). Only PNG and GIF
    are kept, since that's what Tk can show without extra packages."""
    dest = cover_file(game)
    if dest.exists():
        return dest
    path = game.rom.get("path_cover_small") or game.rom.get("path_cover_large")
    if not path or not client:
        return None
    data = client.download_asset(path)
    if not (data.startswith(b"\x89PNG") or data.startswith(b"GIF8")):
        return None
    paths.COVER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


# ----- adding games and their details ----------------------------------------

ROM_PLATFORMS = {  # file extension → RomM platform (fs_slug), to suggest when adding a game
    ".nes": "nes", ".sfc": "snes", ".smc": "snes", ".vb": "virtualboy", ".min": "pokemon-mini",
    ".gb": "gb", ".gbc": "gbc", ".gba": "gba", ".nds": "nds", ".dsi": "nintendo-dsi",
    ".z64": "n64", ".n64": "n64", ".v64": "n64", ".ndd": "64dd",
    ".3ds": "3ds", ".cci": "3ds", ".cxi": "3ds", ".cia": "3ds",
    ".rvz": "ngc", ".gcm": "ngc", ".wbfs": "wii", ".wua": "wiiu", ".wux": "wiiu", ".nsp": "switch", ".xci": "switch",
    ".md": "genesis", ".gen": "genesis", ".smd": "genesis", ".32x": "sega32", ".sms": "sms", ".gg": "gamegear",
    ".sg": "sg1000", ".pce": "tg16", ".ws": "wonderswan", ".wsc": "wonderswan-color",
    ".a26": "atari2600", ".a52": "atari5200", ".a78": "atari7800", ".lnx": "lynx", ".j64": "jaguar",
    ".col": "colecovision", ".int": "intellivision", ".vec": "vectrex",
    ".cso": "psp", ".vpk": "psvita", ".d64": "c64", ".t64": "c64", ".adf": "amiga", ".tic": "tic-80",
}


def guess_platform(file_name: str) -> str | None:
    return ROM_PLATFORMS.get(Path(file_name).suffix.lower())


def game_facts(rom: dict) -> list[tuple[str, str]]:
    """Short facts about a game from RomM's metadata, for the details panel: (label, text)."""
    meta = rom.get("metadatum") or {}
    facts = []
    released = meta.get("first_release_date")
    if isinstance(released, (int, float)) and released > 0:
        facts.append(("Released", str(datetime.fromtimestamp(released / 1000, timezone.utc).year)))
    if meta.get("genres"):
        facts.append(("Genre", ", ".join(meta["genres"][:3])))
    rating = meta.get("average_rating")
    if isinstance(rating, (int, float)) and rating > 0:
        facts.append(("Rating", f"{round(rating)} / 100"))
    modes = [m for m in meta.get("game_modes") or [] if m]
    if modes:
        facts.append(("Modes", ", ".join(modes[:3])))
    if meta.get("franchises"):
        facts.append(("Series", ", ".join(meta["franchises"][:2])))
    if rom.get("regions"):
        facts.append(("Region", ", ".join(rom["regions"][:3])))
    return facts
