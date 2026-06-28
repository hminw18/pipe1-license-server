from __future__ import annotations

from io import StringIO
from pathlib import Path

from fastapi.testclient import TestClient

from pipe1_license_server.admin import AdminService
from pipe1_license_server.admin_cli import run_cli
from pipe1_license_server.app import create_app
from pipe1_license_server.settings import ServerSettings
from pipe1_license_server.signing import generate_private_key_b64


def _settings(tmp_path: Path) -> ServerSettings:
    return ServerSettings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'license.db'}",
        signing_private_key=generate_private_key_b64(),
        signing_key_id="admin-test-key",
        app_env="test",
    )


def _activate(client: TestClient, raw_key: str, device_id: str) -> dict:
    response = client.post(
        "/licenses/activate",
        json={
            "license_key": raw_key,
            "device_id": device_id,
            "device_name": device_id,
            "os_name": "Windows",
            "os_version": "11",
            "app_version": "0.1.0",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_admin_device_deactivation_key_rotation_feature_and_quota(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    admin = AdminService(settings)
    org_id = admin.create_organization("Ops Co", "ops@example.com")
    license_id = admin.create_license(
        organization_id=org_id,
        plan="standard",
        device_limit=1,
        expires_at="2027-06-30T23:59:59Z",
        features={"local_report": True, "training_upload": False},
    )
    old_key = admin.generate_license_key(license_id)
    first = _activate(client, old_key, "pipe1-admin-dev-001")

    blocked = client.post(
        "/licenses/activate",
        json={
            "license_key": old_key,
            "device_id": "pipe1-admin-dev-002",
            "os_name": "Windows",
            "os_version": "11",
            "app_version": "0.1.0",
        },
    )
    assert blocked.status_code == 409

    admin.deactivate_device(first["activation_id"], actor="support")
    second = _activate(client, old_key, "pipe1-admin-dev-002")
    assert second["entitlement"]["payload"]["features"]["training_upload"] is False

    admin.set_feature(license_id, "training_upload", True, actor="support")
    admin.set_ai_quota(
        license_id,
        feature_key="ai_assist",
        limit=2500,
        unit="credit",
        period="monthly",
        actor="support",
    )
    validation = client.post(
        "/licenses/validate",
        json={
            "activation_id": second["activation_id"],
            "device_id": "pipe1-admin-dev-002",
            "app_version": "0.1.0",
        },
    )
    assert validation.status_code == 200
    entitlement = validation.json()["entitlement"]["payload"]
    assert entitlement["features"]["training_upload"] is True
    assert entitlement["ai_quota"]["limit"] == 2500

    replacement_key = admin.rotate_license_key(old_key[:10], actor="support")
    revoked = client.post(
        "/licenses/activate",
        json={
            "license_key": old_key,
            "device_id": "pipe1-admin-dev-003",
            "os_name": "Windows",
            "os_version": "11",
            "app_version": "0.1.0",
        },
    )
    assert revoked.status_code == 403
    assert revoked.json()["code"] == "REVOKED_LICENSE_KEY"

    devices = admin.list_device_activations(license_id)
    assert [device["device_id"] for device in devices] == ["pipe1-admin-dev-002"]
    audit_actions = [event["action"] for event in admin.list_audit_events()]
    assert "license_key.rotate" in audit_actions
    assert "device.deactivate" in audit_actions
    assert replacement_key.startswith("PIPE1-")


def test_admin_cli_can_issue_and_revoke_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    out = StringIO()
    org = run_cli(
        ["org", "create", "--name", "CLI Co", "--contact-email", "cli@example.com"],
        settings=settings,
        stdout=out,
    )
    license_row = run_cli(
        [
            "license",
            "create",
            "--org",
            org["id"],
            "--plan",
            "standard",
            "--device-limit",
            "2",
            "--expires-at",
            "2027-06-30T23:59:59Z",
        ],
        settings=settings,
        stdout=out,
    )
    key = run_cli(
        ["key", "generate", "--license", license_row["id"]],
        settings=settings,
        stdout=out,
    )
    revoked = run_cli(
        ["key", "revoke", "--key-prefix", key["license_key"][:10]],
        settings=settings,
        stdout=out,
    )

    assert org["id"].startswith("org_")
    assert license_row["id"].startswith("lic_")
    assert key["license_key"].startswith("PIPE1-")
    assert revoked["status"] == "revoked"


def test_admin_cli_can_list_license_server_records(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    out = StringIO()
    org = run_cli(
        ["org", "create", "--name", "Lookup Co", "--contact-email", "lookup@example.com"],
        settings=settings,
        stdout=out,
    )
    license_row = run_cli(
        [
            "license",
            "create",
            "--org",
            org["id"],
            "--plan",
            "standard",
            "--device-limit",
            "2",
            "--expires-at",
            "2027-06-30T23:59:59Z",
        ],
        settings=settings,
        stdout=out,
    )
    key = run_cli(
        ["key", "generate", "--license", license_row["id"]],
        settings=settings,
        stdout=out,
    )
    activation = _activate(client, key["license_key"], "pipe1-lookup-dev-001")
    run_cli(
        [
            "feature",
            "set",
            "--license",
            license_row["id"],
            "--feature",
            "training_upload",
            "--enabled",
            "true",
        ],
        settings=settings,
        stdout=out,
    )
    run_cli(
        [
            "quota",
            "set",
            "--license",
            license_row["id"],
            "--feature",
            "ai_assist",
            "--unit",
            "credit",
            "--limit",
            "1000",
            "--period",
            "monthly",
        ],
        settings=settings,
        stdout=out,
    )

    organizations = run_cli(["org", "list"], settings=settings, stdout=out)
    licenses = run_cli(["license", "list"], settings=settings, stdout=out)
    keys = run_cli(
        ["key", "list", "--license", license_row["id"]],
        settings=settings,
        stdout=out,
    )
    devices = run_cli(
        ["devices", "list", "--license", license_row["id"], "--all"],
        settings=settings,
        stdout=out,
    )
    features = run_cli(
        ["feature", "list", "--license", license_row["id"]],
        settings=settings,
        stdout=out,
    )
    quotas = run_cli(
        ["quota", "list", "--license", license_row["id"]],
        settings=settings,
        stdout=out,
    )

    assert organizations["organizations"][0]["name"] == "Lookup Co"
    assert organizations["organizations"][0]["license_count"] == 1
    assert licenses["licenses"][0]["organization_name"] == "Lookup Co"
    assert licenses["licenses"][0]["active_devices"] == 1
    assert keys["keys"][0]["key_prefix"] == key["license_key"][:10]
    assert "key_hash" not in keys["keys"][0]
    assert devices["devices"][0]["id"] == activation["activation_id"]
    assert devices["devices"][0]["device_id"] == "pipe1-lookup-dev-001"
    assert features["features"][0]["feature"] == "training_upload"
    assert features["features"][0]["enabled"] is True
    assert quotas["quotas"][0]["feature"] == "ai_assist"
    assert quotas["quotas"][0]["remaining"] == 1000
