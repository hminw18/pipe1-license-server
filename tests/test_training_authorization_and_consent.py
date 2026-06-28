from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pipe1_license_server.admin import AdminService
from pipe1_license_server.app import create_app
from pipe1_license_server.settings import ServerSettings
from pipe1_license_server.signing import generate_private_key_b64

def _settings(tmp_path: Path) -> ServerSettings:
    return ServerSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'server.db'}",
        signing_private_key=generate_private_key_b64(),
        signing_key_id="training-auth-key",
        app_env="test",
    )


def _activate(client: TestClient, raw_key: str, device_id: str) -> str:
    response = client.post(
        "/licenses/activate",
        json={
            "license_key": raw_key,
            "device_id": device_id,
            "os_name": "Windows",
            "os_version": "11",
            "app_version": "0.1.0",
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["device_upload_token"])


def test_training_upload_requires_feature_activation_and_consent(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    admin = AdminService(settings)
    org_id = admin.create_organization("Auth Co", None)
    license_id = admin.create_license(
        organization_id=org_id,
        plan="standard",
        device_limit=2,
        expires_at="2027-06-30T23:59:59Z",
        features={"local_report": True, "training_upload": False},
    )
    raw_key = admin.generate_license_key(license_id)
    upload_token = _activate(client, raw_key, "pipe1-consent-dev")
    headers = {"Authorization": f"Bearer {upload_token}"}

    body = {
        "license_id": license_id,
        "device_id": "pipe1-consent-dev",
        "local_report_id": "1",
        "report_fingerprint": "abc",
        "export_type": "excel",
        "metadata": {
            "consent": {
                "accepted": True,
                "type": "capture_images_and_labels",
                "version": "2026-06-25",
            }
        },
    }
    missing_token = client.post("/training/snapshots", json=body)
    assert missing_token.status_code == 401
    assert missing_token.json()["code"] == "MISSING_UPLOAD_TOKEN"

    no_feature = client.post("/training/snapshots", json=body, headers=headers)
    assert no_feature.status_code == 403
    assert no_feature.json()["code"] == "TRAINING_UPLOAD_NOT_ENABLED"

    admin.set_feature(license_id, "training_upload", True)
    not_activated = client.post(
        "/training/snapshots",
        json={**body, "device_id": "pipe1-other-dev"},
        headers=headers,
    )
    assert not_activated.status_code == 403
    assert not_activated.json()["code"] == "DEVICE_NOT_ACTIVATED"

    no_server_consent = client.post(
        "/training/snapshots",
        json=body,
        headers=headers,
    )
    assert no_server_consent.status_code == 403
    assert no_server_consent.json()["code"] == "TRAINING_CONSENT_REQUIRED"

    missing_consent_metadata = client.post(
        "/training/snapshots",
        json={**body, "metadata": {}},
        headers=headers,
    )
    assert missing_consent_metadata.status_code == 403
    assert missing_consent_metadata.json()["code"] == "TRAINING_CONSENT_REQUIRED"

    consent = client.post(
        "/training/consents",
        json={
            "license_id": license_id,
            "device_id": "pipe1-consent-dev",
            "consent_type": "capture_images_and_labels",
            "consent_version": "2026-06-25",
            "accepted": True,
            "app_version": "0.1.0",
        },
        headers=headers,
    )
    assert consent.status_code == 200, consent.text
    accepted = client.post("/training/snapshots", json=body, headers=headers)
    assert accepted.status_code == 200
    assert accepted.json()["snapshot_id"]
