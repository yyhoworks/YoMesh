"""SSRF guard for outbound HTTP fetches (fixes H3).

Subscription pulls and rule-provider refreshes request URLs that come from
user configuration or imported third-party files. By default, hosts resolving
to loopback / private / link-local / multicast / otherwise non-public
addresses are refused, and every redirect hop is re-validated (urllib follows
redirects without re-checking the target).

Opt-outs (explicit only):
  * per-source ``allow_private: true`` in the source/provider dict, or
  * the ``YOMESH_ALLOW_PRIVATE_FETCH=1`` environment variable.

Known limitation: the address is validated at resolve time, not pinned for
the subsequent connection, so a hostile DNS with a very short TTL could
still rebind between check and connect. This blocks the common cases
(direct private URLs, redirect chains to intranet/metadata endpoints).
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .model import ConfigError

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5


class _NoAutoRedirect(HTTPRedirectHandler):
    """Turn automatic redirect following into an HTTPError we handle ourselves."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = build_opener(_NoAutoRedirect)


def _check_target(url: str, *, allow_private: bool, what: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ConfigError(f"{what}仅支持 http(s) 协议")
    host = parts.hostname
    if not host:
        raise ConfigError(f"{what}缺少主机名")
    try:
        infos = socket.getaddrinfo(host, parts.port or 0, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ConfigError(f"{what}的主机名无法解析：{host}") from exc
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            raise ConfigError(f"{what}解析出无效地址：{host}")
        if not allow_private and not ip.is_global:
            raise ConfigError(f"{what}指向非公网地址，已拒绝：{host}")


def _resolve_allow_private(flag) -> bool:
    if isinstance(flag, str):
        return flag.strip() == "1" or flag.strip().lower() == "true"
    if flag:
        return True
    return os.environ.get("YOMESH_ALLOW_PRIVATE_FETCH") == "1"


def guarded_fetch(
    url: str,
    *,
    timeout: float = 20,
    max_bytes: int = 8 * 1024 * 1024,
    headers: dict | None = None,
    allow_private=False,
    what: str = "目标地址",
):
    """Fetch a URL with SSRF protections. Returns ``(body, headers)``.

    ``body`` is at most ``max_bytes + 1`` bytes so callers can enforce their
    own size limit exactly as before.
    """
    allow = _resolve_allow_private(allow_private)
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        _check_target(current, allow_private=allow, what=what)
        req = Request(current, headers=headers or {})
        try:
            with _opener.open(req, timeout=timeout) as resp:
                return resp.read(max_bytes + 1), resp.headers
        except HTTPError as exc:
            if exc.code in _REDIRECT_CODES:
                location = exc.headers.get("Location") if exc.headers else None
                if not location:
                    raise ConfigError(f"{what}重定向缺少 Location，已中止") from None
                current = urljoin(current, location)
                continue
            raise
    raise ConfigError(f"{what}重定向次数过多，已中止")
