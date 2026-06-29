from __future__ import annotations

import hashlib
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


def test_deactivated_device_cannot_validate_cached_activation(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    admin = AdminService(settings)
    org_id = admin.create_organization("Deactivate Co", "ops@example.com")
    license_id = admin.create_license(
        organization_id=org_id,
        plan="standard",
        device_limit=1,
        expires_at="2027-06-30T23:59:59Z",
        features={"local_report": True},
    )
    raw_key = admin.generate_license_key(license_id)
    activation = _activate(client, raw_key, "pipe1-deactivated-dev")

    admin.deactivate_device(activation["activation_id"], actor="support")
    validation = client.post(
        "/licenses/validate",
        json={
            "activation_id": activation["activation_id"],
            "device_id": "pipe1-deactivated-dev",
            "app_version": "0.1.0",
        },
    )

    assert validation.status_code == 403
    assert validation.json()["code"] == "INACTIVE_ACTIVATION"


def test_deactivated_same_device_can_be_reactivated_with_license_key(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    admin = AdminService(settings)
    org_id = admin.create_organization("Reactivate Co", "ops@example.com")
    license_id = admin.create_license(
        organization_id=org_id,
        plan="standard",
        device_limit=1,
        expires_at="2027-06-30T23:59:59Z",
        features={"local_report": True},
    )
    raw_key = admin.generate_license_key(license_id)
    first = _activate(client, raw_key, "pipe1-reactivated-dev")

    admin.deactivate_device(first["activation_id"], actor="support")
    second = _activate(client, raw_key, "pipe1-reactivated-dev")
    validation = client.post(
        "/licenses/validate",
        json={
            "activation_id": second["activation_id"],
            "device_id": "pipe1-reactivated-dev",
            "app_version": "0.1.0",
        },
    )
    active_devices = admin.list_device_activations(license_id)
    all_devices = admin.list_device_activations(license_id, active_only=False)
    all_device_statuses = [device["status"] for device in all_devices]

    assert second["activation_id"] == first["activation_id"]
    assert validation.status_code == 200
    assert [device["device_id"] for device in active_devices] == [
        "pipe1-reactivated-dev"
    ]
    assert all_device_statuses == ["active"]
    assert all_devices[0]["deactivated_at"] is None


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


def test_admin_cli_can_manage_app_releases(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    out = StringIO()
    installer = tmp_path / "Pipe1-1.2.3-x64.msi"
    installer.write_bytes(b"pipe1 test installer")
    expected_sha = hashlib.sha256(installer.read_bytes()).hexdigest()

    created = run_cli(
        [
            "release",
            "create",
            "--version",
            "1.2.3",
            "--platform",
            "windows",
            "--arch",
            "x64",
            "--channel",
            "stable",
            "--download-url",
            "https://license.example.com/downloads/Pipe1-1.2.3-x64.msi",
            "--file",
            str(installer),
            "--notes",
            "Windows production release",
        ],
        settings=settings,
        stdout=out,
    )
    published = run_cli(
        ["release", "publish", "--version", "1.2.3"],
        settings=settings,
        stdout=out,
    )
    listed = run_cli(["release", "list"], settings=settings, stdout=out)
    disabled = run_cli(
        ["release", "disable", "--version", "1.2.3"],
        settings=settings,
        stdout=out,
    )

    assert created["release"]["id"].startswith("rel_")
    assert created["release"]["sha256"] == expected_sha
    assert created["release"]["size_bytes"] == len(b"pipe1 test installer")
    assert published["release"]["status"] == "published"
    assert listed["releases"][0]["version"] == "1.2.3"
    assert disabled["release"]["status"] == "disabled"
    audit_actions = [event["action"] for event in AdminService(settings).list_audit_events()]
    assert "app_release.create" in audit_actions
    assert "app_release.publish" in audit_actions
    assert "app_release.disable" in audit_actions


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
