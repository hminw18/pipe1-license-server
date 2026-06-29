from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pipe1_license_server.admin import AdminService
from pipe1_license_server.app import create_app
from pipe1_license_server.settings import ServerSettings
from pipe1_license_server.signing import generate_private_key_b64


def _settings(tmp_path: Path) -> ServerSettings:
    return ServerSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'license.db'}",
        signing_private_key=generate_private_key_b64(),
        signing_key_id="release-test-key",
        app_env="test",
    )


def test_latest_release_endpoint_returns_only_newer_published_release(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    admin = AdminService(settings)
    admin.create_release(
        version="1.0.0",
        platform="windows",
        arch="x64",
        channel="stable",
        download_url="https://license.example.com/downloads/Pipe1-1.0.0-x64.msi",
        sha256="a" * 64,
        size_bytes=1024,
        status="draft",
        release_notes="Draft release",
    )
    admin.create_release(
        version="1.1.0",
        platform="windows",
        arch="x64",
        channel="stable",
        download_url="https://license.example.com/downloads/Pipe1-1.1.0-x64.msi",
        sha256="b" * 64,
        size_bytes=2048,
        status="published",
        min_supported_version="0.9.0",
        release_notes="Stable release",
    )
    admin.create_release(
        version="1.2.0",
        platform="windows",
        arch="x64",
        channel="beta",
        download_url="https://license.example.com/downloads/Pipe1-1.2.0-x64.msi",
        sha256="c" * 64,
        size_bytes=4096,
        status="published",
        mandatory=True,
    )

    client = TestClient(create_app(settings))
    update = client.get(
        "/app/releases/latest",
        params={
            "platform": "windows",
            "arch": "x64",
            "channel": "stable",
            "current_version": "1.0.0",
        },
    )
    current = client.get(
        "/app/releases/latest",
        params={
            "platform": "windows",
            "arch": "x64",
            "channel": "stable",
            "current_version": "1.1.0",
        },
    )
    unsupported = client.get(
        "/app/releases/latest",
        params={
            "platform": "windows",
            "arch": "x64",
            "channel": "stable",
            "current_version": "0.8.0",
        },
    )
    bad_version = client.get(
        "/app/releases/latest",
        params={
            "platform": "windows",
            "arch": "x64",
            "channel": "stable",
            "current_version": "dev",
        },
    )
    mandatory_current = client.get(
        "/app/releases/latest",
        params={
            "platform": "windows",
            "arch": "x64",
            "channel": "beta",
            "current_version": "1.2.0",
        },
    )

    assert update.status_code == 200, update.text
    update_payload = update.json()
    assert update_payload["update_available"] is True
    assert update_payload["latest_version"] == "1.1.0"
    assert update_payload["download_url"].endswith("Pipe1-1.1.0-x64.msi")
    assert update_payload["sha256"] == "b" * 64
    assert update_payload["size_bytes"] == 2048
    assert update_payload["mandatory"] is False

    assert current.status_code == 200, current.text
    current_payload = current.json()
    assert current_payload["update_available"] is False
    assert current_payload["latest_version"] == "1.1.0"
    assert current_payload["download_url"] is None
    assert current_payload["sha256"] is None

    assert unsupported.status_code == 200, unsupported.text
    assert unsupported.json()["mandatory"] is True

    assert bad_version.status_code == 400
    assert bad_version.json()["code"] == "BAD_VERSION"

    assert mandatory_current.status_code == 200
    assert mandatory_current.json()["update_available"] is False
    assert mandatory_current.json()["mandatory"] is False
    assert mandatory_current.json()["download_url"] is None


def test_latest_release_endpoint_handles_empty_release_channel(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))

    response = client.get(
        "/app/releases/latest",
        params={
            "platform": "windows",
            "arch": "x64",
            "channel": "stable",
            "current_version": "0.1.0",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "update_available": False,
        "current_version": "0.1.0",
        "latest_version": None,
        "mandatory": False,
        "min_supported_version": None,
        "download_url": None,
        "sha256": None,
        "size_bytes": None,
        "release_notes": None,
        "published_at": None,
    }
