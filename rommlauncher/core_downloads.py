"""Downloading missing RetroArch cores (and their .info files) from libretro's
buildbot, the same place RetroArch's own Core Downloader uses.
"""

from __future__ import annotations

import io
import platform
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .retroarch import core_id
from .system import CORE_EXT, IS_WINDOWS
from .ui import progress_bar
from .util import write_new_file


# Missing cores are downloaded from libretro's buildbot, where RetroArch's own
# "Online Updater → Core Downloader" gets them.
BUILDBOT = "https://buildbot.libretro.com"
BUILDBOT_HOST = "buildbot.libretro.com"
MAX_CORE_DOWNLOAD = 300 * 1024 * 1024


class CoreInstallError(Exception):
    pass


def buildbot_platform() -> str | None:
    """The buildbot folder for this system and CPU, e.g. "windows/x86_64"."""
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        arch = "x86_64"
    elif machine in ("i386", "i686", "x86"):
        arch = "x86"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64" if sys.platform == "darwin" else "aarch64"
    elif machine.startswith("armv7"):
        arch = "armhf"
    else:
        return None
    if IS_WINDOWS:
        return f"windows/{arch}" if arch in ("x86_64", "x86") else None
    if sys.platform == "darwin":
        return f"apple/osx/{arch}" if arch in ("x86_64", "arm64") else None
    return f"linux/{arch}"


def _updater_url(configured: str | None) -> str | None:
    """A download address from retroarch.cfg, if it's usable: libretro's own server is
    always fetched over https, any other server only if it's already https."""
    parts = urllib.parse.urlsplit(configured or "")
    if parts.hostname == BUILDBOT_HOST:
        return urllib.parse.urlunsplit(("https",) + tuple(parts)[1:]).rstrip("/") + "/"
    if parts.scheme == "https" and parts.hostname:
        return configured.rstrip("/") + "/"
    return None


def core_download_url(core: str, ra_cfg: dict) -> str | None:
    """Where to download a core: the address RetroArch's Core Downloader uses (from
    retroarch.cfg), else libretro's buildbot for this system. None if there's no build
    for this kind of computer."""
    base = _updater_url(ra_cfg.get("core_updater_buildbot_cores_url"))
    if not base:
        plat = buildbot_platform()
        if not plat:
            return None
        base = f"{BUILDBOT}/nightly/{plat}/latest/"
    return f"{base}{core_id(core)}_libretro{CORE_EXT}.zip"


def info_download_url(ra_cfg: dict) -> str:
    base = _updater_url(ra_cfg.get("core_updater_buildbot_assets_url")) or f"{BUILDBOT}/assets/"
    return f"{base}frontend/info.zip"


def http_download(url: str, label: str | None = None, timeout: int = 60) -> bytes:
    """Download a file (no login is sent), with a progress bar if `label` is given."""
    req = urllib.request.Request(url, headers={"User-Agent": "romm-launcher"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0) or None
            progress = progress_bar(label, total) if label else None
            chunks, done = [], 0
            while chunk := resp.read(1 << 16):
                chunks.append(chunk)
                done += len(chunk)
                if done > MAX_CORE_DOWNLOAD:
                    raise CoreInstallError(f"{url} is unexpectedly large")
                if progress:
                    progress(done)
            if progress:
                progress.finish()
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        raise CoreInstallError(f"{url} answered {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise CoreInstallError(f"can't reach {urllib.parse.urlsplit(url).netloc} ({exc.reason})") from exc
    except (TimeoutError, OSError) as exc:
        raise CoreInstallError(f"the download from {url} broke off ({exc})") from exc


def _one_file_from_zip(data: bytes, name: str, source: str) -> bytes:
    """The file `name` from the top level of a zip; nothing else in the zip is used."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            member = next((i for i in zf.infolist() if i.filename == name), None)
            if member is None:
                raise CoreInstallError(f"{source} doesn't contain {name}")
            return zf.read(member)
    except zipfile.BadZipFile as exc:
        raise CoreInstallError(f"{source} isn't a valid zip ({exc})") from exc


def install_core(core: str, cores_dir: Path, ra_cfg: dict, label: str | None = None) -> Path:
    """Download a core into RetroArch's cores folder and return its path.
    An existing core file is never replaced."""
    name = f"{core_id(core)}_libretro{CORE_EXT}"
    dest = cores_dir / name
    if dest.exists():
        return dest
    url = core_download_url(core, ra_cfg)
    if not url:
        raise CoreInstallError(f"libretro has no cores for this kind of computer ({platform.machine()})")
    write_new_file(dest, _one_file_from_zip(http_download(url, label), name, url))
    return dest


def core_info_dir(ra_cfg: dict, cores_dir: Path) -> Path:
    """Where RetroArch looks for core .info files."""
    configured = ra_cfg.get("libretro_info_path")
    if configured and configured != "default":
        return Path(configured).expanduser()
    return cores_dir.parent / "info" if (cores_dir.parent / "info").is_dir() else cores_dir


def install_core_info(core: str, info_dir: Path, ra_cfg: dict) -> Path | None:
    """Add the core's .info file (its display name, which names its save folder) if it's
    missing. Returns the new file, or None if it was already there."""
    name = f"{core_id(core)}_libretro.info"
    dest = info_dir / name
    if dest.exists():
        return None
    url = info_download_url(ra_cfg)
    write_new_file(dest, _one_file_from_zip(http_download(url), name, url))
    return dest
