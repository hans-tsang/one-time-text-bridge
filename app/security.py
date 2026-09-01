"""Security helpers: token generation/hashing, security headers,
a simple in-memory rate limiter, and CSRF token helpers.

Nothing in this module logs raw tokens or message contents.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

TOKEN_BYTES = 32  # 256 bits


def generate_token() -> str:
    """Generate a cryptographically secure random 256-bit URL-safe token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    """Return the hex SHA-256 hash of a raw token. Never log the raw token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# ---------------------------------------------------------------------------
# CSRF protection
#
# Simple double-submit-cookie style CSRF token, HMAC-signed with the app
# SECRET_KEY. A token is issued on GET requests that render a form and
# must be echoed back as a hidden form field on POST.
# ---------------------------------------------------------------------------

CSRF_COOKIE_NAME = "csrf_token"


def _sign(value: str) -> str:
    return hmac.new(settings.secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_csrf_token() -> str:
    nonce = secrets.token_urlsafe(16)
    signature = _sign(nonce)
    return f"{nonce}.{signature}"


def verify_csrf_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    nonce, _, signature = token.partition(".")
    expected = _sign(nonce)
    return constant_time_equals(signature, expected)


# ---------------------------------------------------------------------------
# Rate limiting
#
# WARNING: this is a simple in-memory, per-process rate limiter intended
# for development / single-instance deployments only. It is NOT suitable
# for multi-instance production deployments because each process has its
# own independent counters. For multi-instance production use, replace
# this with a shared store (e.g. Redis) keyed by client IP.
# ---------------------------------------------------------------------------


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True


rate_limiter = InMemoryRateLimiter(max_requests=settings.rate_limit_per_minute, window_seconds=60.0)


def client_ip(request: Request) -> str:
    """Best-effort client IP extraction.

    Trusts X-Forwarded-For only when the direct peer is a configured
    trusted proxy (e.g. the Caddy container), matching the deployment's
    proxy topology.
    """
    peer = request.client.host if request.client else "unknown"
    if peer in settings.trusted_proxy_ips:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)


def apply_security_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "magnetometer=(), gyroscope=(), interest-cohort=()"
    )
    return response
