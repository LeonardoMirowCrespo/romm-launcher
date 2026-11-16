"""The RomM server's REST API.

Safety: RommClient checks every request before it's sent (`_check`). It may only read
from the RomM server, upload saves, and add new ROM files (never replacing one): no
DELETE/PATCH, no other POSTs or PUTs, no other servers. See ALLOWED_WRITES.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .util import parse_time


class RommError(Exception):
    pass


class LoginError(RommError):
    pass


def _origin(url: str) -> tuple[str, str]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme.lower(), parts.netloc.lower()


class _SameServerRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only within the RomM server, so the login never goes elsewhere."""

    def __init__(self, origin: tuple[str, str]):
        super().__init__()
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _origin(newurl) != self.origin:
            raise RommError(f"Blocked: RomM redirected to another server ({newurl}).")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


UPLOAD_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _json_body(body) -> dict | None:
    try:
        value = json.loads(body) if body else None
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _new_rom_only(body) -> bool:
    """The start of a ROM upload: a new file in a platform folder. Never into an existing
    ROM's folder (rom_id, folder), never replacing a file (overwrite)."""
    value = _json_body(body)
    return value is not None and set(value) <= {"filename"} and isinstance(value.get("filename", ""), str)


def _quick_scan_only(body) -> bool:
    """A scan that only adds files RomM hasn't seen yet, on the platforms named."""
    value = _json_body(body)
    return (value is not None and set(value) <= {"type", "platforms"} and value.get("type") == "quick"
            and isinstance(value.get("platforms"), list) and bool(value["platforms"])
            and all(type(p) is int for p in value["platforms"]))


def _no_body(body) -> bool:
    return not body


def _new_platform_only(body) -> bool:
    """Creating a platform: only its folder name, a plain slug (RomM makes the folder and
    the platform's entry; an existing platform's folder is left as it is)."""
    value = _json_body(body)
    return (value is not None and set(value) == {"fs_slug"} and isinstance(value["fs_slug"], str)
            and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value["fs_slug"]) is not None)


