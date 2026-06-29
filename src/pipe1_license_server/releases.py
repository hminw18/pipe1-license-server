from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlparse

from pipe1_license_server.models import AppRelease


RELEASE_PLATFORMS = {"windows"}
RELEASE_ARCHES = {"x64", "arm64"}
RELEASE_CHANNELS = {"stable", "beta"}
RELEASE_STATUSES = {"draft", "published", "disabled"}
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def normalize_release_target(
    platform: str,
    arch: str,
    channel: str,
) -> tuple[str, str, str]:
    normalized_platform = platform.strip().lower()
    normalized_arch = arch.strip().lower()
    normalized_channel = channel.strip().lower()
    if normalized_platform not in RELEASE_PLATFORMS:
        raise ValueError("platform must be windows")
    if normalized_arch not in RELEASE_ARCHES:
        raise ValueError("arch must be x64 or arm64")
    if normalized_channel not in RELEASE_CHANNELS:
        raise ValueError("channel must be stable or beta")
    return normalized_platform, normalized_arch, normalized_channel


def normalize_release_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized not in RELEASE_STATUSES:
        raise ValueError("status must be draft, published, or disabled")
    return normalized


def validate_version(value: str) -> str:
    normalized = value.strip()
    if not _VERSION_RE.fullmatch(normalized):
        raise ValueError("version must use MAJOR.MINOR.PATCH")
    return normalized


def version_key(value: str) -> tuple[int, int, int]:
    major, minor, patch = validate_version(value).split(".")
    return int(major), int(minor), int(patch)


def compare_versions(left: str, right: str) -> int:
    left_key = version_key(left)
    right_key = version_key(right)
    return (left_key > right_key) - (left_key < right_key)


def validate_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("sha256 must be a 64-character hex digest")
    return normalized


def validate_download_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("download_url must be an absolute https URL")
    return normalized


def validate_size_bytes(value: int) -> int:
    if value <= 0:
        raise ValueError("size_bytes must be greater than zero")
    return value


def latest_published_release(releases: Iterable[AppRelease]) -> AppRelease | None:
    candidates = list(releases)
    if not candidates:
        return None
    return max(candidates, key=lambda release: version_key(release.version))


def release_to_dict(release: AppRelease) -> dict[str, Any]:
    return {
        "id": release.id,
        "version": release.version,
        "platform": release.platform,
        "arch": release.arch,
        "channel": release.channel,
        "status": release.status,
        "mandatory": release.mandatory,
        "min_supported_version": release.min_supported_version,
        "download_url": release.download_url,
        "sha256": release.sha256,
        "size_bytes": release.size_bytes,
        "release_notes": release.release_notes,
        "published_at": _iso(release.published_at),
        "created_at": _iso(release.created_at),
        "updated_at": _iso(release.updated_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
