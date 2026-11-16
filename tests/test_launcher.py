"""Tests for romm_launcher.py against a fake RomM server (stdlib only).

Run:  python3 tests/test_launcher.py
"""

import base64
import io
import json
import zipfile
import os
import sys
import subprocess
import tempfile
import threading
from datetime import datetime
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from rommlauncher import (  # noqa: E402
    config,
    core_downloads,
    dualscreen,
    games,
    gui,
    launcher,
    paths,
    retroarch,
    romm,
    saves,
    system,
    ui,
    util,
)

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
FLAGS = {"old": False, "csrf": False, "fail_upload": False, "fail_save_download": False}
SEEN = {"rom_downloads": 0, "token": 0, "destructive": 0, "core_downloads": 0}


def make_png(w, h):
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\x30\x60\x90" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = make_png(300, 400)


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


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
        u = urlparse(self.path)
        if u.path.startswith("/nightly/"):  # stands in for libretro's buildbot
            SEEN["core_downloads"] += 1
            name = u.path.rsplit("/", 1)[-1]
            if name.startswith("nosuchcore"):
                return self.send(404, b"not found", "text/html")
            if name.startswith("badzip"):
                return self.send(200, make_zip({"other/file.dll": b"x"}), "application/zip")
            return self.send(200, make_zip({name[:-4]: b"CORE:" + name[:-4].encode()}), "application/zip")
        if u.path == "/assets/frontend/info.zip":
            return self.send(200, make_zip({
                "mgba_libretro.info": b'corename = "mGBA"\n',
                "snes9x_libretro.info": b'corename = "Snes9x"\n',
            }), "application/zip")
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
            if FLAGS["fail_save_download"]:
                return self.send(500, {"detail": "broken"})
            return self.send(200, STORE["files"][int(u.path.split("/")[3])], "application/octet-stream")
        if u.path.startswith("/api/raw/"):
            return self.send(200, STORE["files"][int(u.path.split("/")[-1])], "application/octet-stream")
        if u.path.startswith("/assets/romm/resources/"):
            SEEN["assets"] = SEEN.get("assets", 0) + 1
            return self.send(200, PNG if u.path.endswith(".png") else b"\xff\xd8\xffjpeg", "image/png")
        if u.path in ("/redirect-away", "/redirect-home"):
            self.send_response(302)
            host = "127.0.0.2" if u.path == "/redirect-away" else "127.0.0.1"
            self.send_header("Location", f"http://{host}:{self.server.server_port}/api/users/me")
            self.send_header("Content-Length", "0")
            return self.end_headers()
        self.send(404, {"detail": "nf"})

    def refuse_write(self):
        SEEN["destructive"] += 1  # the launcher must never get here
        self.send(405, {"detail": "not allowed"})

    do_DELETE = do_PUT = do_PATCH = refuse_write

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
c = romm.RommClient(base, "leo", "pw")
check("login", c.check_login() == "leo")
try:
    romm.RommClient(base, "leo", "bad").check_login()
    raise SystemExit("bad password accepted")
except romm.LoginError as e:
    check("bad password -> LoginError", "password" in str(e))
try:
    romm.RommClient("http://127.0.0.1:1", "a", "b", timeout=2).check_login()
    raise SystemExit("no error")
except romm.RommError as e:
    check("unreachable server -> clear error", "Can't reach" in str(e))
check("list roms (paged response)", [r["id"] for r in c.list_roms()] == [7, 8, 9])

# ---- The client can't delete or change anything on RomM -------------------
def blocked(name, method, path, params=None, client=c):
    try:
        client._request(method, path, params=params)
    except romm.RommError as e:
        return check(name, str(e).startswith("Blocked"))
    raise SystemExit(f"FAILED: {name} (request was sent)")


blocked("DELETE refused", "DELETE", "/api/saves/1")
blocked("PUT refused", "PUT", "/api/roms/7")
blocked("PATCH refused", "PATCH", "/api/users/me")
blocked("POST to other routes refused", "POST", "/api/roms/delete")
blocked("POST /api/saves/delete refused", "POST", "/api/saves/delete")
blocked("upload with autocleanup refused", "POST", "/api/saves", {"rom_id": 7, "emulator": "x", "autocleanup": "true"})
blocked("upload with overwrite refused", "POST", "/api/saves", {"rom_id": 7, "emulator": "x", "overwrite": "true"})
blocked("other servers refused", "GET", "http://127.0.0.2:1/api/users/me")
blocked("redirect to another server refused", "GET", "/redirect-away")
check("redirect within RomM followed", c._json("GET", "/redirect-home")["username"] == "leo")
check("nothing destructive reached the server", SEEN["destructive"] == 0)
sub = romm.RommClient("https://romm.example.com/romm/", "a", "b")
sub._check("POST", "https://romm.example.com/romm/api/saves?rom_id=1&emulator=mgba")
check("RomM under a sub-path: uploads allowed", True)
blocked("RomM under a sub-path: DELETE refused", "DELETE", "/api/saves/1", client=sub)

# ---- decide() -------------------------------------------------------------
D = saves.decide
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
paths.CONFIG_FILE = tmp / "config" / "config.json"
paths.STATE_FILE = tmp / "data" / "state.json"
paths.LIBRARY_CACHE = tmp / "data" / "library.json"
paths.BACKUP_DIR = tmp / "data" / "backups"
ra = tmp / "RetroArch"
(ra / "cores").mkdir(parents=True)
(ra / "cores" / "mupen64plus_next_libretro.so").write_bytes(b"core")
cfg = config.default_config()
cfg.update(romm_url=base, username="leo", roms_dir=str(tmp / "Roms"),
           retroarch=str(ra / "retroarch"), cores_dir=str(ra / "cores"), saves_dir=str(ra / "saves"))
