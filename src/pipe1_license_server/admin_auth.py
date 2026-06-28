from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from pipe1_license_server.settings import ServerSettings


PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
SESSION_COOKIE_NAME = "pipe1_admin_session"


@dataclass(frozen=True)
class AdminSession:
    username: str
    csrf_token: str
    expires_at: int


def hash_admin_password(password: str, *, iterations: int = 260_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return ":".join(
        [
            PASSWORD_HASH_ALGORITHM,
            str(iterations),
            _b64(salt),
            _b64(digest),
        ]
    )


def verify_admin_password(password: str, encoded: str) -> bool:
    try:
        separator = ":" if ":" in encoded else "$"
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split(separator, 3)
        if algorithm != PASSWORD_HASH_ALGORITHM:
            return False
        iterations = int(iterations_raw)
        salt = _b64decode(salt_raw)
        expected = _b64decode(digest_raw)
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return secrets.compare_digest(actual, expected)


def admin_auth_is_configured(settings: ServerSettings) -> bool:
    if not (
        settings.admin_username
        and settings.admin_session_secret
        and len(settings.admin_session_secret) >= 32
    ):
        return False
    if settings.app_env == "production":
        return bool(settings.admin_password_hash and settings.admin_totp_secret)
    return bool(settings.admin_password_hash or settings.admin_password)


def verify_admin_login(
    settings: ServerSettings,
    *,
    username: str,
    password: str,
    totp_code: str | None,
) -> bool:
    if not admin_auth_is_configured(settings):
        return False
    if not secrets.compare_digest(username, settings.admin_username or ""):
        return False

    if settings.app_env == "production" and not settings.admin_password_hash:
        return False
    if settings.admin_password_hash:
        password_ok = verify_admin_password(password, settings.admin_password_hash)
    else:
        password_ok = secrets.compare_digest(password, settings.admin_password or "")
    if not password_ok:
        return False

    if settings.admin_totp_secret:
        return verify_totp_code(settings.admin_totp_secret, totp_code or "")
    return True


def create_admin_session(settings: ServerSettings, username: str) -> str:
    now = int(time.time())
    ttl = max(300, int(settings.admin_session_ttl_seconds))
    payload = {
        "sub": username,
        "csrf": secrets.token_urlsafe(24),
        "iat": now,
        "exp": now + ttl,
    }
    payload_bytes = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    encoded_payload = _b64url(payload_bytes)
    signature = _session_signature(settings, encoded_payload)
    return f"{encoded_payload}.{signature}"


def load_admin_session(
    settings: ServerSettings, cookie_value: str | None
) -> AdminSession | None:
    if not admin_auth_is_configured(settings) or not cookie_value:
        return None
    encoded_payload, separator, signature = cookie_value.partition(".")
    if not separator or not encoded_payload or not signature:
        return None
    expected_signature = _session_signature(settings, encoded_payload)
    if not secrets.compare_digest(signature, expected_signature):
        return None
    try:
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    username = payload.get("sub")
    csrf_token = payload.get("csrf")
    expires_at = payload.get("exp")
    if not isinstance(username, str) or not isinstance(csrf_token, str):
        return None
    if not isinstance(expires_at, int) or int(time.time()) > expires_at:
        return None
    if not secrets.compare_digest(username, settings.admin_username or ""):
        return None
    return AdminSession(
        username=username,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


def verify_csrf(admin_session: AdminSession, submitted: str | None) -> bool:
    return bool(
        submitted and secrets.compare_digest(submitted, admin_session.csrf_token)
    )


def totp_code(secret: str, *, at_time: int | None = None) -> str:
    key = _totp_key(secret)
    timestamp = int(time.time()) if at_time is None else at_time
    counter = timestamp // 30
    digest = hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(value % 1_000_000).zfill(6)


def verify_totp_code(secret: str, code: str, *, at_time: int | None = None) -> bool:
    normalized = "".join(ch for ch in code.strip() if ch.isdigit())
    if len(normalized) != 6:
        return False
    timestamp = int(time.time()) if at_time is None else at_time
    try:
        return any(
            secrets.compare_digest(
                normalized, totp_code(secret, at_time=timestamp + (offset * 30))
            )
            for offset in (-1, 0, 1)
        )
    except ValueError:
        return False


def _session_signature(settings: ServerSettings, encoded_payload: str) -> str:
    secret = (settings.admin_session_secret or "").encode("utf-8")
    digest = hmac.new(secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return _b64url(digest)


def _totp_key(secret: str) -> bytes:
    normalized = "".join(secret.upper().split())
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    return base64.b32decode(normalized + padding, casefold=True)


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
