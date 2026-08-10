from __future__ import annotations

import functools
import http.client
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


def _is_safe_url(url: str) -> tuple[bool, str | None]:
    """检查 URL 是否安全（scheme + 主机不指向内网/回环）。

    BUG-105：返回解析到的 IP 供调用方 pinning，避免 getaddrinfo → urlopen 之间的 TOCTOU DNS rebinding。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        return False, None
    host = parsed.hostname
    if not host:
        return False, None
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False, None
    safe_ip: str | None = None
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, None
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, None
        if safe_ip is None:
            safe_ip = ip_str  # 取第一个安全 IP 用于 pinning
    return True, safe_ip


def _format_netloc(host: str, port: int | None) -> str:
    """构造 netloc/Host 头：IPv6 地址必须加方括号（BUG-131）。"""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return host if port is None else f"{host}:{port}"


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TCP 连接打到钉住的 IP，但 TLS SNI 与证书主机名校验仍用原始主机名（BUG-131）。

    BUG-105 的 IP pinning 把请求 URL 的主机替换为 IP，默认 HTTPSConnection 会用该 IP
    做 server_hostname，导致合法 HTTPS 站点证书校验失败（证书签给域名而非 IP）。
    这里显式把原始主机名传回 wrap_socket。
    """

    def __init__(self, *args, server_hostname: str | None = None, **kwargs):
        self._pinned_server_hostname = server_hostname
        super().__init__(*args, **kwargs)

    def connect(self):
        # 与 http.client.HTTPSConnection.connect 等价，仅 server_hostname 换成原始主机名。
        # 注意优先于 _tunnel_host：经代理 CONNECT 隧道时 _tunnel_host 是钉住的 IP，
        # 若用它做 SNI/证书校验仍会 IP 不匹配。
        super(http.client.HTTPSConnection, self).connect()  # HTTPConnection.connect：TCP + 代理隧道
        server_hostname = self._pinned_server_hostname or self._tunnel_host or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """为钉住 IP 的请求创建 _PinnedHTTPSConnection（BUG-131）。

    原始主机名由 _build_pinned_request / _SafeRedirectHandler 暂存在
    request 的 ``_bookshelf_server_hostname`` 属性上。
    """

    def https_open(self, req):
        server_hostname = getattr(req, "_bookshelf_server_hostname", None)
        factory = functools.partial(_PinnedHTTPSConnection, server_hostname=server_hostname)
        return self.do_open(factory, req, context=self._context)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """跟随 3xx 前对每个 Location 复检 _is_safe_url 并钉住新解析的安全 IP。

    BUG-105：对重定向目标同样做 IP pinning，杜绝 getaddrinfo→urlopen 之间的 DNS rebinding。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe, pinned_ip = _is_safe_url(newurl)
        if not safe or not pinned_ip:
            raise urllib.error.URLError(f"blocked redirect to unsafe URL: {newurl}")
        # 用钉住 IP 的 URL 替换原始 Location，让父类构建的新 Request 也使用该 IP
        pinned_url = _pin_url(newurl, pinned_ip)
        new_req = super().redirect_request(req, fp, code, msg, headers, pinned_url)
        if new_req is not None:
            parsed = urllib.parse.urlparse(newurl)
            host = parsed.hostname
            if host:
                # 保留原始 Host 头，保证虚拟主机/TLS 正常
                new_req.add_unredirected_header("Host", _format_netloc(host, parsed.port))
                # BUG-131：HTTPS 握手的 SNI/证书校验需要原始主机名
                new_req._bookshelf_server_hostname = host  # type: ignore[attr-defined]
        return new_req


def _pin_url(url: str, pinned_ip: str) -> str:
    """把 URL 中的 hostname 替换为已校验安全的 IP，保留端口；IPv6 加方括号（BUG-131）。"""
    parsed = urllib.parse.urlparse(url)
    netloc = _format_netloc(pinned_ip, parsed.port)
    return urllib.parse.urlunparse(parsed._replace(netloc=netloc))


def _build_pinned_request(url: str, pinned_ip: str) -> urllib.request.Request:
    """构造钉住指定 IP 的请求，避免 urlopen 内部再次解析 DNS。

    BUG-105：将主机名替换为已校验安全的 IP，同时保留原始 Host 头与端口。
    BUG-131：把原始主机名暂存到 request 上，供 _PinnedHTTPSHandler 做 TLS SNI/证书校验。
    """
    pinned_url = _pin_url(url, pinned_ip)
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    headers = {"User-Agent": "home-bookshelf/1.0"}
    if host:
        headers["Host"] = _format_netloc(host, parsed.port)
    req = urllib.request.Request(pinned_url, headers=headers)
    req._bookshelf_server_hostname = host  # type: ignore[attr-defined]
    return req


# BUG-105：ProxyHandler({}) 禁用环境变量代理（HTTP_PROXY/HTTPS_PROXY），
# 否则代理服务器自行解析 DNS 会绕过上面的 IP pinning，重新打开 DNS rebinding 窗口。
_SAFE_OPENER = urllib.request.build_opener(
    _SafeRedirectHandler, _PinnedHTTPSHandler, urllib.request.ProxyHandler({})
)


def download_cover(cover_url: str, target_name: str) -> str | None:
    settings.covers_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jpg"
    if "." in cover_url.rsplit("/", 1)[-1]:
        suffix = "." + cover_url.rsplit(".", 1)[-1].split("?")[0][:5]

    # BUG-105：解析得到安全 IP，并钉住该 IP 构造请求 URL，避免 urlopen 再次 DNS 解析被重绑定到内网
    safe, pinned_ip = _is_safe_url(cover_url)
    if not safe or not pinned_ip:
        logger.warning("拒绝下载封面（不安全的 URL）: %s", cover_url)
        return None

    dest = settings.covers_dir / f"{sanitize_filename_stem(target_name)}{suffix}"
    from uuid import uuid4

    tmp_dest = settings.covers_dir / f"{sanitize_filename_stem(target_name)}.{uuid4().hex[:8]}.part{suffix}"
    try:
        req = _build_pinned_request(cover_url, pinned_ip)
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