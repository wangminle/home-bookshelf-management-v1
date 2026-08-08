from __future__ import annotations

import ipaddress
import logging
import shutil
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from app.config import settings
from app.utils.book_helpers import sanitize_filename_stem

logger = logging.getLogger(__name__)

MAX_COVER_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_SCHEMES = frozenset({"http", "https"})
_DOWNLOAD_CHUNK = 64 * 1024


def _is_safe_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """跟随 3xx 前对每个 Location 复检 _is_safe_url，阻断跳转到内网/回环。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_safe_url(newurl):
            raise urllib.error.URLError(f"blocked redirect to unsafe URL: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SAFE_OPENER = urllib.request.build_opener(_SafeRedirectHandler)


def download_cover(cover_url: str, target_name: str) -> str | None:
    settings.covers_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jpg"
    if "." in cover_url.rsplit("/", 1)[-1]:
        suffix = "." + cover_url.rsplit(".", 1)[-1].split("?")[0][:5]

    if not _is_safe_url(cover_url):
        logger.warning("拒绝下载封面（不安全的 URL）: %s", cover_url)
        return None

    dest = settings.covers_dir / f"{sanitize_filename_stem(target_name)}{suffix}"
    from uuid import uuid4

    tmp_dest = settings.covers_dir / f"{sanitize_filename_stem(target_name)}.{uuid4().hex[:8]}.part{suffix}"
    try:
        req = urllib.request.Request(cover_url, headers={"User-Agent": "home-bookshelf/1.0"})
        with _SAFE_OPENER.open(req, timeout=20) as resp:
            total = 0
            too_large = False
            with open(tmp_dest, "wb") as out:
                while True:
                    chunk = resp.read(_DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_COVER_BYTES:
                        too_large = True
                        break
                    out.write(chunk)
        if too_large:
            logger.warning(
                "封面超过大小上限 (%d > %d): %s", total, MAX_COVER_BYTES, cover_url
            )
            tmp_dest.unlink(missing_ok=True)
            return None
        tmp_dest.replace(dest)
        return str(dest.relative_to(settings.data_dir))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        logger.warning("下载封面失败 %s: %s", cover_url, exc)
        tmp_dest.unlink(missing_ok=True)
        return None
    except Exception as exc:
        logger.exception("下载封面出现未预期异常 %s: %s", cover_url, exc)
        tmp_dest.unlink(missing_ok=True)
        return None


def save_uploaded_image(source_path: Path, target_name: str, *, overwrite: bool = False) -> str | None:
    settings.covers_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix or ".jpg"
    stem = sanitize_filename_stem(target_name)
    dest = settings.covers_dir / f"{stem}{suffix}"
    if dest.exists() and not overwrite:
        # 避免扫描图覆盖已下载封面：追加短后缀
        from uuid import uuid4

        dest = settings.covers_dir / f"{stem}_{uuid4().hex[:8]}{suffix}"
    try:
        shutil.copy2(source_path, dest)
        return str(dest.relative_to(settings.data_dir))
    except OSError as exc:
        logger.warning("保存上传图片失败 %s -> %s: %s", source_path, dest, exc)
        return None
    except Exception as exc:
        logger.exception("保存上传图片出现未预期异常 %s: %s", source_path, exc)
        return None