util.save_json(paths.CONFIG_FILE, cfg)

app = launcher.Launcher()
app.client = c
library = app.library()
oot = library[0]
check("library cached for offline use", paths.LIBRARY_CACHE.exists())
check("search by words", games.filter_games(library, "zelda ocarina") == [oot])
check("search by platform", len(games.filter_games(library, "n64")) == 2)
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
check("sync recorded", app.state["7"]["hash"] == util.sha256(b"B" * 296960))

# Second time: ROM not downloaded again, save already up to date.
app.play(oot, launch=False)
check("ROM not downloaded twice", SEEN["rom_downloads"] == 1)

# Local save changed (played), RomM unchanged -> keep local, upload.
local_save.write_bytes(b"C" * 296960)
check("local newer is kept", app.sync_before_play(oot, saves.FileSave(local_save)) == util.sha256(b"B" * 296960)
      and local_save.read_bytes() == b"C" * 296960)
check("upload works", app.upload(oot, saves.FileSave(local_save)))
check("RomM now has it", saves.fetch_remote(c, oot, saves.FileSave(local_save)).data == b"C" * 296960)
romm_backups = list((paths.BACKUP_DIR / "7").glob("*romm-before-upload*"))
check("RomM's save backed up before it was replaced", any(b.read_bytes() == b"B" * 296960 for b in romm_backups))

# If RomM's current save can't be backed up, nothing is uploaded.
saves_before = len(STORE["saves"])
local_save.write_bytes(b"G" * 296960)
FLAGS["fail_save_download"] = True
check("no backup -> no upload", app.upload(oot, saves.FileSave(local_save)) is False
      and len(STORE["saves"]) == saves_before and app.state["7"]["pending_upload"] is True)
FLAGS["fail_save_download"] = False
local_save.write_bytes(b"C" * 296960)
app.mark_synced(oot, util.sha256(b"C" * 296960))

# RomM changed elsewhere (e.g. EmulatorJS) -> download, old local save backed up.
c.upload_save(7, "other-name.srm", b"D" * 296960, "mupen64plus_next")
app.sync_before_play(oot, saves.FileSave(local_save))
check("remote newer is downloaded", local_save.read_bytes() == b"D" * 296960)
backups = list((paths.BACKUP_DIR / "7").iterdir())
check("replaced save was backed up", any(b.read_bytes() == b"C" * 296960 for b in backups))

# Failed upload: loud, local save untouched, retried next start.
local_save.write_bytes(b"E" * 296960)
FLAGS["fail_upload"] = True
check("failed upload reports failure", app.upload(oot, saves.FileSave(local_save)) is False)
check("local save untouched", local_save.read_bytes() == b"E" * 296960)
check("marked pending", app.state["7"]["pending_upload"] is True)
FLAGS["fail_upload"] = False
app.retry_pending(library)
check("pending upload retried", saves.fetch_remote(c, oot, saves.FileSave(local_save)).data == b"E" * 296960
      and app.state["7"]["pending_upload"] is False)

# No save in RomM: say so, start the game normally, upload the save it made on close.
# RetroArch is replaced by a stand-in that "plays" by writing a save file.
import contextlib
import io

sm64 = [g for g in library if g.id == 8][0]
sm64.rom_dest.parent.mkdir(parents=True, exist_ok=True)
sm64.rom_dest.write_bytes(b"rom")
(ra / "retroarch").write_bytes(b"")
(ra / "cores" / f"mupen64plus_next_libretro{system.CORE_EXT}").write_bytes(b"core")
sm64_save = sm64.save_path(sm64.rom_dest)
launched = []


class FakeRetroArch:
    def __init__(self, args, **kw):
        launched.append(args)
        sm64_save.parent.mkdir(parents=True, exist_ok=True)
        sm64_save.write_bytes(b"M" * 512)

    def wait(self):
        return 0


real_popen, subprocess.Popen = subprocess.Popen, FakeRetroArch
out = io.StringIO()
with contextlib.redirect_stdout(out):
    app.play(sm64)
subprocess.Popen = real_popen
log = out.getvalue()
check("no RomM save is reported", "RomM has no save for this game yet" in log)
check("game started normally", launched and launched[0][-1] == str(sm64.rom_dest))
check("new save uploaded on close", "Save uploaded to RomM." in log
      and saves.fetch_remote(c, sm64, saves.FileSave(sm64_save)).data == b"M" * 512
      and app.state["8"]["hash"] == util.sha256(b"M" * 512))
sm64.rom_dest.unlink()  # later tests expect only Ocarina of Time on this computer

# RomM that refuses Basic auth on writes (CSRF) -> switch to a token.
FLAGS["csrf"] = True
c2 = romm.RommClient(base, "leo", "pw")
c2.upload_save(7, "x.srm", b"F", "mupen64plus_next")
check("CSRF -> token fallback", SEEN["token"] == 1 and c2.auth == "Bearer tok")
FLAGS["csrf"] = False

# Older RomM without /api/saves listing or /content download.
FLAGS["old"] = True
check("old RomM fallbacks", saves.fetch_remote(c, oot, saves.FileSave(local_save)).data == b"F")
FLAGS["old"] = False

# Platforms
emerald = [g for g in library if g.id == 9][0]
check("GBA uses mGBA folder", emerald.save_dir() == ra / "saves" / "mGBA")
cfg3 = config.load_config()
check("3DS saves are synced", games.Game({"fs_name": "a.3ds", "platform_fs_slug": "3ds", "id": 1}, cfg3).syncs_saves)

