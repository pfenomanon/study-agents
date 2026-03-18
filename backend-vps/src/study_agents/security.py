"""Shared security helpers for API authentication, throttling, and validation."""
from __future__ import annotations

import hmac
import re
import socket
import time
from collections import defaultdict, deque
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urlparse

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_PRIVATE_NETWORKS = (
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),
    ip_network("169.254.0.0/16"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
)


def extract_auth_token(headers: Mapping[str, str]) -> str:
    """Extract auth token from X-API-Key or Authorization headers."""
    direct = (headers.get("X-API-Key") or "").strip()
    if direct:
        return direct
    authorization = (headers.get("Authorization") or "").strip()
    if not authorization:
        return ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return authorization


def parse_trusted_proxy_networks(raw_value: str | None) -> tuple:
    """Parse comma-separated trusted proxy CIDRs."""
    if not raw_value:
        return ()
    networks = []
    for token in re.split(r"[\s,]+", raw_value.strip()):
        cidr = token.strip()
        if not cidr:
            continue
        try:
            networks.append(ip_network(cidr, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def extract_client_ip(
    remote_addr: str | None,
    x_forwarded_for: str | None,
    trusted_proxy_networks: Iterable = (),
) -> str:
    """
    Resolve a client IP safely.

    Trust X-Forwarded-For only when the direct remote address belongs to a
    configured trusted proxy network.
    """
    remote = (remote_addr or "").strip()
    remote_ip = None
    if remote:
        try:
            remote_ip = ip_address(remote)
        except ValueError:
            remote_ip = None

    trust_forwarded = bool(x_forwarded_for and remote_ip)
    if trust_forwarded:
        trust_forwarded = any(remote_ip in network for network in trusted_proxy_networks)

    if trust_forwarded:
        for candidate in (x_forwarded_for or "").split(","):
            candidate = candidate.strip()
            if not candidate:
                continue
            try:
                ip_address(candidate)
            except ValueError:
                continue
            return candidate

    if remote:
        return remote
    return "unknown"


def token_matches(expected_token: str, provided_token: str) -> bool:
    """Constant-time comparison to reduce token timing leakage."""
    if not expected_token:
        return True
    if not provided_token:
        return False
    return hmac.compare_digest(expected_token, provided_token)


class RateLimiter:
    """Simple in-memory sliding-window rate limiter."""

    def __init__(self, max_requests: int, window_seconds: int = 60):
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = max(1, int(window_seconds))
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        history = self._events[key]
        cutoff = now - self.window_seconds
        while history and history[0] <= cutoff:
            history.popleft()
        if len(history) >= self.max_requests:
            retry_after = int(self.window_seconds - (now - history[0])) + 1
            return False, max(1, retry_after)
        history.append(now)
        return True, 0


def parse_allowed_roots(raw_value: str | None, defaults: Iterable[Path]) -> tuple[Path, ...]:
    """Build an absolute allowlist of filesystem roots from env and defaults."""
    roots: list[Path] = []
    if raw_value:
        for token in raw_value.split(","):
            token = token.strip()
            if token:
                roots.append(Path(token).expanduser().resolve())
    for default in defaults:
        resolved = Path(default).expanduser().resolve()
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def is_path_within_roots(candidate: Path, roots: Iterable[Path]) -> bool:
    """Return True if candidate path resides under one of the allowed roots."""
    resolved_candidate = candidate.expanduser().resolve()
    for root in roots:
        resolved_root = Path(root).expanduser().resolve()
        if resolved_candidate == resolved_root or resolved_candidate.is_relative_to(resolved_root):
            return True
    return False


def validate_outbound_url(url: str, *, allow_private_networks: bool = False) -> tuple[bool, str]:
    """
    Validate URL scheme and prevent private-network SSRF by default.

    Returns:
        (True, "") if allowed; otherwise (False, reason).
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return False, "unsupported URL scheme"
    if not parsed.netloc or not parsed.hostname:
        return False, "missing URL host"

    host = parsed.hostname.strip().lower()
    if not allow_private_networks and host in {"localhost", "127.0.0.1", "::1"}:
        return False, "localhost URLs are not allowed"

    try:
        addr_info = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, "host resolution failed"

    for info in addr_info:
        ip_str = info[4][0]
        ip_obj = ip_address(ip_str)
        if allow_private_networks:
            continue
        if any(ip_obj in network for network in _PRIVATE_NETWORKS):
            return False, f"private network address is not allowed ({ip_obj})"
        if ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified:
            return False, f"restricted network address is not allowed ({ip_obj})"

    return True, ""