class RommClient:
    TOKEN_SCOPES = "me.read roms.read platforms.read assets.read assets.write"
    ADD_ROM_SCOPES = TOKEN_SCOPES + " roms.write tasks.run platforms.write"
    # The launcher may only READ from RomM, UPLOAD saves and ADD new ROM files. Every
    # request is checked against this before it's sent (see _check), so nothing in the
    # launcher can delete or change anything else on the server: no DELETE/PATCH, and
    # POST/PUT only to these paths, with only these query parameters (so e.g. RomM's
    # "autocleanup" option for save uploads, which deletes older saves, can't be switched
    # on), and only with a body the check accepts (None: any body).
    #   (method, path pattern, allowed query parameters, body check)
    ALLOWED_WRITES = [
        ("POST", r"/api/saves", {"rom_id", "emulator"}, None),
        ("POST", r"/api/token", set(), None),
        ("POST", r"/api/roms/upload/start", set(), _new_rom_only),
        ("PUT", rf"/api/roms/upload/{UPLOAD_ID}", set(), None),  # one chunk of that file
        ("POST", rf"/api/roms/upload/{UPLOAD_ID}/(complete|cancel)", set(), _no_body),
        ("POST", r"/api/tasks/scan", set(), _quick_scan_only),
        ("POST", r"/api/platforms", set(), _new_platform_only),  # a new platform to add a game to
    ]

    def __init__(self, base_url: str, username: str, password: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.username, self.password = username, password
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.auth = f"Basic {token}"
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_SameServerRedirects(_origin(self.base)))

    def _url(self, path: str, params: dict | None) -> str:
        url = path if path.startswith("http") else f"{self.base}{path}"
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        return url

    def _check(self, method: str, url: str, body: bytes | None = None) -> None:
        """Refuse any request that isn't a read, a save upload or a new ROM for this RomM server."""
        if _origin(url) != _origin(self.base):
            raise RommError(f"Blocked: {url} is not on your RomM server ({self.base}).")
        if method == "GET":
            return
        parts = urllib.parse.urlsplit(url)
        prefix = urllib.parse.urlsplit(self.base).path.rstrip("/")
        path = parts.path[len(prefix):] if parts.path.startswith(prefix) else parts.path
        params = set(urllib.parse.parse_qs(parts.query, keep_blank_values=True))
        for allowed_method, pattern, allowed_params, body_ok in self.ALLOWED_WRITES:
            if (method == allowed_method and re.fullmatch(pattern, path) and params <= allowed_params
                    and (body_ok is None or body_ok(body))):
                return
        raise RommError(
            f"Blocked: {method} {path}{'?' + parts.query if parts.query else ''}. The launcher is only "
            "allowed to read from RomM, upload saves and add new ROM files."
        )

    def _open(self, method, path, params=None, body=None, content_type=None, retry=True, headers=None,
              scopes=None):
        url = self._url(path, params)
        self._check(method, url, body)
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", self.auth)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "romm-launcher")
        if content_type:
            req.add_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            req.add_header(name, value)
        try:
            return self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code == 403 and "csrf" in detail.lower() and retry and self.auth.startswith("Basic"):
                # Some RomM setups refuse Basic auth on writes; use an API token instead.
                self._get_token(scopes)
                return self._open(method, path, params, body, content_type, retry=False, headers=headers)
            if exc.code == 401:
                raise LoginError("RomM rejected the username or password.") from exc
            if exc.code == 403:
                raise RommError(f"RomM refused access to {path}: {detail}") from exc
            raise RommError(f"RomM answered {exc.code} for {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RommError(
                f"Can't reach RomM at {self.base} ({exc.reason}). Is the server up, and is this computer on its network (VPN)?"
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise RommError(f"RomM at {self.base} did not answer ({exc}).") from exc

    def _get_token(self, scopes: str | None = None) -> None:
        form = urllib.parse.urlencode(
            {
                "grant_type": "password",
                "username": self.username,
                "password": self.password,
                "scope": scopes or self.TOKEN_SCOPES,
            }
        ).encode()
        url = f"{self.base}/api/token"
        self._check("POST", url)
        req = urllib.request.Request(url, data=form, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                self.auth = f"Bearer {json.loads(resp.read())['access_token']}"
        except Exception as exc:
            raise RommError(f"RomM refused to issue an API token: {exc}") from exc

    def _request(self, method: str, path: str, **kw) -> bytes:
        try:
            with self._open(method, path, **kw) as resp:
                return resp.read()
        except OSError as exc:  # connection dropped mid-transfer
            raise RommError(f"Connection to RomM broke off ({exc}).") from exc

    def _json(self, method: str, path: str, **kw):
        return json.loads(self._request(method, path, **kw) or b"null")

    def check_login(self) -> str:
        """Return the logged-in username, or raise."""
        me = self._json("GET", "/api/users/me")
        if not isinstance(me, dict):
            raise LoginError("RomM rejected the username or password.")
        return me.get("username", "?")

    def list_roms(self, search: str | None = None) -> list[dict]:
        params = {
            "limit": 10000,
            "order_by": "name",
            "with_char_index": "false",
            "with_filter_values": "false",
            "with_rom_id_index": "false",
        }
        if search:
            params["search_term"] = search
        data = self._json("GET", "/api/roms", params=params)
        items = data.get("items", []) if isinstance(data, dict) else data
        return items or []

    def list_saves(self, rom_id: int) -> list[dict]:
        try:
            saves = self._json("GET", "/api/saves", params={"rom_id": rom_id})
            if isinstance(saves, list):
                return saves
        except RommError as exc:
            if " 404 " not in str(exc) and " 405 " not in str(exc):
                raise
        # Older RomM versions: saves are part of the ROM details.
        rom = self._json("GET", f"/api/roms/{rom_id}")
        return rom.get("user_saves", []) if isinstance(rom, dict) else []

    def newest_save(self, rom_id: int, extensions: list[str]) -> dict | None:
        exts = {e.lower().lstrip(".") for e in extensions}
        saves = [
            s
            for s in self.list_saves(rom_id)
            if not s.get("missing_from_fs")
            and (
                (s.get("file_extension") or "").lower().lstrip(".") in exts
                or Path(s.get("file_name") or "").suffix.lower().lstrip(".") in exts
            )
        ]
        if not saves:
            return None
        return max(
            saves,
            key=lambda s: parse_time(s.get("updated_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
        )

    def download_save(self, save: dict) -> bytes:
        try:
            return self._request("GET", f"/api/saves/{save['id']}/content")
        except RommError as exc:
            if not save.get("download_path") or (
                " 404 " not in str(exc) and " 405 " not in str(exc)
            ):
                raise
        # Older RomM versions: use the file's download path.
        path = save["download_path"]
        if not path.startswith("http"):
            origin = urllib.parse.urlsplit(self.base)
            path = f"{origin.scheme}://{origin.netloc}{path}"
        return self._request("GET", path)

    def upload_save(self, rom_id: int, filename: str, data: bytes, emulator: str) -> dict:
        boundary = uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="saveFile"; '
            f'filename="{filename.replace(chr(34), "")}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        result = self._json(
            "POST",
            "/api/saves",
            params={"rom_id": rom_id, "emulator": emulator},
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        return result if isinstance(result, dict) else {}

    # ----- adding games ---------------------------------------------------

    def list_platforms(self) -> list[dict]:
        platforms = self._json("GET", "/api/platforms")
        return platforms if isinstance(platforms, list) else []

    def list_supported_platforms(self) -> list[dict]:
        """Every platform RomM knows. The ones not on this server yet have no real id (-1)."""
        try:
            platforms = self._json("GET", "/api/platforms/supported")
        except RommError as exc:
            if " 404 " in str(exc) or " 405 " in str(exc):  # older RomM: only its own platforms
                return self.list_platforms()
            raise
        return platforms if isinstance(platforms, list) else []

    def add_platform(self, fs_slug: str) -> dict:
        """Create a platform on RomM (its folder and entry), to add a game to it."""
        platform = self._json("POST", "/api/platforms", body=json.dumps({"fs_slug": fs_slug}).encode(),
                              content_type="application/json", scopes=self.ADD_ROM_SCOPES)
        if not isinstance(platform, dict) or not isinstance(platform.get("id"), int) or platform["id"] < 1:
            raise RommError(f"RomM didn't create the platform {fs_slug} (it answered {platform!r}).")
        return platform

    def upload_rom(self, platform_id: int, path: Path, name: str | None = None, progress=None,
                   chunk_size: int = 8 << 20) -> None:
        """Add a ROM file to a platform on RomM, in chunks (RomM 5's chunked upload).

        RomM refuses a file name that's already in that platform's folder, and the safety
        lock never lets an upload into an existing ROM or with "overwrite" through, so
        nothing is replaced. The ROM shows up in the library after a scan (start_scan)."""
        name = name or path.name
        size = path.stat().st_size
        chunks = -(-size // chunk_size) if size else 0
        chunk_size = -(-size // chunks) if chunks else 0  # RomM expects equal chunks, the last one shorter
        start = self._json(
            "POST", "/api/roms/upload/start", body=json.dumps({"filename": name}).encode(),
            content_type="application/json", scopes=self.ADD_ROM_SCOPES,
            headers={"x-upload-platform": str(platform_id),
                     # the header can't carry other characters; the body's name wins anyway
                     "x-upload-filename": name.encode("latin-1", "replace").decode("latin-1"),
                     "x-upload-total-size": str(size), "x-upload-total-chunks": str(chunks)})
        upload_id = str((start or {}).get("upload_id", "")) if isinstance(start, dict) else ""
        if not re.fullmatch(UPLOAD_ID, upload_id):
            raise RommError(f"RomM didn't start the upload (it answered {start!r}).")
        try:
            done = 0
            with open(path, "rb") as f:
                for index in range(chunks):
                    data = f.read(chunk_size)
                    self._request("PUT", f"/api/roms/upload/{upload_id}", body=data,
                                  content_type="application/octet-stream", scopes=self.ADD_ROM_SCOPES,
                                  headers={"x-chunk-index": str(index)})
                    done += len(data)
                    if progress:
                        progress(done)
            self._request("POST", f"/api/roms/upload/{upload_id}/complete", scopes=self.ADD_ROM_SCOPES)
        except BaseException:
            try:  # leave no half upload behind on the server
                self._request("POST", f"/api/roms/upload/{upload_id}/cancel", scopes=self.ADD_ROM_SCOPES)
            except RommError:
                pass
            raise

    def start_scan(self, platform_id: int) -> dict:
        """Ask RomM for a quick scan of one platform: it only adds files it hasn't seen yet."""
        body = json.dumps({"type": "quick", "platforms": [platform_id]}).encode()
        task = self._json("POST", "/api/tasks/scan", body=body, content_type="application/json",
                          scopes=self.ADD_ROM_SCOPES)
        return task if isinstance(task, dict) else {}

    def wait_for_task(self, task_id: str, timeout: float = 120, every: float = 2) -> str:
        """The task's status once it has ended ("finished", "failed", ...), or "timeout"."""
        end = time.monotonic() + timeout
        while True:
            task = self._json("GET", f"/api/tasks/{urllib.parse.quote(task_id)}")
            status = task.get("status", "") if isinstance(task, dict) else ""
            if status in ("finished", "failed", "stopped", "canceled"):
                return status
            if time.monotonic() >= end:
                return "timeout"
            time.sleep(every)

    def download_asset(self, path: str) -> bytes:
        """A file RomM serves for a ROM, like its cover ("/assets/romm/resources/…")."""
        return self._request("GET", urllib.parse.quote(path, safe="/?=&:%+"))

    def download_rom(self, rom: dict, dest: Path, progress=None) -> None:
        """Download a ROM to `dest` (a .zip for multi-file ROMs), via a .part file."""
        name = rom["fs_name"] + (".zip" if rom.get("has_multiple_files") else "")
        path = f"/api/roms/{rom['id']}/content/{urllib.parse.quote(name)}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            with self._open("GET", path, params={"purpose": "play"}) as resp, open(part, "wb") as f:
                done = 0
                while chunk := resp.read(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done)
            part.replace(dest)
        except OSError as exc:  # connection dropped mid-transfer
            part.unlink(missing_ok=True)
            raise RommError(f"The download broke off ({exc}). Run it again to retry.") from exc
        except BaseException:
            part.unlink(missing_ok=True)
            raise