# Cores: config names are OS-neutral, files are .dll on Windows and .so on Linux.
check("core_id strips any extension", retroarch.core_id("citra_libretro.dll") == retroarch.core_id("citra_libretro.so") == "citra")
(ra / "cores" / f"citra_libretro{system.CORE_EXT}").write_bytes(b"core")
check("find_core by name", retroarch.find_core(ra / "cores", "citra") == ra / "cores" / f"citra_libretro{system.CORE_EXT}")
check("find_core accepts old file-name config", retroarch.find_core(ra / "cores", f"citra_libretro{system.CORE_EXT}") is not None)
check("find_core missing", retroarch.find_core(ra / "cores", "mgba") is None)

# RetroArch's own retroarch.cfg decides where saves go.
(ra / "retroarch.cfg").write_text(
    'savefile_directory = ":\\saves"\nsort_savefiles_enable = "false"\n', encoding="utf-8"
)
check("':' paths are relative to RetroArch", retroarch.read_retroarch_cfg(str(ra / "retroarch"))["savefile_directory"] == str(ra / "saves"))
rom_file = tmp / "Roms" / "n64" / OOT
check("sorting off -> plain saves folder", oot.save_path(rom_file).parent == ra / "saves")
(ra / "retroarch.cfg").write_text(
    'sort_savefiles_enable = "true"\nsort_savefiles_by_content_enable = "true"\n', encoding="utf-8"
)
check("sort by core + content dir", oot.save_path(rom_file).parent == save_dir / "n64")

# Missing cores are downloaded from libretro's buildbot (a fake one here).
core_downloads.BUILDBOT = base.rstrip("/")
check("buildbot folder for this computer", core_downloads.buildbot_platform() is not None)
ext = system.CORE_EXT
check("RetroArch's updater address is used (over https)", core_downloads.core_download_url("snes9x", {
    "core_updater_buildbot_cores_url": "http://buildbot.libretro.com/nightly/windows/x86_64/latest/"})
    == f"https://buildbot.libretro.com/nightly/windows/x86_64/latest/snes9x_libretro{ext}.zip")
check("plain-http mirrors are ignored", core_downloads.core_download_url("snes9x", {
    "core_updater_buildbot_cores_url": "http://mirror.example/cores/"})
    == f"{core_downloads.BUILDBOT}/nightly/{core_downloads.buildbot_platform()}/latest/snes9x_libretro{ext}.zip")

cores2 = tmp / "cores2"
app.config["cores_dir"] = str(cores2)
emerald_core = app.ensure_core(emerald)
check("missing core downloaded into the cores folder", emerald_core == cores2 / f"mgba_libretro{ext}"
      and emerald_core.read_bytes() == f"CORE:mgba_libretro{ext}".encode())
check("its .info file installed too", (cores2 / "mgba_libretro.info").read_text().startswith("corename"))
check("no leftover .part files", not list(cores2.glob("*.part")))
downloads = SEEN["core_downloads"]
check("installed core is reused", app.ensure_core(emerald) == emerald_core and SEEN["core_downloads"] == downloads)

(tmp / "cores3").mkdir()
(tmp / "cores3" / f"mgba_libretro{ext}").write_bytes(b"mine")
check("an existing core is never replaced", core_downloads.install_core("mgba", tmp / "cores3", {}).read_bytes() == b"mine"
      and SEEN["core_downloads"] == downloads)
for bad, why in (("nosuchcore", "answered 404"), ("badzip", "doesn't contain")):
    try:
        core_downloads.install_core(bad, tmp / "cores4", {})
        raise SystemExit(f"FAILED: {bad} installed")
    except core_downloads.CoreInstallError as e:
        check(f"{bad}: clear error, nothing written", why in str(e) and not list((tmp / "cores4").glob("*")))

app.config.update(cores_dir=str(tmp / "cores5"), auto_install_cores=False)
downloads = SEEN["core_downloads"]
check("auto_install_cores off -> no download", app.ensure_core(emerald) is None and SEEN["core_downloads"] == downloads)
app.config.update(cores_dir=str(ra / "cores"), auto_install_cores=True)

core_downloads.install_core_info("snes9x", cores2, {})
check("save folder named from the downloaded .info", retroarch.core_save_folder("S", str(cores2), "snes9x") == str(Path("S") / "Snes9x"))
check(".info found in RetroArch's info folder", retroarch.core_save_folder("S", str(tmp), "snes9x", str(cores2)) == str(Path("S") / "Snes9x"))
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
check("title ID from .3ds header", saves.title_id_from_rom(rom3) == "0004000000033500")
z3 = games.Game({"id": 11, "fs_name": rom3.name, "platform_fs_slug": "3ds",
             "save_target": "00040000/00033500"}, config.load_config())
s3 = z3.save(rom3)
data_dir = (ra / "saves" / "Citra" / "Citra" / "sdmc" / "Nintendo 3DS" / ("0" * 32) / ("0" * 32)
            / "title" / "00040000" / "00033500" / "data")
check("3DS save is Citra's data folder", isinstance(s3, saves.FolderSave) and s3.path == data_dir)
check("same folder from ROM header alone",
      games.Game(dict(z3.rom, save_target=None), z3.config).save(rom3).path == data_dir)
check("3DS uploads as <rom>.zip", s3.upload_name == "Zelda OoT3D.zip")
check("no save yet -> no hash", s3.hash() is None)

