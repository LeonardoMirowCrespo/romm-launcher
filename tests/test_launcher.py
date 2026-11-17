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
import urllib.request
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
    ports,
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
STORE = {"saves": [], "files": {}, "uploads": {}, "new_roms": {}}
SUPPORTED = [("snes", "Super Nintendo Entertainment System"), ("nes", "Nintendo Entertainment System"),
             ("genesis", "Sega Mega Drive/Genesis"), ("psx", "Sony PlayStation"), ("n64", "Nintendo 64")]
PLATFORMS = [{"id": 1, "fs_slug": "n64", "display_name": "Nintendo 64"},
             {"id": 2, "fs_slug": "gba", "display_name": "Game Boy Advance"},
             {"id": 3, "fs_slug": "nds", "display_name": "Nintendo DS"}]
AUTH = "Basic " + base64.b64encode(b"leo:pw").decode()
FLAGS = {"old": False, "csrf": False, "fail_upload": False, "fail_save_download": False}
SEEN = {"rom_downloads": 0, "token": 0, "destructive": 0, "core_downloads": 0, "scans": 0, "cancels": 0,
        "soh_downloads": 0}
GH = {"release": {}, "files": {}}  # the fake GitHub's latest SoH release and its files


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
        if u.path.startswith("/repos/") and u.path.endswith("/releases/latest"):  # stands in for GitHub
            GH["asked"] = u.path
            return self.send(200, GH["release"])
        if "/releases/download/" in u.path:
            SEEN["soh_downloads"] += 1
            return self.send(200, GH["files"][unquote(u.path.rsplit("/", 1)[-1])], "application/octet-stream")
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
        if u.path == "/api/platforms":
            return self.send(200, PLATFORMS)
        if u.path == "/api/platforms/supported":
            mine = {p["fs_slug"] for p in PLATFORMS}
            return self.send(200, PLATFORMS + [{"id": -1, "slug": slug, "fs_slug": slug, "name": name}
                                               for slug, name in SUPPORTED if slug not in mine])
        if u.path.startswith("/api/tasks/"):
            return self.send(200, {"task_id": u.path.rsplit("/", 1)[-1], "status": "finished"})
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

    do_DELETE = do_PATCH = refuse_write

    def do_PUT(self):  # only a chunk of a ROM upload that was started
        u = urlparse(self.path)
        upload = STORE["uploads"].get(u.path.rsplit("/", 1)[-1])
        if not (u.path.startswith("/api/roms/upload/") and upload and self.authed()):
            return self.refuse_write()
        upload["chunks"][int(self.headers["x-chunk-index"])] = self.rfile.read(int(self.headers["Content-Length"]))
        self.send(200, {"received": len(upload["chunks"]), "total": upload["total_chunks"]})

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
        if u.path == "/api/roms/upload/start":
            body = json.loads(raw)
            slug = next(p["fs_slug"] for p in PLATFORMS if p["id"] == int(self.headers["x-upload-platform"]))
            if (slug, body["filename"]) in STORE["new_roms"] or any(
                    r["platform_fs_slug"] == slug and r["fs_name"] == body["filename"] for r in ROMS):
                return self.send(400, {"detail": f"File {body['filename']} already exists"})
            uid = f"00000000-0000-4000-8000-{len(STORE['uploads']):012d}"
            STORE["uploads"][uid] = {"name": body["filename"], "slug": slug, "chunks": {},
                                     "size": int(self.headers["x-upload-total-size"]),
                                     "total_chunks": int(self.headers["x-upload-total-chunks"])}
            return self.send(201, {"upload_id": uid})
        if u.path.startswith("/api/roms/upload/") and u.path.endswith("/complete"):
            up = STORE["uploads"][u.path.split("/")[-2]]
            data = b"".join(up["chunks"][i] for i in range(up["total_chunks"]))
            assert len(data) == up["size"], (len(data), up["size"])
            STORE["new_roms"][(up["slug"], up["name"])] = data
            return self.send(201, b"", "text/plain")
        if u.path.startswith("/api/roms/upload/") and u.path.endswith("/cancel"):
            SEEN["cancels"] += 1
            return self.send(204, b"", "text/plain")
        if u.path == "/api/platforms":
            slug = json.loads(raw)["fs_slug"]
            name = dict(SUPPORTED)[slug]
            PLATFORMS.append({"id": len(PLATFORMS) + 1, "fs_slug": slug, "slug": slug, "display_name": name})
            return self.send(201, PLATFORMS[-1])
        if u.path == "/api/tasks/scan":
            body = json.loads(raw)
            SEEN["scans"] += 1
            for (slug, name), data in STORE["new_roms"].items():
                if not any(r["fs_name"] == name for r in ROMS):
                    ROMS.append({"id": 100 + len(ROMS), "name": Path(name).stem, "fs_name": name, "fs_size_bytes": len(data),
                                 "platform_fs_slug": slug, "platform_display_name": slug.upper()})
            return self.send(200, {"task_id": "scan-1", "status": "queued", "scan": body})
        q = parse_qs(u.query)
        assert u.path == "/api/saves" and q["emulator"][0] in ("mupen64plus_next", "citra", "soh", "2ship"), self.path
        msg = BytesParser(policy=HTTP).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + raw
        )
        part = [p for p in msg.iter_parts() if p.get_param("name", header="content-disposition") == "saveFile"][0]
        sid = len(STORE["saves"]) + 1
        STORE["saves"].append({
            "id": sid, "rom_id": int(q["rom_id"][0]), "file_name": part.get_filename(),
            "file_extension": part.get_filename().rsplit(".", 1)[-1],
            "updated_at": f"2026-11-14T12:00:{sid:02d}Z", "download_path": f"/api/raw/x/{sid}",
            "missing_from_fs": False, "emulator": q["emulator"][0],
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

def blocked_body(name, method, path, body):
    try:
        c._request(method, path, body=body, content_type="application/json")
    except romm.RommError as e:
        return check(name, str(e).startswith("Blocked"))
    raise SystemExit(f"FAILED: {name} (request was sent)")


UID = "00000000-0000-4000-8000-000000000999"
blocked_body("ROM upload with overwrite refused", "POST", "/api/roms/upload/start",
             json.dumps({"filename": "x.gba", "overwrite": True}).encode())
blocked_body("ROM upload into an existing ROM refused", "POST", "/api/roms/upload/start",
             json.dumps({"filename": "x.gba", "rom_id": 7}).encode())
blocked_body("full rescan refused", "POST", "/api/tasks/scan", json.dumps({"type": "complete", "platforms": [1]}).encode())
blocked_body("scan of everything refused", "POST", "/api/tasks/scan", json.dumps({"type": "quick"}).encode())
blocked_body("platform with more than a folder name refused", "POST", "/api/platforms",
             json.dumps({"fs_slug": "snes", "name": "x"}).encode())
blocked_body("platform with a path for a name refused", "POST", "/api/platforms",
             json.dumps({"fs_slug": "../roms"}).encode())
blocked_body("PUT outside an upload refused", "PUT", "/api/roms/upload/not-an-upload", b"x")
blocked_body("POST /api/roms/delete still refused", "POST", "/api/roms/delete", json.dumps({"roms": [7]}).encode())
blocked_body("PUT /api/roms/{id} still refused", "PUT", "/api/roms/7", b"{}")
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

# ---- Ship of Harkinian (a PC port of Ocarina of Time) ---------------------------
oot3d = games.Game({"id": 50, "name": "The Legend of Zelda: Ocarina of Time 3D", "fs_name": "oot3d.3ds",
                    "platform_fs_slug": "3ds"}, app.config)
mq = games.Game({"id": 51, "name": "The Legend of Zelda: Ocarina of Time - Master Quest", "fs_name": "mq.iso",
                 "platform_fs_slug": "ngc"}, app.config)
check("SoH offered for Ocarina of Time (N64/GC) only", ports.player_choices(oot) == ["soh", "retroarch"]
      and ports.player_choices(mq) == ["soh", "retroarch"] and ports.player_choices(sm64) == []
      and ports.player_choices(oot3d) == [])
check("Master Quest has its own archive", ports.SHIP_OF_HARKINIAN.archive_for(mq) == "oot-mq.o2r" and ports.SHIP_OF_HARKINIAN.archive_for(oot) == "oot.o2r")
check("SoH not set up: RetroArch stays the default", app.default_player(oot) == "retroarch"
      and "player" not in app.state.get("7", {}))
with contextlib.redirect_stdout(io.StringIO()):
    got = app.choose_player(sm64, "soh")
check("other games ignore --with soh", got == "retroarch")

soh_dir = tmp / "SoH"
soh_dir.mkdir()
soh_exe = soh_dir / "soh.exe"
soh_exe.write_bytes(b"")
app.config["soh"] = str(soh_exe)
real_windows = system.IS_WINDOWS
system.IS_WINDOWS = True
check("SoH data folder on Windows: the program's folder", ports.SHIP_OF_HARKINIAN.data_dir(app.config) == soh_dir)
system.IS_WINDOWS = False
os.environ["XDG_DATA_HOME"] = str(tmp / "xdg")
check("SoH data folder on Linux: ~/.local/share/soh (XDG)", ports.SHIP_OF_HARKINIAN.data_dir(app.config) == tmp / "xdg" / "soh")
(soh_dir / "shipofharkinian.json").write_text("{}")
check("SoH data folder on Linux: the program's folder if SoH's files are there", ports.SHIP_OF_HARKINIAN.data_dir(app.config) == soh_dir)
_, _, env = ports.SHIP_OF_HARKINIAN.launch(app.config, oot, tmp / "x.z64")
check("SoH on Linux: SHIP_HOME points at the data folder", env["SHIP_HOME"] == str(soh_dir))
system.IS_WINDOWS = real_windows
del os.environ["XDG_DATA_HOME"]
check("SoH set up: it's the default for Ocarina of Time", app.default_player(oot) == "soh")
check("SoH path kept in this system's config section",
      config.config_for_disk(dict(app.config, **{system.OS_KEY: {}}))[system.OS_KEY]["soh"] == str(soh_exe))

soh_saves = soh_dir / "Save"
soh_saves.mkdir()
(soh_saves / "file1.sav").write_text('{"slot": 1}')
(soh_saves / "global.sav").write_text('{"global": 1}')
(soh_saves / "file2-1763300000.bak").write_text("SoH's own backup")
(soh_saves / "file1.sav.temp").write_text("half written")
oot_rom = oot.find_local_rom()
s_soh = app.save_for(oot, oot_rom, "soh")
check("SoH save: the .sav files only", [n for n, _ in s_soh.files()] == ["file1.sav", "global.sav"]
      and s_soh.upload_name == f"{oot_rom.stem} (Ship of Harkinian).zip")
check("SoH save: zip hashes like the folder", s_soh.hash_of(s_soh.read()) == s_soh.hash())
with contextlib.redirect_stdout(io.StringIO()):
    got = app.upload(oot, s_soh)
check("SoH save uploaded as emulator soh", got and STORE["saves"][-1]["emulator"] == "soh")
check("SoH sync tracked apart from RetroArch's", app.state["7"]["hash_soh"] == s_soh.hash()
      and app.state["7"]["hash"] == util.sha256(b"E" * 296960))
check("RetroArch still gets its .srm, not SoH's zip",
      saves.fetch_remote(c, oot, saves.FileSave(local_save)).data == b"E" * 296960)

# RomM got a newer SoH save (another computer): it replaces the .sav files, keeps SoH's own files.
c.upload_save(7, s_soh.upload_name, make_zip({"file1.sav": '{"slot": 1, "hearts": 9}', "global.sav": "{}",
                                             "file3.sav": '{"slot": 3}', "../evil.sav": "x"}), "soh")
STORE["saves"][-1]["file_name"] = s_soh.upload_name
got = None
with contextlib.redirect_stdout(io.StringIO()):
    try:
        app.play(oot, launch=False, player="soh")
    except romm.RommError as e:
        got = str(e)
check("SoH save zip with an unsafe path refused", got and "unsafe" in got
      and (soh_saves / "file1.sav").read_text() == '{"slot": 1}')
STORE["files"][len(STORE["saves"])] = make_zip({"file1.sav": '{"slot": 1, "hearts": 9}', "global.sav": "{}",
                                                "file3.sav": '{"slot": 3}'})
with contextlib.redirect_stdout(io.StringIO()):
    app.play(oot, launch=False, player="soh")
check("newer SoH save from RomM placed", (soh_saves / "file1.sav").read_text() == '{"slot": 1, "hearts": 9}'
      and (soh_saves / "file3.sav").read_text() == '{"slot": 3}'
      and (soh_saves / "global.sav").read_text() == "{}")
check("SoH's own .bak and .temp files untouched", (soh_saves / "file2-1763300000.bak").read_text() == "SoH's own backup"
      and (soh_saves / "file1.sav.temp").read_text() == "half written" and not list(soh_saves.glob("*.romm-new")))
check("replaced SoH save backed up as a zip", any(
    b.suffix == ".zip" and zipfile.ZipFile(b).read("file1.sav") == b'{"slot": 1}'
    for b in (paths.BACKUP_DIR / "7").glob("*replaced-by-romm*")))
check("the choice of SoH is remembered", app.state["7"]["player"] == "soh")

# The question, when nothing says which: Enter keeps last time's, 2 is RetroArch.
ui.input = lambda prompt: ""
with contextlib.redirect_stdout(io.StringIO()):
    got = app.choose_player(oot)
check("play-with question: Enter takes last time's", got == "soh")
ui.input = lambda prompt: "2"
with contextlib.redirect_stdout(io.StringIO()):
    got = app.choose_player(oot)
check("play-with question: RetroArch picked and remembered", got == "retroarch"
      and app.default_player(oot) == "retroarch")
del ui.input
app.state["7"]["player"] = "soh"


class FakeSoh:
    def __init__(self, args, **kw):
        launched.append((args, kw))
        (soh_saves / "file2.sav").write_text('{"slot": 2}')

    def wait(self):
        return 0


launched.clear()
subprocess.Popen = FakeSoh
out = io.StringIO()
with contextlib.redirect_stdout(out):
    app.play(oot, player="soh")
subprocess.Popen = real_popen
args, kw = launched[0]
check("SoH started in its folder, given the ROM to make oot.o2r", args == [str(soh_exe), str(oot_rom)]
      and kw["cwd"] == str(soh_dir) and "oot.o2r" in out.getvalue())
newest = saves.fetch_remote(c, oot, app.save_for(oot, oot_rom, "soh"))
check("SoH save uploaded after playing", "Save uploaded to RomM." in out.getvalue()
      and "file2.sav" in zipfile.ZipFile(io.BytesIO(newest.data)).namelist()
      and app.state["7"]["hash_soh"] == s_soh.hash())
(soh_dir / "oot.o2r").write_bytes(b"assets")
launched.clear()
subprocess.Popen = FakeSoh
with contextlib.redirect_stdout(io.StringIO()):
    app.play(oot, player="soh")
subprocess.Popen = real_popen
check("with oot.o2r made, SoH starts without the ROM", launched[0][0] == [str(soh_exe)])

# A failed SoH upload is retried as SoH's save, even after switching to RetroArch.
(soh_saves / "file2.sav").write_text('{"slot": 2, "rupees": 99}')
FLAGS["fail_upload"] = True
with contextlib.redirect_stdout(io.StringIO()):
    app.upload(oot, s_soh)
FLAGS["fail_upload"] = False
app.state["7"]["player"] = "retroarch"
with contextlib.redirect_stdout(io.StringIO()):
    app.retry_pending(library)
check("pending SoH upload retried as SoH's save", app.state["7"]["pending_upload"] is False
      and app.state["7"]["hash_soh"] == s_soh.hash()
      and saves.fetch_remote(c, oot, saves.FileSave(local_save)).data == b"E" * 296960)
app.state["7"]["player"] = "soh"
util.save_json(paths.STATE_FILE, app.state)
del app.config["soh"]

# Downloading SoH: the latest release from (a fake) GitHub, checked, into soh/<system>/.
ports.GITHUB_API = ports.GITHUB = base.rstrip("/")
real_home, real_os = paths.APP_HOME, system.OS_KEY
paths.APP_HOME = tmp
win_zip = make_zip({"SoH-Dewey-Bravo-Win64/soh.exe": b"MZ soh", "SoH-Dewey-Bravo-Win64/soh.o2r": b"port assets",
                    "SoH-Dewey-Bravo-Win64/readme.txt": b"hi"})
appimage = b"\x7fELF appimage"


def gh_release(files, digests=None):
    GH["files"] = files
    GH["release"] = {"tag_name": "9.3.1", "name": "Dewey Bravo 9.3.1", "assets": [
        {"name": name, "size": len(data), "digest": "sha256:" + (digests or {}).get(name, util.sha256(data)),
         "browser_download_url": f"{base}HarbourMasters/Shipwright/releases/download/9.3.1/{name}"}
        for name, data in files.items()] + [{"name": "SoH-Dewey-Bravo-Mac.zip", "size": 1,
                                             "browser_download_url": f"{base}x/Mac.zip"}]}


gh_release({"SoH-Dewey-Bravo-Win64.zip": win_zip, "SoH-Dewey-Bravo.appimage": appimage})
system.OS_KEY = "windows"
ui.input = lambda prompt: "1"  # "Download it for me"
out = io.StringIO()
with contextlib.redirect_stdout(out):
    got = app.setup_port(ports.SHIP_OF_HARKINIAN)
del ui.input
installed = tmp / "soh" / "windows" / "soh.exe"
check("SoH downloaded and installed (Windows zip)", got and installed.read_bytes() == b"MZ soh"
      and (installed.parent / "soh.o2r").read_bytes() == b"port assets" and "9.3.1" in out.getvalue())
check("SoH install: checked, no leftovers", "checked against GitHub's checksum" in out.getvalue()
      and not (tmp / "soh" / "windows.part").exists())
check("downloaded SoH stored relative to the launcher folder", app.config["soh"] == "soh/windows/soh.exe"
      and ports.SHIP_OF_HARKINIAN.program(app.config) == installed and ports.SHIP_OF_HARKINIAN.installed(app.config))
check("downloaded SoH keeps its data beside it", ports.SHIP_OF_HARKINIAN.data_dir(app.config) == installed.parent)
n = SEEN["soh_downloads"]
(installed.parent / "Save").mkdir()
(installed.parent / "Save" / "file1.sav").write_text("mine")
with contextlib.redirect_stdout(io.StringIO()):
    got = app.download_port(ports.SHIP_OF_HARKINIAN)
check("SoH already installed: not downloaded again, nothing replaced", got and SEEN["soh_downloads"] == n
      and installed.read_bytes() == b"MZ soh" and (installed.parent / "Save" / "file1.sav").read_text() == "mine")
check("an installed SoH is found by setup", ports.SHIP_OF_HARKINIAN.find({}) == "soh/windows/soh.exe")

system.OS_KEY = "linux"
real_windows, system.IS_WINDOWS = system.IS_WINDOWS, False
gh_release({"SoH-Dewey-Bravo.appimage": appimage}, digests={"SoH-Dewey-Bravo.appimage": "0" * 64})
try:
    ports.SHIP_OF_HARKINIAN.install(ports.SHIP_OF_HARKINIAN.latest_release())
    raise SystemExit("FAILED: SoH download with a wrong checksum installed")
except ports.PortInstallError as e:
    check("SoH download with a wrong checksum refused, nothing written", "checksum" in str(e)
          and not (tmp / "soh" / "linux").exists())
gh_release({"SoH-Dewey-Bravo.appimage": appimage})
linux_program = ports.SHIP_OF_HARKINIAN.install(ports.SHIP_OF_HARKINIAN.latest_release())
check("SoH downloaded (Linux AppImage)", linux_program == tmp / "soh" / "linux" / "SoH-Dewey-Bravo.appimage"
      and linux_program.read_bytes() == appimage
      and (system.IS_WINDOWS or os.access(linux_program, os.X_OK)))
check("Linux: a downloaded SoH keeps its data beside it",
      ports.SHIP_OF_HARKINIAN.data_dir({"soh": "soh/linux/SoH-Dewey-Bravo.appimage"}) == linux_program.parent)
system.IS_WINDOWS = real_windows

system.OS_KEY = "windows"
shutil_tree = tmp / "soh" / "windows"
import shutil
shutil.rmtree(shutil_tree)
gh_release({"SoH-Dewey-Bravo-Win64.zip": make_zip({"../evil.exe": b"x", "soh.exe": b"MZ"})})
try:
    ports.SHIP_OF_HARKINIAN.install(ports.SHIP_OF_HARKINIAN.latest_release())
    raise SystemExit("FAILED: SoH zip with an unsafe path installed")
except ports.PortInstallError as e:
    check("SoH zip with an unsafe path refused", "unsafe" in str(e) and not shutil_tree.exists()
          and not (tmp / "soh" / "evil.exe").exists())
gh_release({"SoH-Dewey-Bravo-Win64.zip": win_zip})
GH["release"]["assets"][0]["browser_download_url"] = "https://example.com/HarbourMasters/soh.zip"
try:
    ports.SHIP_OF_HARKINIAN.latest_release()
    raise SystemExit("FAILED: SoH from another address accepted")
except ports.PortInstallError as e:
    check("SoH only downloaded from its GitHub releases", "Unexpected download address" in str(e))
system.OS_KEY = "macos"
try:
    ports.SHIP_OF_HARKINIAN.latest_release()
    raise SystemExit("FAILED: SoH offered where there's no build")
except ports.PortInstallError as e:
    check("no SoH download offered where there's no build", "no download" in str(e))
system.OS_KEY = real_os
paths.APP_HOME = real_home
del app.config["soh"]

# ---- 2 Ship 2 Harkinian (a PC port of Majora's Mask) ----------------------------
TWO = ports.TWO_SHIP
mm = games.Game({"id": 3, "name": "The Legend of Zelda: Majora's Mask", "platform_fs_slug": "n64",
                 "fs_name": "Legend of Zelda, The_ Majora's Mask.zip"}, app.config)
mm3d = games.Game({"id": 52, "name": "The Legend of Zelda: Majora's Mask 3D", "fs_name": "mm3d.3ds",
                   "platform_fs_slug": "3ds"}, app.config)
check("2S2H offered for Majora's Mask (N64) only", ports.player_choices(mm) == ["2ship", "retroarch"]
      and ports.port_for(mm) is TWO and ports.player_choices(mm3d) == []
      and ports.port_for(oot) is ports.SHIP_OF_HARKINIAN)
check("2S2H not set up: RetroArch stays the default", app.default_player(mm) == "retroarch")
with contextlib.redirect_stdout(io.StringIO()):
    got = app.choose_player(mm, "soh", launch=False)
check("--with soh doesn't apply to Majora's Mask", got == "retroarch")

mm_rom = mm.rom_dest
mm_rom.parent.mkdir(parents=True, exist_ok=True)
mm_rom.write_bytes(make_zip({"Majora's Mask.z64": b"\x80\x37\x12\x40 MM ROM", "readme.txt": b"hi"}))
two_dir = tmp / "2Ship"
two_dir.mkdir()
two_exe = two_dir / "2ship.exe"
two_exe.write_bytes(b"")
app.config["2ship"] = str(two_exe)
real_windows = system.IS_WINDOWS
system.IS_WINDOWS = False
os.environ["XDG_DATA_HOME"] = str(tmp / "xdg")
check("2S2H data folder on Linux: ~/.local/share/2ship (XDG)", TWO.data_dir(app.config) == tmp / "xdg" / "2ship")
(two_dir / "2ship2harkinian.json").write_text("{}")
check("2S2H data folder on Linux: the program's folder if its files are there", TWO.data_dir(app.config) == two_dir)
system.IS_WINDOWS = True
check("2S2H data folder on Windows: the program's folder", TWO.data_dir(app.config) == two_dir)
system.IS_WINDOWS = real_windows
del os.environ["XDG_DATA_HOME"]
check("2S2H set up: it's the default for Majora's Mask", app.default_player(mm) == "2ship"
      and app.default_player(oot) == "soh")
check("2S2H path kept in this system's config section",
      config.config_for_disk(dict(app.config, **{system.OS_KEY: {}}))[system.OS_KEY]["2ship"] == str(two_exe))

two_saves = two_dir / "saves"
two_saves.mkdir()
(two_saves / "file1.json").write_text('{"slot": 1}')
(two_saves / "file1backup.json").write_text('{"slot": 1, "backup": true}')
(two_saves / "file2_invalid_1763300000.json").write_text("2S2H couldn't read it")
(two_saves / "global.json").write_text('{"old": "options"}')
s_two = app.save_for(mm, mm_rom, "2ship")
check("2S2H save: the slot files and their backups only",
      [n for n, _ in s_two.files()] == ["file1.json", "file1backup.json"]
      and s_two.upload_name == f"{mm_rom.stem} (2 Ship 2 Harkinian).zip" and s_two.emulator == "2ship")
check("2S2H save: zip hashes like the folder", s_two.hash_of(s_two.read()) == s_two.hash())
check("SoH's save is still SoH's", ports.SHIP_OF_HARKINIAN.save({"soh": str(soh_exe)}, oot_rom).emulator == "soh")


class FakeTwoShip:
    def __init__(self, args, **kw):
        launched.append((args, kw, Path(args[-1]).read_bytes() if len(args) > 1 else None))
        (two_saves / "file2.json").write_text('{"slot": 2}')

    def wait(self):
        return 0


launched.clear()
subprocess.Popen = FakeTwoShip
out = io.StringIO()
with contextlib.redirect_stdout(out):
    app.play(mm, player="2ship")
subprocess.Popen = real_popen
args, kw, rom_seen = launched[0]
unpacked = two_dir / ports.ROM_FOLDER / "Majora's Mask.z64"
check("2S2H started in its folder with the ROM unzipped (it can't read zips)",
      args == [str(two_exe), str(unpacked)] and kw["cwd"] == str(two_dir)
      and rom_seen == b"\x80\x37\x12\x40 MM ROM" and "mm.o2r" in out.getvalue())
check("unzipped ROM removed after the game closed, the zip kept", not (two_dir / ports.ROM_FOLDER).exists()
      and mm_rom.is_file())
newest = saves.fetch_remote(c, mm, app.save_for(mm, mm_rom, "2ship"))
check("2S2H save uploaded after playing, as emulator 2ship", "Save uploaded to RomM." in out.getvalue()
      and STORE["saves"][-1]["emulator"] == "2ship"
      and sorted(zipfile.ZipFile(io.BytesIO(newest.data)).namelist()) == ["file1.json", "file1backup.json", "file2.json"]
      and app.state["3"]["hash_2ship"] == s_two.hash() and app.state["3"]["player"] == "2ship")
(two_dir / "mm.o2r").write_bytes(b"assets")
launched.clear()
subprocess.Popen = FakeTwoShip
with contextlib.redirect_stdout(io.StringIO()):
    app.play(mm, player="2ship")
subprocess.Popen = real_popen
check("with mm.o2r made, 2S2H starts without the ROM", launched[0][0] == [str(two_exe)])

# RomM's newer 2S2H save replaces the slot files; 2S2H's own other files stay.
c.upload_save(3, s_two.upload_name, make_zip({"file1.json": '{"slot": 1, "masks": 24}',
                                              "file3.json": '{"slot": 3}'}), "2ship")
STORE["saves"][-1]["file_name"] = s_two.upload_name
with contextlib.redirect_stdout(io.StringIO()):
    app.play(mm, launch=False, player="2ship")
check("newer 2S2H save from RomM placed", (two_saves / "file1.json").read_text() == '{"slot": 1, "masks": 24}'
      and (two_saves / "file3.json").read_text() == '{"slot": 3}' and not (two_saves / "file2.json").exists())
check("2S2H's invalid-save copies and old global.json untouched",
      (two_saves / "file2_invalid_1763300000.json").read_text() == "2S2H couldn't read it"
      and (two_saves / "global.json").read_text() == '{"old": "options"}')

# A failed 2S2H upload is retried as 2S2H's save.
(two_saves / "file3.json").write_text('{"slot": 3, "day": 2}')
FLAGS["fail_upload"] = True
with contextlib.redirect_stdout(io.StringIO()):
    app.upload(mm, s_two)
FLAGS["fail_upload"] = False
check("failed 2S2H upload marked as 2S2H's", app.state["3"]["pending_player"] == "2ship")
app.state["3"]["player"] = "retroarch"
with contextlib.redirect_stdout(io.StringIO()):
    app.retry_pending([mm])
check("pending 2S2H upload retried as 2S2H's save", app.state["3"]["pending_upload"] is False
      and app.state["3"]["hash_2ship"] == s_two.hash())
del app.config["2ship"]

# Downloading 2S2H: from its own GitHub organisation; Windows and Linux both come as zips.
real_home, real_os = paths.APP_HOME, system.OS_KEY
paths.APP_HOME = tmp
system.OS_KEY = "windows"
two_win = make_zip({"2ship.exe": b"MZ 2ship", "2ship.o2r": b"port assets", "debug/2ship.pdb": b"symbols",
                    "readme.txt": b"hi"})
GH["files"] = {"2Ship-Battler-Bravo-Win64.zip": two_win,
               "2Ship-Battler-Bravo-Linux.zip": make_zip({"2ship.appimage": appimage, "readme.txt": b"hi"})}
GH["release"] = {"tag_name": "5.0.1", "name": "Battler Bravo 5.0.1", "assets": [
    {"name": name, "size": len(data), "digest": "sha256:" + util.sha256(data),
     "browser_download_url": f"{base}2ship2harkinian/2ship2harkinian/releases/download/5.0.1/{name}"}
    for name, data in GH["files"].items()]}
ui.input = lambda prompt: "1"  # "Download it for me"
out = io.StringIO()
with contextlib.redirect_stdout(out):
    got = app.setup_port(TWO)
del ui.input
installed = tmp / "2ship" / "windows" / "2ship.exe"
check("2S2H downloaded from its own GitHub page", got and GH["asked"] == "/repos/2ship2harkinian/2ship2harkinian/releases/latest"
      and "5.0.1" in out.getvalue())
check("2S2H installed (Windows zip), debug symbols left out", installed.read_bytes() == b"MZ 2ship"
      and (installed.parent / "2ship.o2r").read_bytes() == b"port assets"
      and not (installed.parent / "debug").exists() and app.config["2ship"] == "2ship/windows/2ship.exe"
      and TWO.data_dir(app.config) == installed.parent)
system.OS_KEY = "linux"
real_windows, system.IS_WINDOWS = system.IS_WINDOWS, False
linux_program = TWO.install(TWO.latest_release())
check("2S2H downloaded (Linux: the AppImage from its zip)",
      linux_program == tmp / "2ship" / "linux" / "2ship.appimage" and linux_program.read_bytes() == appimage
      and (real_windows or os.access(linux_program, os.X_OK))
      and TWO.data_dir({"2ship": "2ship/linux/2ship.appimage"}) == linux_program.parent)
system.IS_WINDOWS = real_windows
system.OS_KEY = real_os
paths.APP_HOME = real_home
del app.config["2ship"]

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
snes_game = games.Game({"fs_name": "Chrono.sfc", "platform_fs_slug": "snes", "id": 2}, cfg3)
check("SNES: Snes9x, save synced as .srm", snes_game.core == "snes9x" and snes_game.syncs_saves
      and snes_game.save_path(tmp / "Chrono.sfc") == ra / "saves" / "Snes9x" / "Chrono.srm")
check("Genesis: Genesis Plus GX folder", games.Game({"fs_name": "s.md", "platform_fs_slug": "genesis", "id": 3}, cfg3)
      .save_dir() == ra / "saves" / "Genesis Plus GX")
psp = games.Game({"fs_name": "g.iso", "platform_fs_slug": "psp", "id": 4}, cfg3)
check("PSP: a core, but its own saves aren't synced", psp.core == "ppsspp" and not psp.syncs_saves)
check("every platform's core has a save folder name",
      all(retroarch.core_id(v["core"]) in retroarch.KNOWN_CORE_FOLDERS for v in config.DEFAULT_PLATFORMS.values()))

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

facts = games.game_facts({"metadatum": {"first_release_date": 956793600000, "genres": ["Adventure", "Puzzle"],
                                         "average_rating": 88.02, "game_modes": ["Single player"]},
                           "regions": ["USA"]})
check("game facts from RomM's metadata", facts == [("Released", "2000"), ("Genre", "Adventure, Puzzle"),
                                                   ("Rating", "88 / 100"), ("Modes", "Single player"), ("Region", "USA")])
check("no metadata, no facts", games.game_facts({"metadatum": {"genres": [], "first_release_date": None}}) == [])
check("platform guessed from the file", games.guess_platform("Game (U).Z64") == "n64" and games.guess_platform("x.txt") is None)

opts = [("snes", "Super Nintendo Entertainment System"), ("nes", "Nintendo Entertainment System"),
        ("n64", "Nintendo 64")]
check("search: words in any order", [k for k, _ in ui.filter_options(opts, "entertainment super")] == ["snes"])
check("search: an exact key comes first", [k for k, _ in ui.filter_options(opts, "nes")][0] == "nes")
many = [(f"p{i}", f"Platform {i}") for i in range(40)] + opts
answers = ["nintendo", "2"]
ui.input = lambda prompt: answers.pop(0)
with contextlib.redirect_stdout(io.StringIO()):
    check("long choice: search, then pick a number", ui.choose("Which?", many, "Platform", cancel=True) == "nes")
answers = [""]
with contextlib.redirect_stdout(io.StringIO()):
    check("long choice: Enter takes the suggestion", ui.choose("Which?", many, "Platform", default="n64") == "n64")
del ui.input

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
    check("known address shown under the box, not asked", window.address_line is not None
          and any(base.split("//")[1].rstrip("/") in w.cget("text") for w in window.address_line.winfo_children()))
    window.edit_address()
    window.v_url.set("https://other.example")
    window.finish_address_edit(False)  # what Escape does
    check("Escape undoes an address edit", window.v_url.get().rstrip("/") == base.rstrip("/")
          and isinstance(window.address_line.winfo_children()[0], gui.tk.Label))
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
    check("Ocarina of Time: play-with chips, last choice selected",
          [c.text for c in window.player_row.winfo_children()] == ["Ship of Harkinian", "RetroArch"]
          and window.v_player.get() == "soh")
    window.player_row.winfo_children()[1].invoke()
    check("play-with chip picks RetroArch", window.v_player.get() == "retroarch")
    window.select(ds)
    check("DS game: layout choices shown", window.v_layout.get() in ("main", "both", "corner")
          and len(window.layout_row.winfo_children()) == 3)
    window.layout_row.winfo_children()[0].invoke()
    check("layout chip picks the layout", window.v_layout.get() == "main")
    wide, big = gui.tk.PhotoImage(width=204, height=142), gui.tk.PhotoImage(width=408, height=408)
    fitted = [window.fit_image(i, 204, 204) for i in (wide, big)]
    check("covers fit whole, never cut", [(i.width(), i.height()) for i in fitted] == [(204, 142), (204, 204)])
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
    window.select(oot)
    check("online: the Add game button is there", window.add_button.text.endswith("Add game"))

    def pick_from_list():
        text, listbox, shown, finish = window.list_dialog
        text.set("super nintendo")
        window.list_picked = [label for _, label in shown]
        finish(True)

    window.root.after(300, pick_from_list)
    try:
        got = window.ask_from_list("Platform?", many, default="n64", cancel=True)
        check("window: long choice searched in a list", got == "snes"
              and window.list_picked == ["Super Nintendo Entertainment System"])
    except gui.tk.TclError as exc:  # a hidden window can't always grab the keyboard
        print(f"skipped: list dialog ({exc})")
    # Settings: Ship of Harkinian can be downloaded from there, and cleared again.
    def find_button(widget, text):
        for child in widget.winfo_children():
            if isinstance(child, gui.tk.Button) and child.cget("text") == text:
                return child
            found = find_button(child, text)
            if found:
                return found
        return None

    gh_release({"SoH-Dewey-Bravo-Win64.zip": win_zip, "SoH-Dewey-Bravo.appimage": appimage})
    window.launcher.config.pop("soh", None)
    real_home, paths.APP_HOME = paths.APP_HOME, tmp / "gui-home"  # downloads go here, not the real folder
    seen = {}

    def download_from_settings():
        seen.update(note=window.port_notes["soh"].cget("text"), download=window.port_downloads["soh"],
                    two_note=window.port_notes["2ship"].cget("text"), two_download=window.port_downloads["2ship"])
        window.port_downloads["soh"].invoke()  # closes the settings and starts the download

    def clear_in_settings():
        seen.update(note2=window.port_notes["soh"].cget("text"), download2=window.port_downloads["soh"])
        window.port_entries["soh"].delete(0, "end")
        find_button(window.port_entries["soh"].winfo_toplevel(), "Save").invoke()

    try:
        window.root.after(300, download_from_settings)
        window.show_settings()
        check("settings: SoH not set up, Download offered", seen["download"] is not None
              and "Not set up" in seen["note"])
        check("settings: 2S2H has its own field and Download", seen["two_download"] is not None
              and "Not set up" in seen["two_note"] and "2ship" in seen["two_note"])
        check("settings: Download installs SoH", pump(lambda: ports.SHIP_OF_HARKINIAN.installed(window.launcher.config)
                                                     and not window.busy)
              and "installed" in window.activity.get("1.0", "end"))
        window.root.after(300, clear_in_settings)
        window.show_settings()
        check("settings: installed SoH shown as ready, no Download", seen["download2"] is None
              and seen["note2"].startswith("Ready"))
        check("settings: clearing the field stops using SoH", "soh" not in window.launcher.config
              and "soh" not in util.load_json(paths.CONFIG_FILE, {}).get(system.OS_KEY, {}))
    except gui.tk.TclError as exc:  # a hidden window can't always grab the keyboard
        print(f"skipped: settings dialog ({exc})")
    check("settings: SoH downloaded into the test folder", (tmp / "gui-home" / "soh" / system.OS_KEY).is_dir())
    paths.APP_HOME = real_home
    ui.set_ui(ui.TerminalUI())
    window.root.destroy()

# Offline: only games on this computer are listed.
off = launcher.Launcher()
off.offline = True
check("offline lists local games only", [g.id for g in off.library()] == [7])

# Adding games: chunked upload, quick scan, copy in the ROM folder; never a replace.
mine = tmp / "My Homebrew.gba"
mine.write_bytes(bytes(range(256)) * 5000)  # 1.28 MB, three chunks of 0.5 MB
c.upload_rom(2, mine, chunk_size=1 << 19)
check("ROM uploaded in equal chunks", STORE["new_roms"][("gba", "My Homebrew.gba")] == mine.read_bytes()
      and len(STORE["uploads"]["00000000-0000-4000-8000-000000000000"]["chunks"]) == 3)
try:
    c.upload_rom(2, mine)
    raise SystemExit("FAILED: second upload of the same name went through")
except romm.RommError as e:
    check("same file name refused by RomM", "already exists" in str(e))
ROMS[:] = [r for r in ROMS if r["fs_name"] != "My Homebrew.gba"]
STORE["new_roms"].clear()
ui.input = lambda prompt: ""  # the platform question: Enter takes the one the .gba suggests
with contextlib.redirect_stdout(io.StringIO()):
    added = app.add_rom(mine)
del ui.input
check("add_rom: uploaded, scanned, and in the library", added and SEEN["scans"] >= 1
      and any(r["fs_name"] == "My Homebrew.gba" and r["platform_fs_slug"] == "gba" for r in ROMS))
check("add_rom: copied into the ROM folder", (config.roms_root(app.config) / "gba" / "My Homebrew.gba").read_bytes()
      == mine.read_bytes())
util.save_json(paths.LIBRARY_CACHE, {"roms": ROMS})
with contextlib.redirect_stdout(io.StringIO()):
    check("add_rom: already in the library, nothing sent", app.add_rom(mine, platform="gba") is False)
    check("add_rom: platform RomM doesn't have", app.add_rom(mine, platform="psp") is False)

snes = tmp / "My Game.sfc"
snes.write_bytes(b"SNES" * 1000)
ui.input = lambda prompt: ""  # Enter: the platform .sfc suggests (Super Nintendo, not on this RomM yet)
with contextlib.redirect_stdout(io.StringIO()):
    added = app.add_rom(snes)
del ui.input
check("add_rom: a platform new in your RomM is created first", added
      and any(p["fs_slug"] == "snes" for p in PLATFORMS) and STORE["new_roms"][("snes", "My Game.sfc")] == snes.read_bytes())

check("still nothing destructive reached the server", SEEN["destructive"] == 0)
print("\nall tests passed")
