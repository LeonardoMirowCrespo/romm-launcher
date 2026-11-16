"""The RomM server's REST API.

Safety: RommClient checks every request before it's sent (`_check`). It may only read
from the RomM server and upload saves: no DELETE/PUT/PATCH, no other POSTs, no other
servers. See ALLOWED_POSTS.
"""

from __future__ import annotations

import base64
import json
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


class RommClient:
    TOKEN_SCOPES = "me.read roms.read platforms.read assets.read assets.write"
    # The launcher may only READ from RomM and UPLOAD saves. Every request is checked
    # against this before it's sent (see _check), so nothing in the launcher can delete
    # or change anything else on the server: no DELETE/PUT/PATCH, no POST except these
    # paths, and only with these query parameters (so e.g. RomM's "autocleanup" option
    # for uploads, which deletes older saves, can't be switched on).
    ALLOWED_POSTS = {"/api/saves": {"rom_id", "emulator"}, "/api/token": set()}

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

    def _check(self, method: str, url: str) -> None:
        """Refuse any request that isn't a read or a save upload to this RomM server."""
        if _origin(url) != _origin(self.base):
            raise RommError(f"Blocked: {url} is not on your RomM server ({self.base}).")
        if method == "GET":
            return
        parts = urllib.parse.urlsplit(url)
        prefix = urllib.parse.urlsplit(self.base).path.rstrip("/")
        path = parts.path[len(prefix):] if parts.path.startswith(prefix) else parts.path
        allowed = self.ALLOWED_POSTS.get(path) if method == "POST" else None
        params = set(urllib.parse.parse_qs(parts.query, keep_blank_values=True))
        if allowed is None or not params <= allowed:
            raise RommError(
                f"Blocked: {method} {path}{'?' + parts.query if parts.query else ''}. "
                "The launcher is only allowed to read from RomM and upload saves."
            )

    def _open(self, method, path, params=None, body=None, content_type=None, retry=True):
        url = self._url(path, params)
        self._check(method, url)
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", self.auth)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "romm-launcher")
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            return self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code == 403 and "csrf" in detail.lower() and retry and self.auth.startswith("Basic"):
                # Some RomM setups refuse Basic auth on writes; use an API token instead.
                self._get_token()
                return self._open(method, path, params, body, content_type, retry=False)
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

    def _get_token(self) -> None:
        form = urllib.parse.urlencode(
            {
                "grant_type": "password",
                "username": self.username,
                "password": self.password,
                "scope": self.TOKEN_SCOPES,
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