(data_dir / "00000001").mkdir(parents=True)
(data_dir / "00000001" / "save00.bin").write_bytes(b"link" * 1000)
(data_dir / "00000001" / "system.dat").write_bytes(b"sys")
(data_dir / "00000001.metadata").write_bytes(b"meta")
check("zip is reproducible", s3.read() == s3.read())
check("folder hash == zip hash", s3.hash() == s3.hash_of(s3.read()))
check("3DS upload", app.upload(z3, s3))
remote3 = saves.fetch_remote(c, z3, s3)
check("RomM has the zip, same hash", remote3.info["file_name"] == "Zelda OoT3D.zip" and remote3.hash == s3.hash())
check("unchanged 3DS save is up to date", app.sync_before_play(z3, s3) == s3.hash()
      and app.state["11"]["hash"] == s3.hash())

# Another device saved further: new save00.bin, system.dat removed.
other = saves.FolderSave(tmp / "other-device" / "data", "Zelda OoT3D.zip")
(other.path / "00000001").mkdir(parents=True)
(other.path / "00000001" / "save00.bin").write_bytes(b"ganon" * 1000)
(other.path / "00000001.metadata").write_bytes(b"meta")
c.upload_save(11, "Zelda OoT3D.zip", other.read(), "citra")
old_hash = s3.hash()
app.sync_before_play(z3, s3)
check("newer 3DS save unpacked", (data_dir / "00000001" / "save00.bin").read_bytes() == b"ganon" * 1000)
check("files gone from the new save are gone", not (data_dir / "00000001" / "system.dat").exists())
check("no leftover temp folders", sorted(p.name for p in data_dir.parent.iterdir()) == ["data"])
zips = [b for b in (paths.BACKUP_DIR / "11").iterdir() if b.suffix == ".zip"]
check("old 3DS save backed up as zip", any(s3.hash_of(b.read_bytes()) == old_hash for b in zips))

# Played: local changes -> uploaded after play (same path as play() takes).
(data_dir / "00000001" / "save00.bin").write_bytes(b"triforce" * 1000)
check("3DS local newer kept", app.sync_before_play(z3, s3) != s3.hash()
      and (data_dir / "00000001" / "save00.bin").read_bytes() == b"triforce" * 1000)
app.upload(z3, s3)
check("3DS RomM updated", saves.fetch_remote(c, z3, s3).hash == s3.hash())

# A malicious or broken zip is refused and the local save stays.
import io, zipfile
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("../../evil.txt", "x")
try:
    s3.write(buf.getvalue())
    raise SystemExit("unsafe zip accepted")
except romm.RommError:
    check("unsafe zip refused, save intact", (data_dir / "00000001" / "save00.bin").read_bytes() == b"triforce" * 1000)
try:
    s3.hash_of(b"not a zip")
    raise SystemExit("bad zip accepted")
except romm.RommError:
    check("broken zip reported", True)

# Old config that switched 3DS sync off is migrated.
util.save_json(paths.CONFIG_FILE, dict(cfg, platforms={"3ds": {"core": "citra", "save_exts": [], "sync_saves": False}}))
check("old 3DS config migrated", config.load_config()["platforms"]["3ds"]["save_layout"] == "citra"
      and games.Game({"id": 1, "fs_name": "a.3ds", "platform_fs_slug": "3ds"}, config.load_config()).syncs_saves)
util.save_json(paths.CONFIG_FILE, cfg)

# One config.json for Windows and Linux: RetroArch paths live in a section per system.
check("guess_os windows", config.guess_os({"retroarch": "C:\\RetroArch-Win64\\retroarch.exe"}) == "windows")
check("guess_os linux", config.guess_os({"retroarch": "~/.steam/x/RetroArch/retroarch"}) == "linux")
check("guess_os macos", config.guess_os({"retroarch": "/Applications/RetroArch.app/Contents/MacOS/RetroArch"}) == "macos")
saved_cfg_file, saved_os = paths.CONFIG_FILE, system.OS_KEY
paths.CONFIG_FILE = tmp / "multi" / "config.json"
win_paths = {"retroarch": "C:\\RetroArch-Win64\\retroarch.exe", "cores_dir": "C:\\RetroArch-Win64\\cores",
             "saves_dir": "C:\\RetroArch-Win64\\saves", "sort_saves_by_core": True}
util.save_json(paths.CONFIG_FILE, {"romm_url": base, "username": "leo", "roms_dir": "roms", **win_paths})

system.OS_KEY = "linux"  # the Windows config copied to a Linux computer
lc = config.load_config()
check("other system's flat paths not used", lc["retroarch"] == str(retroarch.DEFAULT_RETROARCH) and "linux" not in lc)
check("flat paths read as their system's section", lc["windows"] == win_paths)
check("shared settings kept", lc["username"] == "leo" and lc["romm_url"] == base)
check("RetroArch setup needed on the new system", launcher.Launcher().needs_retroarch_setup())
config.migrate_flat_config()
on_disk = util.load_json(paths.CONFIG_FILE, {})
check("flat config rewritten with sections", on_disk.get("windows") == win_paths
      and "retroarch" not in on_disk and "linux" not in on_disk and on_disk["username"] == "leo")
config.migrate_flat_config()
check("rewrite only once", util.load_json(paths.CONFIG_FILE, {}) == on_disk)

# Setting up RetroArch on Linux adds a "linux" section and leaves "windows" alone.
lin = launcher.Launcher()
lin.config.setdefault("linux", {})
lin.config.update(retroarch="/opt/ra/retroarch", cores_dir="/opt/ra/cores", saves_dir="/opt/ra/saves")
lin.save_config()
on_disk = util.load_json(paths.CONFIG_FILE, {})
check("both sections on disk", on_disk["linux"]["retroarch"] == "/opt/ra/retroarch"
      and on_disk["windows"] == win_paths and "retroarch" not in on_disk)
check("Linux uses its section", config.load_config()["saves_dir"] == "/opt/ra/saves"
      and not launcher.Launcher().needs_retroarch_setup())
