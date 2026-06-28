from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from pipe1_license_server.admin import AdminService
from pipe1_license_server.app import create_app
from pipe1_license_server.db import session_scope
from pipe1_license_server.models import TrainingSample
from pipe1_license_server.settings import ServerSettings
from pipe1_license_server.signing import generate_private_key_b64


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def _settings(tmp_path: Path) -> ServerSettings:
    return ServerSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'server.db'}",
        signing_private_key=generate_private_key_b64(),
        signing_key_id="security-test-key",
        app_env="test",
        max_training_image_bytes=1024,
    )


def _training_server(tmp_path: Path) -> tuple[ServerSettings, TestClient, str, str, str]:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    admin = AdminService(settings)
    org_id = admin.create_organization("Security Co", None)
    license_id = admin.create_license(
        organization_id=org_id,
        plan="standard",
        device_limit=2,
        expires_at="2027-06-30T23:59:59Z",
        features={"local_report": True, "training_upload": True},
    )
    raw_key = admin.generate_license_key(license_id)
    activation = client.post(
        "/licenses/activate",
        json={
            "license_key": raw_key,
            "device_id": "pipe1-sec-dev",
            "os_name": "Windows",
            "os_version": "11",
            "app_version": "0.1.0",
        },
    )
    assert activation.status_code == 200, activation.text
    return (
        settings,
        client,
        license_id,
        "pipe1-sec-dev",
        activation.json()["device_upload_token"],
    )


def _snapshot_body(license_id: str, device_id: str) -> dict:
    return {
        "license_id": license_id,
        "device_id": device_id,
        "local_report_id": "1",
        "report_fingerprint": "fingerprint-1",
        "export_type": "excel",
        "metadata": {
            "consent": {
                "accepted": True,
                "type": "capture_images_and_labels",
                "version": "2026-06-25",
            }
        },
    }


def _sample_body(image_bytes: bytes = PNG_1X1) -> dict:
    return {
        "local_defect_id": "1",
        "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "image_filename": "capture.png",
        "labels": {
            "sample_type": "defect",
            "item_category": "관로",
            "defect_item": "균열(길이)",
            "grade": "대",
        },
        "metadata": {"timestamp_ms": 1000},
        "image_base64": base64.b64encode(image_bytes).decode("ascii"),
    }


def test_training_upload_requires_bearer_token_for_every_step_and_does_not_store_image(
    tmp_path: Path,
) -> None:
    settings, client, license_id, device_id, upload_token = _training_server(tmp_path)
    body = _snapshot_body(license_id, device_id)

    no_token = client.post("/training/snapshots", json=body)
    assert no_token.status_code == 401
    assert no_token.json()["code"] == "MISSING_UPLOAD_TOKEN"

    wrong_token = client.post(
        "/training/snapshots",
        json=body,
        headers={"Authorization": "Bearer wrong"},
    )
    assert wrong_token.status_code == 403
    assert wrong_token.json()["code"] == "INVALID_UPLOAD_TOKEN"

    headers = {"Authorization": f"Bearer {upload_token}"}
    no_server_consent = client.post("/training/snapshots", json=body, headers=headers)
    assert no_server_consent.status_code == 403
    assert no_server_consent.json()["code"] == "TRAINING_CONSENT_REQUIRED"

    consent = client.post(
        "/training/consents",
        json={
            "license_id": license_id,
            "device_id": device_id,
            "consent_type": "capture_images_and_labels",
            "consent_version": "2026-06-25",
            "accepted": True,
            "app_version": "0.1.0",
        },
        headers=headers,
    )
    assert consent.status_code == 200, consent.text

    created = client.post("/training/snapshots", json=body, headers=headers)
    assert created.status_code == 200, created.text
    snapshot_id = created.json()["snapshot_id"]

    sample_without_token = client.post(
        f"/training/snapshots/{snapshot_id}/samples",
        json=_sample_body(),
    )
    assert sample_without_token.status_code == 401

    uploaded = client.post(
        f"/training/snapshots/{snapshot_id}/samples",
        json=_sample_body(),
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text

    read_without_token = client.get(f"/training/snapshots/{snapshot_id}")
    assert read_without_token.status_code == 401
    read_with_token = client.get(f"/training/snapshots/{snapshot_id}", headers=headers)
    assert read_with_token.status_code == 200

    with session_scope(settings) as session:
        sample = session.get(TrainingSample, uploaded.json()["sample_id"])
        assert sample is not None
        assert "image_base64" not in sample.metadata_json
        assert sample.metadata_json["image_size_bytes"] == len(PNG_1X1)


def test_training_upload_rejects_oversized_and_invalid_images(tmp_path: Path) -> None:
    _, client, license_id, device_id, upload_token = _training_server(tmp_path)
    headers = {"Authorization": f"Bearer {upload_token}"}
    consent = client.post(
        "/training/consents",
        json={
            "license_id": license_id,
            "device_id": device_id,
            "consent_type": "capture_images_and_labels",
            "consent_version": "2026-06-25",
            "accepted": True,
            "app_version": "0.1.0",
        },
        headers=headers,
    )
    assert consent.status_code == 200, consent.text
    created = client.post(
        "/training/snapshots",
        json=_snapshot_body(license_id, device_id),
        headers=headers,
    )
    assert created.status_code == 200
    snapshot_id = created.json()["snapshot_id"]

    invalid_image = b"not-an-image"
    invalid = client.post(
        f"/training/snapshots/{snapshot_id}/samples",
        json=_sample_body(invalid_image),
        headers=headers,
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "INVALID_IMAGE"

    oversized_image = PNG_1X1 + (b"x" * 2048)
    oversized = client.post(
        f"/training/snapshots/{snapshot_id}/samples",
        json=_sample_body(oversized_image),
        headers=headers,
    )
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "IMAGE_TOO_LARGE"
