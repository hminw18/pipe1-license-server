from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pipe1_license_server.admin import AdminService
from pipe1_license_server.app import create_app
from pipe1_license_server.settings import ServerSettings
from pipe1_license_server.signing import generate_private_key_b64


def _settings(tmp_path: Path) -> ServerSettings:
    private_key = generate_private_key_b64()
    return ServerSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'license.db'}",
        signing_private_key=private_key,
        signing_key_id="test-key",
        app_env="test",
    )


def test_license_activation_validate_and_device_limit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    admin = AdminService(settings)
    org_id = admin.create_organization("ABC Construction", "ops@example.com")
    license_id = admin.create_license(
        organization_id=org_id,
        plan="standard",
        device_limit=1,
        expires_at="2027-06-30T23:59:59Z",
        features={"local_report": True, "excel_export": True, "pdf_export": True},
        ai_quota={"feature_key": "ai_assist", "limit": 1000, "unit": "credit"},
    )
    raw_key = admin.generate_license_key(license_id, key_type="production")

    stored_key = admin.find_license_key_by_prefix(raw_key[:10])
    assert stored_key is not None
    assert stored_key.key_hash != raw_key
    assert stored_key.key_prefix == raw_key[:10]

    client = TestClient(app)
    activation = client.post(
        "/licenses/activate",
        json={
            "license_key": raw_key,
            "device_id": "pipe1-dev-001",
            "device_name": "field-pc-1",
            "os_name": "Windows",
            "os_version": "11",
            "app_version": "0.1.0",
        },
    )
    assert activation.status_code == 200
    activation_payload = activation.json()
    assert activation_payload["activation_id"]
    assert activation_payload["device_upload_token"]
    entitlement = activation_payload["entitlement"]
    assert entitlement["payload"]["device_id"] == "pipe1-dev-001"
    assert entitlement["payload"]["features"]["local_report"] is True
    assert entitlement["payload"]["ai_quota"]["limit"] == 1000
    assert entitlement["signature"]

    validation = client.post(
        "/licenses/validate",
        json={
            "activation_id": activation_payload["activation_id"],
            "device_id": "pipe1-dev-001",
            "app_version": "0.1.0",
        },
    )
    assert validation.status_code == 200
    assert validation.json()["status"] == "valid"
    assert validation.json()["entitlement"]["payload"]["device_id"] == "pipe1-dev-001"

    second_device = client.post(
        "/licenses/activate",
        json={
            "license_key": raw_key,
            "device_id": "pipe1-dev-002",
            "device_name": "field-pc-2",
            "os_name": "Windows",
            "os_version": "11",
            "app_version": "0.1.0",
        },
    )
    assert second_device.status_code == 409
    assert second_device.json()["code"] == "DEVICE_LIMIT_EXCEEDED"


def test_admin_revoke_key_blocks_activation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    admin = AdminService(settings)
    org_id = admin.create_organization("Revoked Co", None)
    license_id = admin.create_license(
        organization_id=org_id,
        plan="standard",
        device_limit=3,
        expires_at="2027-06-30T23:59:59Z",
    )
    raw_key = admin.generate_license_key(license_id)
    admin.revoke_license_key(raw_key[:10], actor="test")

    response = TestClient(app).post(
        "/licenses/activate",
        json={
            "license_key": raw_key,
            "device_id": "pipe1-dev-003",
            "os_name": "Windows",
            "os_version": "11",
            "app_version": "0.1.0",
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "REVOKED_LICENSE_KEY"