system.OS_KEY = "windows"
wc = config.load_config()
check("Windows uses its section", wc["retroarch"] == win_paths["retroarch"] and wc["saves_dir"] == win_paths["saves_dir"])
launcher.Launcher().save_config()
check("saving on Windows keeps the Linux section", util.load_json(paths.CONFIG_FILE, {})["linux"]["retroarch"] == "/opt/ra/retroarch")
check("per-system ROM folder", config.roms_root({"roms_dir": "roms", "windows": {"roms_dir": str(tmp / "W")}}) == tmp / "W"
      and config.roms_root({"roms_dir": "roms", "linux": {"roms_dir": "/x"}}) == paths.APP_HOME / "roms")
paths.CONFIG_FILE, system.OS_KEY = saved_cfg_file, saved_os
check("flat test config still read on this system", config.load_config()["retroarch"] == str(ra / "retroarch"))

# Moving everything into one launcher folder (APP_HOME) from older locations.
mig = tmp / "migration"
old_cfg, old_data = mig / "AppData" / "Roaming" / "romm-launcher", mig / "AppData" / "Local" / "romm-launcher"
saved = (paths.APP_HOME, paths.CONFIG_FILE, paths.OLD_LOCATIONS, paths.OLD_ROMS_DIRS)


def use_home(home):
    paths.APP_HOME, paths.CONFIG_FILE = home, home / "config.json"
    paths.OLD_LOCATIONS = [old_cfg, old_data]
    paths.OLD_ROMS_DIRS = [mig / "Documents" / "Roms", old_cfg / "roms", old_data / "roms"]


home = mig / "Workspace" / "RomM"
use_home(home)
util.save_json(old_cfg / "config.json", {"username": "leo", "roms_dir": str(old_data / "roms")})
(old_cfg / "config.steam-backup.json").write_text("{}")
util.save_json(old_data / "state.json", {"7": {"hash": "h"}})
(old_data / "backups" / "7").mkdir(parents=True)
(old_data / "backups" / "7" / "b.zip").write_bytes(b"backup")
(old_data / "roms" / "3ds").mkdir(parents=True)
(old_data / "roms" / "3ds" / "game.3ds").write_bytes(b"rom")
config.migrate_legacy_dirs()
check("settings moved into home", util.load_json(home / "config.json", {})["username"] == "leo"
      and (home / "config.steam-backup.json").exists())
check("state + backups moved", util.load_json(home / "state.json", {})["7"]["hash"] == "h"
      and (home / "backups" / "7" / "b.zip").read_bytes() == b"backup")
check("ROMs moved into home", (home / "roms" / "3ds" / "game.3ds").read_bytes() == b"rom")
check("config points at home/roms (relative)", util.load_json(home / "config.json", {})["roms_dir"] == "roms"
      and config.roms_root(util.load_json(home / "config.json", {})) == home / "roms")
check("old folders removed", not old_cfg.exists() and not old_data.exists())
config.migrate_legacy_dirs()
check("migration runs once", (home / "roms" / "3ds" / "game.3ds").exists())

# Never overwrite what's already in home; leave the old copy instead.
util.save_json(old_data / "state.json", {"old": True})
config.migrate_legacy_dirs()
check("existing files never overwritten", util.load_json(home / "state.json", {})["7"]["hash"] == "h"
      and (old_data / "state.json").exists())
(old_data / "state.json").unlink()
old_data.rmdir()

# Very old versions: ~/Documents/Roms.
home2 = mig / "Home2"
use_home(home2)
docs = mig / "Documents" / "Roms"
(docs / "n64").mkdir(parents=True)
(docs / "n64" / "z.z64").write_bytes(b"z")
util.save_json(home2 / "config.json", {"username": "leo", "roms_dir": str(docs)})
config.migrate_legacy_dirs()
check("Documents/Roms moved", (home2 / "roms" / "n64" / "z.z64").exists() and not docs.exists()
      and util.load_json(home2 / "config.json", {})["roms_dir"] == "roms")
util.save_json(home2 / "config.json", {"username": "leo", "roms_dir": str(mig / "MyRoms")})
config.migrate_legacy_dirs()
check("a chosen ROM folder is left alone", util.load_json(home2 / "config.json", {})["roms_dir"] == str(mig / "MyRoms"))
paths.APP_HOME, paths.CONFIG_FILE, paths.OLD_LOCATIONS, paths.OLD_ROMS_DIRS = saved

# By default everything lives next to the script, and "roms" is relative to it.
import os
if not os.environ.get(paths.HOME_ENV):
    check("launcher folder is the script's folder", paths.APP_HOME == Path(__file__).resolve().parent.parent)
check("default ROM folder inside launcher folder", config.roms_root(config.default_config()) == paths.APP_HOME / "roms")
check("absolute ROM folder kept", config.roms_root({"roms_dir": str(tmp / "X")}) == tmp / "X")

# Two-screen games (DS, 3DS): main screen only, or both. Set in RetroArch's core options.
opts = tmp / "opts" / "x.opt"
opts.parent.mkdir()
opts.write_text('a = "1"\n# comment\nmelonds_screen_layout1 = "left-right"\nb = "2"\n')
retroarch.set_core_options(opts, {"melonds_screen_layout1": "top", "new_key": "v"})
check("only the chosen options change", opts.read_text() == 'a = "1"\n# comment\nmelonds_screen_layout1 = "top"\nb = "2"\nnew_key = "v"\n')

