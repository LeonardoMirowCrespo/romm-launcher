"""Tests for romm_launcher.py against a fake RomM server (stdlib only).

Run:  python3 tests/test_launcher.py
"""

import base64
import json
import os
import sys
import tempfile
import threading
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import romm_launcher as L  # noqa: E402

ROM_BYTES = bytes(range(256)) * 4096  # 1 MiB
OOT = "Legend of Zelda, The - Ocarina of Time (U) (V1.2) [!].z64"
ROMS = [
    {"id": 7, "name": "The Legend of Zelda: Ocarina of Time", "fs_name": OOT,
     "fs_size_bytes": len(ROM_BYTES), "platform_fs_slug": "n64",
     "platform_display_name": "Nintendo 64", "has_multiple_files": False},
    {"id": 8, "name": "Super Mario 64", "fs_name": "Super Mario 64 (U).z64",
     "platform_fs_slug": "n64", "platform_display_name": "Nintendo 64"},
    {"id": 9, "name": "Pokemon Emerald", "fs_name": "Pokemon - Emerald (U).gba",
     "platform_fs_slug": "gba", "platform_display_name": "Game Boy Advance"},
]
STORE = {"saves": [], "files": {}}
AUTH = "Basic " + base64.b64encode(b"leo:pw").decode()
FLAGS = {"old": False, "csrf": False, "fail_upload": False}
SEEN = {"rom_downloads": 0, "token": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def authed(self):
        return self.headers.get("Authorization") in (AUTH, "Bearer tok")

    def do_GET(self):
        if not self.authed():
            return self.send(401, {"detail": "no"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/users/me":
            return self.send(200, {"username": "leo"})
        if u.path == "/api/roms":
            assert q["limit"] == ["10000"], q
            return self.send(200, {"items": ROMS, "total": len(ROMS)})
        if u.path == "/api/roms/7/content/" + OOT.replace(" ", "%20") or unquote(u.path) == f"/api/roms/7/content/{OOT}":
            SEEN["rom_downloads"] += 1
            return self.send(200, ROM_BYTES, "application/octet-stream")
        if u.path == "/api/saves":
            if FLAGS["old"]:
                return self.send(405, {"detail": "x"})
            return self.send(200, [s for s in STORE["saves"] if str(s["rom_id"]) == q["rom_id"][0]])
        if u.path == "/api/roms/7":
            return self.send(200, {"id": 7, "user_saves": STORE["saves"]})
        if u.path.startswith("/api/saves/") and u.path.endswith("/content"):
            if FLAGS["old"]:
                return self.send(404, {"detail": "x"})
            return self.send(200, STORE["files"][int(u.path.split("/")[3])], "application/octet-stream")
        if u.path.startswith("/api/raw/"):
            return self.send(200, STORE["files"][int(u.path.split("/")[-1])], "application/octet-stream")
        self.send(404, {"detail": "nf"})

    def do_POST(self):
        u = urlparse(self.path)
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if u.path == "/api/token":
            form = parse_qs(raw.decode())
            assert form["username"] == ["leo"] and form["password"] == ["pw"], form
            SEEN["token"] += 1
            return self.send(200, {"access_token": "tok", "token_type": "bearer"})
        if not self.authed():
            return self.send(401, {})
        if FLAGS["csrf"] and self.headers["Authorization"].startswith("Basic"):
            return self.send(403, {"detail": "CSRF token verification failed"})
        if FLAGS["fail_upload"]:
            return self.send(500, {"detail": "disk full"})
        q = parse_qs(u.query)
        assert u.path == "/api/saves" and q["emulator"][0] in ("mupen64plus_next", "citra"), self.path
        msg = BytesParser(policy=HTTP).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + raw
        )
        part = [p for p in msg.iter_parts() if p.get_param("name", header="content-disposition") == "saveFile"][0]
        sid = len(STORE["saves"]) + 1
        STORE["saves"].append({
            "id": sid, "rom_id": int(q["rom_id"][0]), "file_name": part.get_filename(),
            "file_extension": part.get_filename().rsplit(".", 1)[-1],
            "updated_at": f"2026-11-14T12:00:{sid:02d}Z", "download_path": f"/api/raw/x/{sid}",
            "missing_from_fs": False, "emulator": "mupen64plus_next",
        })
        STORE["files"][sid] = part.get_payload(decode=True)
        self.send(200, STORE["saves"][-1])


def check(name, cond):
    if not cond:
        raise SystemExit(f"FAILED: {name}")
    print(f"ok  {name}")


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}/"

# ---- API client -----------------------------------------------------------
c = L.RommClient(base, "leo", "pw")
check("login", c.check_login() == "leo")
try:
    L.RommClient(base, "leo", "bad").check_login()
    raise SystemExit("bad password accepted")
except L.LoginError as e:
    check("bad password -> LoginError", "password" in str(e))
try:
    L.RommClient("http://127.0.0.1:1", "a", "b", timeout=2).check_login()
    raise SystemExit("no error")
except L.RommError as e:
    check("unreachable server -> clear error", "Can't reach" in str(e))
check("list roms (paged response)", [r["id"] for r in c.list_roms()] == [7, 8, 9])

# ---- decide() -------------------------------------------------------------
D = L.decide
check("decide: nothing anywhere", D(None, None, None) == "none")
check("decide: only local", D("l", None, None) == "keep")
check("decide: only remote", D(None, "r", None) == "download")
check("decide: identical", D("x", "x", "old") == "none")
check("decide: remote newer", D("l", "r", "l") == "download")
check("decide: local newer", D("l", "r", "r") == "keep")
check("decide: both changed", D("l", "r", "z") == "conflict")
check("decide: never synced, both differ", D("l", "r", None) == "conflict")

# ---- Everything below works in a throwaway home folder -----------------------
tmp = Path(tempfile.mkdtemp())
L.CONFIG_FILE = tmp / "config" / "config.json"
L.STATE_FILE = tmp / "data" / "state.json"
L.LIBRARY_CACHE = tmp / "data" / "library.json"
L.BACKUP_DIR = tmp / "data" / "backups"
ra = tmp / "RetroArch"
(ra / "cores").mkdir(parents=True)
(ra / "cores" / "mupen64plus_next_libretro.so").write_bytes(b"core")
cfg = L.default_config()
cfg.update(romm_url=base, username="leo", roms_dir=str(tmp / "Roms"),
           retroarch=str(ra / "retroarch"), cores_dir=str(ra / "cores"), saves_dir=str(ra / "saves"))
L.save_json(L.CONFIG_FILE, cfg)

app = L.Launcher()
app.client = c
games = app.library()
oot = games[0]
check("library cached for offline use", L.LIBRARY_CACHE.exists())
check("search by words", L.filter_games(games, "zelda ocarina") == [oot])
check("search by platform", len(L.filter_games(games, "n64")) == 2)
check("ROM goes to Roms/<platform>/", oot.rom_dest == tmp / "Roms" / "n64" / OOT)
save_dir = ra / "saves" / "Mupen64Plus-Next"
check("save folder sorted by core", oot.save_dir() == save_dir)
check("save named after ROM", oot.save_path(oot.rom_dest).name == OOT.replace(".z64", ".srm"))

# A ROM copied by hand somewhere under ~/Documents/Roms is found, not re-downloaded.
(tmp / "Roms").mkdir()
(tmp / "Roms" / OOT).write_bytes(b"x")
check("finds ROM anywhere under Roms", oot.find_local_rom() == tmp / "Roms" / OOT)
(tmp / "Roms" / OOT).unlink()
check("missing ROM detected", oot.find_local_rom() is None)

# Full flow without launching: download ROM, RomM has a save -> it lands in RetroArch.
c.upload_save(7, OOT.replace(".z64", ".srm"), b"A" * 296960, "mupen64plus_next")
c.upload_save(7, OOT.replace(".z64", ".srm"), b"B" * 296960, "mupen64plus_next")
app.play(oot, launch=False)
local_save = save_dir / OOT.replace(".z64", ".srm")
check("ROM downloaded", (tmp / "Roms" / "n64" / OOT).read_bytes() == ROM_BYTES)
check("no leftover .part file", not list((tmp / "Roms" / "n64").glob("*.part")))
check("newest RomM save placed", local_save.read_bytes() == b"B" * 296960)
check("sync recorded", app.state["7"]["hash"] == L.sha256(b"B" * 296960))

# Second time: ROM not downloaded again, save already up to date.
app.play(oot, launch=False)
check("ROM not downloaded twice", SEEN["rom_downloads"] == 1)

# Local save changed (played), RomM unchanged -> keep local, upload.
local_save.write_bytes(b"C" * 296960)
check("local newer is kept", app.sync_before_play(oot, L.FileSave(local_save)) == L.sha256(b"B" * 296960)
      and local_save.read_bytes() == b"C" * 296960)
check("upload works", app.upload(oot, L.FileSave(local_save)))
check("RomM now has it", L.fetch_remote(c, oot, L.FileSave(local_save)).data == b"C" * 296960)

# RomM changed elsewhere (e.g. EmulatorJS) -> download, old local save backed up.
c.upload_save(7, "other-name.srm", b"D" * 296960, "mupen64plus_next")
app.sync_before_play(oot, L.FileSave(local_save))
check("remote newer is downloaded", local_save.read_bytes() == b"D" * 296960)
backups = list((L.BACKUP_DIR / "7").iterdir())
check("replaced save was backed up", any(b.read_bytes() == b"C" * 296960 for b in backups))

# Failed upload: loud, local save untouched, retried next start.
local_save.write_bytes(b"E" * 296960)
FLAGS["fail_upload"] = True
check("failed upload reports failure", app.upload(oot, L.FileSave(local_save)) is False)
check("local save untouched", local_save.read_bytes() == b"E" * 296960)
check("marked pending", app.state["7"]["pending_upload"] is True)
FLAGS["fail_upload"] = False
app.retry_pending(games)
check("pending upload retried", L.fetch_remote(c, oot, L.FileSave(local_save)).data == b"E" * 296960
      and app.state["7"]["pending_upload"] is False)

# RomM that refuses Basic auth on writes (CSRF) -> switch to a token.
FLAGS["csrf"] = True
c2 = L.RommClient(base, "leo", "pw")
c2.upload_save(7, "x.srm", b"F", "mupen64plus_next")
check("CSRF -> token fallback", SEEN["token"] == 1 and c2.auth == "Bearer tok")
FLAGS["csrf"] = False

# Older RomM without /api/saves listing or /content download.
FLAGS["old"] = True
check("old RomM fallbacks", L.fetch_remote(c, oot, L.FileSave(local_save)).data == b"F")
FLAGS["old"] = False

# Platforms
emerald = [g for g in games if g.id == 9][0]
check("GBA uses mGBA folder", emerald.save_dir() == ra / "saves" / "mGBA")
cfg3 = L.load_config()
check("3DS saves are synced", L.Game({"fs_name": "a.3ds", "platform_fs_slug": "3ds", "id": 1}, cfg3).syncs_saves)

# Cores: config names are OS-neutral, files are .dll on Windows and .so on Linux.
check("core_id strips any extension", L.core_id("citra_libretro.dll") == L.core_id("citra_libretro.so") == "citra")
(ra / "cores" / f"citra_libretro{L.CORE_EXT}").write_bytes(b"core")
check("find_core by name", L.find_core(ra / "cores", "citra") == ra / "cores" / f"citra_libretro{L.CORE_EXT}")
check("find_core accepts old file-name config", L.find_core(ra / "cores", f"citra_libretro{L.CORE_EXT}") is not None)
check("find_core missing", L.find_core(ra / "cores", "mgba") is None)

# RetroArch's own retroarch.cfg decides where saves go.
(ra / "retroarch.cfg").write_text(
    'savefile_directory = ":\\saves"\nsort_savefiles_enable = "false"\n', encoding="utf-8"
)
check("':' paths are relative to RetroArch", L.read_retroarch_cfg(str(ra / "retroarch"))["savefile_directory"] == str(ra / "saves"))
rom_file = tmp / "Roms" / "n64" / OOT
check("sorting off -> plain saves folder", oot.save_path(rom_file).parent == ra / "saves")
(ra / "retroarch.cfg").write_text(
    'sort_savefiles_enable = "true"\nsort_savefiles_by_content_enable = "true"\n', encoding="utf-8"
)
check("sort by core + content dir", oot.save_path(rom_file).parent == save_dir / "n64")
(ra / "retroarch.cfg").write_text('savefiles_in_content_dir = "true"\n', encoding="utf-8")
check("saves next to the ROM", oot.save_path(rom_file).parent == rom_file.parent)
(ra / "retroarch.cfg").unlink()

# ---- 3DS: Citra's save folder is synced as one zip ------------------------------
rom3 = tmp / "elsewhere" / "Zelda OoT3D.3ds"
rom3.parent.mkdir()
header = bytearray(0x200)
header[0x100:0x104] = b"NCSD"
header[0x108:0x110] = (0x0004000000033500).to_bytes(8, "little")
rom3.write_bytes(bytes(header))
check("title ID from .3ds header", L.title_id_from_rom(rom3) == "0004000000033500")
z3 = L.Game({"id": 11, "fs_name": rom3.name, "platform_fs_slug": "3ds",
             "save_target": "00040000/00033500"}, L.load_config())
s3 = z3.save(rom3)
data_dir = (ra / "saves" / "Citra" / "Citra" / "sdmc" / "Nintendo 3DS" / ("0" * 32) / ("0" * 32)
            / "title" / "00040000" / "00033500" / "data")
check("3DS save is Citra's data folder", isinstance(s3, L.FolderSave) and s3.path == data_dir)
check("same folder from ROM header alone",
      L.Game(dict(z3.rom, save_target=None), z3.config).save(rom3).path == data_dir)
check("3DS uploads as <rom>.zip", s3.upload_name == "Zelda OoT3D.zip")
check("no save yet -> no hash", s3.hash() is None)

(data_dir / "00000001").mkdir(parents=True)
(data_dir / "00000001" / "save00.bin").write_bytes(b"link" * 1000)
(data_dir / "00000001" / "system.dat").write_bytes(b"sys")
(data_dir / "00000001.metadata").write_bytes(b"meta")
check("zip is reproducible", s3.read() == s3.read())
check("folder hash == zip hash", s3.hash() == s3.hash_of(s3.read()))
check("3DS upload", app.upload(z3, s3))
remote3 = L.fetch_remote(c, z3, s3)
check("RomM has the zip, same hash", remote3.info["file_name"] == "Zelda OoT3D.zip" and remote3.hash == s3.hash())
check("unchanged 3DS save is up to date", app.sync_before_play(z3, s3) == s3.hash()
      and app.state["11"]["hash"] == s3.hash())

# Another device saved further: new save00.bin, system.dat removed.
other = L.FolderSave(tmp / "other-device" / "data", "Zelda OoT3D.zip")
(other.path / "00000001").mkdir(parents=True)
(other.path / "00000001" / "save00.bin").write_bytes(b"ganon" * 1000)
(other.path / "00000001.metadata").write_bytes(b"meta")
c.upload_save(11, "Zelda OoT3D.zip", other.read(), "citra")
old_hash = s3.hash()
app.sync_before_play(z3, s3)
check("newer 3DS save unpacked", (data_dir / "00000001" / "save00.bin").read_bytes() == b"ganon" * 1000)
check("files gone from the new save are gone", not (data_dir / "00000001" / "system.dat").exists())
check("no leftover temp folders", sorted(p.name for p in data_dir.parent.iterdir()) == ["data"])
zips = [b for b in (L.BACKUP_DIR / "11").iterdir() if b.suffix == ".zip"]
check("old 3DS save backed up as zip", any(s3.hash_of(b.read_bytes()) == old_hash for b in zips))

# Played: local changes -> uploaded after play (same path as play() takes).
(data_dir / "00000001" / "save00.bin").write_bytes(b"triforce" * 1000)
check("3DS local newer kept", app.sync_before_play(z3, s3) != s3.hash()
      and (data_dir / "00000001" / "save00.bin").read_bytes() == b"triforce" * 1000)
app.upload(z3, s3)
check("3DS RomM updated", L.fetch_remote(c, z3, s3).hash == s3.hash())

# A malicious or broken zip is refused and the local save stays.
import io, zipfile
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("../../evil.txt", "x")
try:
    s3.write(buf.getvalue())
    raise SystemExit("unsafe zip accepted")
except L.RommError:
    check("unsafe zip refused, save intact", (data_dir / "00000001" / "save00.bin").read_bytes() == b"triforce" * 1000)
try:
    s3.hash_of(b"not a zip")
    raise SystemExit("bad zip accepted")
except L.RommError:
    check("broken zip reported", True)

# Old config that switched 3DS sync off is migrated.
L.save_json(L.CONFIG_FILE, dict(cfg, platforms={"3ds": {"core": "citra", "save_exts": [], "sync_saves": False}}))
check("old 3DS config migrated", L.load_config()["platforms"]["3ds"]["save_layout"] == "citra"
      and L.Game({"id": 1, "fs_name": "a.3ds", "platform_fs_slug": "3ds"}, L.load_config()).syncs_saves)
L.save_json(L.CONFIG_FILE, cfg)

# One config.json for Windows and Linux: RetroArch paths live in a section per system.
check("guess_os windows", L.guess_os({"retroarch": "C:\\RetroArch-Win64\\retroarch.exe"}) == "windows")
check("guess_os linux", L.guess_os({"retroarch": "~/.steam/x/RetroArch/retroarch"}) == "linux")
check("guess_os macos", L.guess_os({"retroarch": "/Applications/RetroArch.app/Contents/MacOS/RetroArch"}) == "macos")
saved_cfg_file, saved_os = L.CONFIG_FILE, L.OS_KEY
L.CONFIG_FILE = tmp / "multi" / "config.json"
win_paths = {"retroarch": "C:\\RetroArch-Win64\\retroarch.exe", "cores_dir": "C:\\RetroArch-Win64\\cores",
             "saves_dir": "C:\\RetroArch-Win64\\saves", "sort_saves_by_core": True}
L.save_json(L.CONFIG_FILE, {"romm_url": base, "username": "leo", "roms_dir": "roms", **win_paths})

L.OS_KEY = "linux"  # the Windows config copied to a Linux computer
lc = L.load_config()
check("other system's flat paths not used", lc["retroarch"] == str(L.DEFAULT_RETROARCH) and "linux" not in lc)
check("flat paths read as their system's section", lc["windows"] == win_paths)
check("shared settings kept", lc["username"] == "leo" and lc["romm_url"] == base)
check("RetroArch setup needed on the new system", L.Launcher().needs_retroarch_setup())
L.migrate_flat_config()
on_disk = L.load_json(L.CONFIG_FILE, {})
check("flat config rewritten with sections", on_disk.get("windows") == win_paths
      and "retroarch" not in on_disk and "linux" not in on_disk and on_disk["username"] == "leo")
L.migrate_flat_config()
check("rewrite only once", L.load_json(L.CONFIG_FILE, {}) == on_disk)

# Setting up RetroArch on Linux adds a "linux" section and leaves "windows" alone.
lin = L.Launcher()
lin.config.setdefault("linux", {})
lin.config.update(retroarch="/opt/ra/retroarch", cores_dir="/opt/ra/cores", saves_dir="/opt/ra/saves")
lin.save_config()
on_disk = L.load_json(L.CONFIG_FILE, {})
check("both sections on disk", on_disk["linux"]["retroarch"] == "/opt/ra/retroarch"
      and on_disk["windows"] == win_paths and "retroarch" not in on_disk)
check("Linux uses its section", L.load_config()["saves_dir"] == "/opt/ra/saves"
      and not L.Launcher().needs_retroarch_setup())
L.OS_KEY = "windows"
wc = L.load_config()
check("Windows uses its section", wc["retroarch"] == win_paths["retroarch"] and wc["saves_dir"] == win_paths["saves_dir"])
L.Launcher().save_config()
check("saving on Windows keeps the Linux section", L.load_json(L.CONFIG_FILE, {})["linux"]["retroarch"] == "/opt/ra/retroarch")
check("per-system ROM folder", L.roms_root({"roms_dir": "roms", "windows": {"roms_dir": str(tmp / "W")}}) == tmp / "W"
      and L.roms_root({"roms_dir": "roms", "linux": {"roms_dir": "/x"}}) == L.APP_HOME / "roms")
L.CONFIG_FILE, L.OS_KEY = saved_cfg_file, saved_os
check("flat test config still read on this system", L.load_config()["retroarch"] == str(ra / "retroarch"))

# Moving everything into one launcher folder (APP_HOME) from older locations.
mig = tmp / "migration"
old_cfg, old_data = mig / "AppData" / "Roaming" / "romm-launcher", mig / "AppData" / "Local" / "romm-launcher"
saved = (L.APP_HOME, L.CONFIG_FILE, L.OLD_LOCATIONS, L.OLD_ROMS_DIRS)


def use_home(home):
    L.APP_HOME, L.CONFIG_FILE = home, home / "config.json"
    L.OLD_LOCATIONS = [old_cfg, old_data]
    L.OLD_ROMS_DIRS = [mig / "Documents" / "Roms", old_cfg / "roms", old_data / "roms"]


home = mig / "Workspace" / "RomM"
use_home(home)
L.save_json(old_cfg / "config.json", {"username": "leo", "roms_dir": str(old_data / "roms")})
(old_cfg / "config.steam-backup.json").write_text("{}")
L.save_json(old_data / "state.json", {"7": {"hash": "h"}})
(old_data / "backups" / "7").mkdir(parents=True)
(old_data / "backups" / "7" / "b.zip").write_bytes(b"backup")
(old_data / "roms" / "3ds").mkdir(parents=True)
(old_data / "roms" / "3ds" / "game.3ds").write_bytes(b"rom")
L.migrate_legacy_dirs()
check("settings moved into home", L.load_json(home / "config.json", {})["username"] == "leo"
      and (home / "config.steam-backup.json").exists())
check("state + backups moved", L.load_json(home / "state.json", {})["7"]["hash"] == "h"
      and (home / "backups" / "7" / "b.zip").read_bytes() == b"backup")
check("ROMs moved into home", (home / "roms" / "3ds" / "game.3ds").read_bytes() == b"rom")
check("config points at home/roms (relative)", L.load_json(home / "config.json", {})["roms_dir"] == "roms"
      and L.roms_root(L.load_json(home / "config.json", {})) == home / "roms")
check("old folders removed", not old_cfg.exists() and not old_data.exists())
L.migrate_legacy_dirs()
check("migration runs once", (home / "roms" / "3ds" / "game.3ds").exists())

# Never overwrite what's already in home; leave the old copy instead.
L.save_json(old_data / "state.json", {"old": True})
L.migrate_legacy_dirs()
check("existing files never overwritten", L.load_json(home / "state.json", {})["7"]["hash"] == "h"
      and (old_data / "state.json").exists())
(old_data / "state.json").unlink()
old_data.rmdir()

# Very old versions: ~/Documents/Roms.
home2 = mig / "Home2"
use_home(home2)
docs = mig / "Documents" / "Roms"
(docs / "n64").mkdir(parents=True)
(docs / "n64" / "z.z64").write_bytes(b"z")
L.save_json(home2 / "config.json", {"username": "leo", "roms_dir": str(docs)})
L.migrate_legacy_dirs()
check("Documents/Roms moved", (home2 / "roms" / "n64" / "z.z64").exists() and not docs.exists()
      and L.load_json(home2 / "config.json", {})["roms_dir"] == "roms")
L.save_json(home2 / "config.json", {"username": "leo", "roms_dir": str(mig / "MyRoms")})
L.migrate_legacy_dirs()
check("a chosen ROM folder is left alone", L.load_json(home2 / "config.json", {})["roms_dir"] == str(mig / "MyRoms"))
L.APP_HOME, L.CONFIG_FILE, L.OLD_LOCATIONS, L.OLD_ROMS_DIRS = saved

# By default everything lives next to the script, and "roms" is relative to it.
import os
if not os.environ.get(L.HOME_ENV):
    check("launcher folder is the script's folder", L.APP_HOME == Path(L.__file__).resolve().parent)
check("default ROM folder inside launcher folder", L.roms_root(L.default_config()) == L.APP_HOME / "roms")
check("absolute ROM folder kept", L.roms_root({"roms_dir": str(tmp / "X")}) == tmp / "X")

# Offline: only games on this computer are listed.
off = L.Launcher()
off.offline = True
check("offline lists local games only", [g.id for g in off.library()] == [7])

print("\nall tests passed")