rcfg = {"rgui_config_directory": str(tmp / "racfg")}
ds_rom = tmp / "Roms" / "nds" / "Some DS Game.nds"
core_dir = tmp / "racfg" / "melonDS DS"
core_dir.mkdir(parents=True)
check("core's options file by default", retroarch.core_options_file("ra", rcfg, "melonDS DS", ds_rom) == core_dir / "melonDS DS.opt")
(core_dir / "nds.opt").write_text("")
check("content folder's file wins", retroarch.core_options_file("ra", rcfg, "melonDS DS", ds_rom) == core_dir / "nds.opt")
(core_dir / "Some DS Game.opt").write_text("")
check("this game's file wins", retroarch.core_options_file("ra", rcfg, "melonDS DS", ds_rom) == core_dir / "Some DS Game.opt")
check("game files ignored when RetroArch ignores them",
      retroarch.core_options_file("ra", dict(rcfg, game_specific_options="false"), "melonDS DS", ds_rom) == core_dir / "melonDS DS.opt")
check("one file for all cores", retroarch.core_options_file("ra", dict(rcfg, game_specific_options="false", global_core_options="true",
      core_options_path=str(tmp / "all.cfg")), "melonDS DS", ds_rom) == tmp / "all.cfg")

# The launcher asks, remembers the answer, and writes RetroArch's option for that core.
ds = games.Game({"id": 20, "name": "DS Game", "fs_name": "ds.nds", "platform_fs_slug": "nds"}, app.config)
ds_rom = tmp / "Roms" / "nds" / "ds.nds"
mel_opt = ra / "config" / "melonDS DS" / "melonDS DS.opt"
mel_opt.parent.mkdir(parents=True)
mel_opt.write_text('melonds_screen_layout1 = "top-bottom"\nmelonds_screen_gap = "0"\n')
mel_core = ra / "cores" / f"melondsds_libretro{system.CORE_EXT}"
answers = []
ui.input = lambda prompt: answers.pop(0)
with contextlib.redirect_stdout(io.StringIO()):
    answers[:] = ["1"]
    app.apply_screen_layout(ds, mel_core, ds_rom)
    check("1 = main screen only", 'melonds_screen_layout1 = "top"' in mel_opt.read_text()
          and 'melonds_screen_gap = "0"' in mel_opt.read_text() and app.state["20"]["screen_layout"] == "main")
    answers[:] = [""]
    app.apply_screen_layout(ds, mel_core, ds_rom)
    check("Enter keeps last time's choice", 'melonds_screen_layout1 = "top"' in mel_opt.read_text())
    answers[:] = ["x", "2"]
    app.apply_screen_layout(ds, mel_core, ds_rom)
    check("2 = both screens (asks again on a wrong answer)", 'melonds_screen_layout1 = "top-bottom"' in mel_opt.read_text()
          and app.state["20"]["screen_layout"] == "both")
    answers[:] = []
    app.apply_screen_layout(ds, mel_core, ds_rom, "main")
    check("--screen main skips the question", 'melonds_screen_layout1 = "top"' in mel_opt.read_text())
    tdsx = games.Game({"id": 21, "name": "3DS Game", "fs_name": "z.3ds", "platform_fs_slug": "3ds"}, app.config)
    app.apply_screen_layout(tdsx, ra / "cores" / f"citra_libretro{system.CORE_EXT}", tmp / "z.3ds", "main")
    citra_opt = (ra / "config" / "Citra" / "Citra.opt").read_text()
    check("3DS: Citra single screen, top", 'citra_layout_option = "Single Screen Only"' in citra_opt
          and 'citra_swap_screen = "Top"' in citra_opt)
    # 3 = main screen + small touch screen in the bottom-right corner.
    answers[:] = ["3"]
    app.apply_screen_layout(ds, mel_core, ds_rom)
    mel = mel_opt.read_text()
    check("3 = corner: melonDS DS hybrid, bottom screen only, 3:1", 'melonds_screen_layout1 = "hybrid-top"' in mel
          and 'melonds_hybrid_small_screen = "one"' in mel and 'melonds_hybrid_ratio = "3"' in mel
          and app.state["20"]["screen_layout"] == "corner")
    app.apply_screen_layout(tdsx, ra / "cores" / f"citra_libretro{system.CORE_EXT}", tmp / "z.3ds", "corner")
    check("3DS corner: Citra large + small screen", 'citra_layout_option = "Large Screen, Small Screen"'
          in (ra / "config" / "Citra" / "Citra.opt").read_text())
    # Cores where the corner layout isn't known: only 1 and 2 are offered.
    (ra / "config" / "DeSmuME").mkdir()
    shown = io.StringIO()
    answers[:] = ["3", "1"]
    with contextlib.redirect_stdout(shown):
        app.apply_screen_layout(ds, ra / "cores" / f"desmume_libretro{system.CORE_EXT}", ds_rom, "corner")
    check("no corner choice for DeSmuME", "corner" not in shown.getvalue().split("Which layout?")[1]
          and 'desmume_screens_layout = "top only"' in (ra / "config" / "DeSmuME" / "DeSmuME.opt").read_text())
del ui.input

# Touch pointer for controllers: shown (config on, the default) or hidden (off) whenever
# the layout shows the touch screen, and the controls are printed.
check("touch pointer on by default", config.default_config()["show_touch_pointer"] is True)
citra_file = ra / "config" / "Citra" / "Citra.opt"
shown = io.StringIO()
with contextlib.redirect_stdout(shown):
    app.apply_screen_layout(tdsx, ra / "cores" / f"citra_libretro{system.CORE_EXT}", tmp / "z.3ds", "both")
check("3DS: pointer drawn", 'citra_render_touchscreen = "enabled"' in citra_file.read_text())
check("3DS: controls printed", "right stick moves the pointer, R3 touches" in shown.getvalue())
app.config["show_touch_pointer"] = False
with contextlib.redirect_stdout(io.StringIO()):
    app.apply_screen_layout(ds, mel_core, ds_rom, "corner")
check("turned off -> pointer hidden", 'melonds_show_cursor = "disabled"' in mel_opt.read_text())
app.config["show_touch_pointer"] = True
shown = io.StringIO()
with contextlib.redirect_stdout(shown):
    app.apply_screen_layout(ds, mel_core, ds_rom, "main")
check("main screen only leaves the pointer alone", 'melonds_show_cursor = "disabled"' in mel_opt.read_text()
      and "touches" not in shown.getvalue())
shown = io.StringIO()
with contextlib.redirect_stdout(shown):
    app.apply_screen_layout(ds, mel_core, ds_rom, "corner")
check("DS: cursor shown, controls printed", 'melonds_show_cursor = "timeout"' in mel_opt.read_text()
      and "R2 touches" in shown.getvalue())
# RetroArch from Flathub ("flatpak:org.libretro.RetroArch"), with a fake flatpak command
# and a fake ~/.var/app.
FP = retroarch.FLATPAK_RETROARCH
fp_install = tmp / "flatpak-install"  # what `flatpak info --show-location` reports
(fp_install / "files" / "share" / "libretro" / "info").mkdir(parents=True)
(fp_install / "files" / "share" / "libretro" / "info" / "mupen64plus_next_libretro.info").write_text(
    'corename = "Mupen64Plus-Next"\n')
retroarch.FLATPAK_APPS = tmp / "var-app"
fp_home = retroarch.FLATPAK_APPS / "org.libretro.RetroArch" / "config" / "retroarch"
fp_home.mkdir(parents=True)
(fp_home / "retroarch.cfg").write_text(
    'libretro_info_path = "/app/share/libretro/info"\n'
    f'libretro_directory = "{(fp_home / "cores").as_posix()}"\n'
    'sort_savefiles_enable = "true"\n')
FLATPAK = {"installed": True, "calls": []}


def fake_run(args, **kw):
    FLATPAK["calls"].append(args)
    ok_ = FLATPAK["installed"] and args[:3] == ["flatpak", "info", "--show-location"]
    return subprocess.CompletedProcess(args, 0 if ok_ else 1, stdout=f"{fp_install}\n" if ok_ else "", stderr="")


real_run, subprocess.run = subprocess.run, fake_run
retroarch._flatpak_locations.clear()
check("flatpak: app id", retroarch.flatpak_id(FP) == "org.libretro.RetroArch" and retroarch.flatpak_id(str(ra / "retroarch")) is None)
check("flatpak: started with flatpak run", retroarch.retroarch_command(FP) == ["flatpak", "run", "org.libretro.RetroArch"])
check("flatpak: settings in ~/.var/app", retroarch.retroarch_home(FP) == fp_home
      and retroarch.retroarch_config_dir(FP, {}) == fp_home / "config")
check("flatpak: installed check", retroarch.retroarch_installed(FP))
fra = retroarch.read_retroarch_cfg(FP)
check("flatpak: its retroarch.cfg is read", fra["libretro_directory"] == (fp_home / "cores").as_posix())
check("flatpak: /app paths point into the installed app",
      Path(fra["libretro_info_path"]) == fp_install / "files" / "share" / "libretro" / "info")
check("flatpak: core names from the app's .info files",
      retroarch.core_display_name(str(fp_home / "cores"), "mupen64plus_next", fra["libretro_info_path"]) == "Mupen64Plus-Next")
check("flatpak config counts as Linux", config.guess_os({"retroarch": FP}) == "linux")

# A whole game start with the Flatpak RetroArch.
saved_ra = (app.config["retroarch"], app.config["cores_dir"], app.config["saves_dir"])
app.config.update(retroarch=FP, cores_dir=str(fp_home / "cores"), saves_dir=str(fp_home / "saves"))
(fp_home / "cores").mkdir()
(fp_home / "cores" / f"mupen64plus_next_libretro{system.CORE_EXT}").write_bytes(b"core")
sm64.rom_dest.write_bytes(b"rom")
sm64_save = sm64.save_path(sm64.rom_dest)
launched.clear()
subprocess.Popen = FakeRetroArch
with contextlib.redirect_stdout(io.StringIO()):
    app.play(sm64)
subprocess.Popen = real_popen
check("flatpak: game started through flatpak run", launched and launched[0][:4] == ["flatpak", "run", "org.libretro.RetroArch", "-L"]
      and launched[0][-1] == str(sm64.rom_dest))
check("flatpak: saves read from ~/.var/app", sm64_save.is_relative_to(fp_home / "saves" / "Mupen64Plus-Next"))
FLATPAK["installed"] = False
retroarch._flatpak_locations.clear()
launched.clear()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    app.play(sm64)
check("flatpak not installed: clear message, nothing started", not launched and "flatpak install flathub" in out.getvalue())
sm64.rom_dest.unlink()
app.config.update(retroarch=saved_ra[0], cores_dir=saved_ra[1], saves_dir=saved_ra[2])
subprocess.run = real_run
retroarch._flatpak_locations.clear()

check("DSi and New 3DS are two-screen platforms too",
      retroarch.core_id(config.load_config()["platforms"]["nintendo-dsi"]["core"]) in dualscreen.SCREEN_LAYOUTS
      and retroarch.core_id(config.load_config()["platforms"]["new-nintendo-3ds"]["core"]) in dualscreen.SCREEN_LAYOUTS)

# ---- GUI pieces -------------------------------------------------------------
# The terminal's multiple-choice question: q cancels when allowed.
ui.input = lambda prompt: "q"
with contextlib.redirect_stdout(io.StringIO()):
    check("choose: q cancels", ui.choose("Pick", [("1", "a"), ("2", "b")], "Number", cancel=True) is None)
answers[:] = ["x", "B"]
ui.input = lambda prompt: answers.pop(0)
with contextlib.redirect_stdout(io.StringIO()):
    check("choose: asks again, any letter case", ui.choose("Pick", [("a", "x"), ("b", "y")], "Letter") == "b")
del ui.input

# Sidebar and filtering.
local_ids = {7}
flt = games.library_filters(library, local_ids)
check("sidebar: all, local, then platforms", [k for k, _, _ in flt] == ["all", "local", "platform:gba", "platform:n64"]
      and [n for _, _, n in flt] == [3, 1, 1, 2])
check("filter: on this computer", [g.id for g in games.filter_library(library, "local", "", local_ids)] == [7])
check("filter: platform + search", [g.id for g in games.filter_library(library, "platform:n64", "mario", local_ids)] == [8])
check("filter: sorted by name", [g.name for g in games.filter_library(library, "all", "", local_ids)]
      == sorted(g.name for g in library))
check("screen choices only for two-screen cores", dualscreen.screen_choices(ds) == ["main", "both", "corner"]
      and dualscreen.screen_choices(oot) == [])

now = datetime(2026, 11, 16, 22, 0)
check("friendly times", [gui.friendly_time(t, now) for t in ("2026-11-16T20:05:13", "2026-11-15 09:30",
                                                              "2026-11-12T10:00:00", "2026-10-03T10:00", "")]
      == ["Today, 20:05", "Yesterday, 09:30", "Thursday, 10:00", "3 Oct 2026", "Never"])

# Covers: downloaded once from RomM, PNG only.
paths.COVER_DIR = tmp / "covers"
cov = games.Game(dict(ROMS[0], id=30, path_cover_small="/assets/romm/resources/roms/4/30/cover/small.png?ts=2026-11-14 12:46:31"), app.config)
check("cover downloaded (URL with a space)", games.fetch_cover(c, cov) == tmp / "covers" / "30.png"
      and (tmp / "covers" / "30.png").read_bytes() == PNG)
n = SEEN["assets"]
check("cover kept, not downloaded again", games.fetch_cover(c, cov) and SEEN["assets"] == n)
jpg = games.Game(dict(ROMS[0], id=31, path_cover_small="/assets/romm/resources/roms/4/31/cover/small.jpg"), app.config)
check("non-PNG covers skipped", games.fetch_cover(c, jpg) is None and not (tmp / "covers" / "31.png").exists())

# The window itself: log in against the fake RomM, then the library shows up.
try:
    window = gui.GuiApp()
except Exception as exc:  # no display (e.g. a Linux server)
    window = None
    print(f"skipped: GUI tests ({exc})")
if window:
    import time as _t
    window.root.withdraw()
    ui.set_ui(gui.GuiUI(window))

    def pump(until, timeout=15):
        end = _t.time() + timeout
        while _t.time() < end and not until():
            window.root.update()
            _t.sleep(0.02)
        return until()

    window.show_login()
    check("login asks for address, username and password", window.v_url.get().rstrip("/") == base.rstrip("/")
          and window.v_user.get() == "leo" and window.v_pass.get() == "")
    window.v_pass.set("wrong")
    window.do_login()
    check("wrong password shown", pump(lambda: "rejected" in window.login_msg.cget("text")))
    window.v_pass.set("pw")
    window.do_login()
    check("library loads after login", pump(lambda: len(window.tiles) == 3))
    check("filter chips: all, installed, then platforms",
          list(window.chips) == ["all", "local", "platform:gba", "platform:n64"]
          and [c.text for c in window.chips.values()] == ["All", "Installed", "GBA", "N64"])
    window.v_search.set("zelda")
    check("search narrows the grid", list(window.tiles) == [7])
    window.v_search.set("")
    window.chips["platform:gba"].invoke()
    check("platform filter", list(window.tiles) == [9])
    window.set_filter("all")
    window.select(oot)
    check("details for the selected game", window.play_button is not None
          and window.play_button.text.endswith("Play") and window.play_button.enabled)
    window.select(ds)
    check("DS game: layout choices shown", window.v_layout.get() in ("main", "both", "corner")
          and len(window.layout_row.winfo_children()) == 3)
    window.layout_row.winfo_children()[0].invoke()
    check("layout chip picks the layout", window.v_layout.get() == "main")
    check("cover title cut to fit", window.fit_text("x" * 200, window.font(10, "bold"), window.TILE_W).endswith("…"))
    # The launcher's questions from a worker thread become dialogs on Tk's thread.
    window.ask_choice = lambda question, options, default, cancel, note: "l"
    got = {}
    threading.Thread(target=lambda: got.update(v=ui.choose("Which?", [("r", "RomM"), ("l", "here")], "?")), daemon=True).start()
    check("worker-thread question answered by the window", pump(lambda: got.get("v") == "l"))
    threading.Thread(target=lambda: ui.warn("hello from the worker"), daemon=True).start()
    check("worker-thread messages reach the activity log",
          pump(lambda: "hello from the worker" in window.activity.get("1.0", "end")))
    check("the status bar shows the last message", "hello from the worker" in window.status.cget("text")
          and not window.activity_open)
    window.log("upload failed", "fail")
    check("a failure opens the activity log", window.activity_open)
    ui.set_ui(ui.TerminalUI())
    window.root.destroy()

# Offline: only games on this computer are listed.
off = launcher.Launcher()
off.offline = True
check("offline lists local games only", [g.id for g in off.library()] == [7])

check("still nothing destructive reached the server", SEEN["destructive"] == 0)
print("\nall tests passed")
