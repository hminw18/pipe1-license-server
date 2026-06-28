from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from pipe1_license_server.admin import AdminService
from pipe1_license_server.admin_auth import (
    hash_admin_password,
    totp_code,
    verify_admin_password,
)
from pipe1_license_server.app import create_app
from pipe1_license_server.settings import ServerSettings
from pipe1_license_server.signing import generate_private_key_b64


def _settings(tmp_path: Path, **overrides: object) -> ServerSettings:
    tmp_path.mkdir(parents=True, exist_ok=True)
    values = {
        "database_url": f"sqlite+pysqlite:///{tmp_path / 'license.db'}",
        "signing_private_key": generate_private_key_b64(),
        "signing_key_id": "admin-web-test-key",
        "app_env": "test",
        "admin_username": "admin",
        "admin_password_hash": hash_admin_password("correct-password"),
        "admin_session_secret": "test-admin-session-secret-that-is-long-enough",
    }
    values.update(overrides)
    return ServerSettings(**values)


def _csrf(html: str) -> str:
    match = re.search(r"name='csrf_token' value='([^']+)'", html)
    assert match is not None, html
    return match.group(1)


def _license_key(html: str) -> str:
    match = re.search(r"PIPE1-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", html)
    assert match is not None, html
    return match.group(0)


def _login(client: TestClient) -> str:
    response = client.post(
        "/admin/login",
        data={"username": "admin", "password": "correct-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/admin/licenses")
    assert page.status_code == 200
    return _csrf(page.text)


def test_admin_password_hash_round_trip() -> None:
    encoded = hash_admin_password("secret")
    assert verify_admin_password("secret", encoded)
    assert not verify_admin_password("wrong", encoded)


def test_admin_web_requires_login_and_manages_license(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    blocked = client.get("/admin/licenses", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/admin/login"

    wrong = client.post(
        "/admin/login",
        data={"username": "admin", "password": "wrong-password"},
    )
    assert wrong.status_code == 401

    csrf = _login(client)
    created_org = client.post(
        "/admin/organizations",
        data={
            "csrf_token": csrf,
            "name": "Portal Co",
            "contact_email": "ops@example.com",
        },
        follow_redirects=False,
    )
    assert created_org.status_code == 303

    admin = AdminService(settings)
    org_id = admin.list_organizations()[0]["id"]
    created_license = client.post(
        "/admin/licenses",
        data={
            "csrf_token": csrf,
            "organization_id": org_id,
            "plan": "standard",
            "device_limit": "1",
            "expires_at": "2027-06-30",
        },
        follow_redirects=False,
    )
    assert created_license.status_code == 303
    license_id = admin.list_licenses()[0]["id"]

    key_response = client.post(
        f"/admin/licenses/{license_id}/keys",
        data={"csrf_token": csrf, "key_type": "production"},
    )
    assert key_response.status_code == 200
    raw_key = _license_key(key_response.text)

    activation = client.post(
        "/licenses/activate",
        json={
            "license_key": raw_key,
            "device_id": "pipe1-admin-web-device",
            "device_name": "admin-web-device",
            "os_name": "Windows",
            "os_version": "11",
            "app_version": "0.1.0",
        },
    )
    assert activation.status_code == 200, activation.text
    activation_id = activation.json()["activation_id"]

    feature = client.post(
        f"/admin/licenses/{license_id}/features",
        data={
            "csrf_token": csrf,
            "feature_key": "training_upload",
            "enabled": "true",
        },
        follow_redirects=False,
    )
    assert feature.status_code == 303

    quota = client.post(
        f"/admin/licenses/{license_id}/quotas",
        data={
            "csrf_token": csrf,
            "feature_key": "ai_assist",
            "period": "monthly",
            "unit": "credit",
            "limit": "2500",
            "used": "100",
            "overage_policy": "block",
        },
        follow_redirects=False,
    )
    assert quota.status_code == 303

    deactivated = client.post(
        f"/admin/licenses/{license_id}/devices/deactivate",
        data={
            "csrf_token": csrf,
            "activation_id": activation_id,
            "reason": "replacement",
        },
        follow_redirects=False,
    )
    assert deactivated.status_code == 303

    detail = admin.get_license_detail(license_id)
    assert detail is not None
    assert detail["features"][0]["feature_key"] == "training_upload"
    assert detail["features"][0]["enabled"] is True
    assert detail["quotas"][0]["limit"] == 2500
    assert detail["devices"][0]["status"] == "deactivated"

    audit = client.get("/admin/audit")
    assert audit.status_code == 200
    assert "license_key.issue" in audit.text
    assert "device.deactivate" in audit.text


def test_admin_web_requires_csrf_for_mutations(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))
    _login(client)

    response = client.post(
        "/admin/organizations",
        data={"name": "No CSRF Co"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Invalid+CSRF+token" in response.headers["location"]
    assert AdminService(settings).list_organizations() == []


def test_admin_web_optional_totp(tmp_path: Path) -> None:
    secret = "JBSWY3DPEHPK3PXP"
    settings = _settings(tmp_path, admin_totp_secret=secret)
    client = TestClient(create_app(settings))

    missing_code = client.post(
        "/admin/login",
        data={"username": "admin", "password": "correct-password"},
    )
    assert missing_code.status_code == 401

    logged_in = client.post(
        "/admin/login",
        data={
            "username": "admin",
            "password": "correct-password",
            "totp_code": totp_code(secret),
        },
        follow_redirects=False,
    )
    assert logged_in.status_code == 303


def test_admin_login_rate_limit_and_audit_log(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        admin_login_rate_limit_attempts=2,
        admin_login_rate_limit_window_seconds=300,
    )
    client = TestClient(create_app(settings))

    for _ in range(2):
        failed = client.post(
            "/admin/login",
            data={"username": "admin", "password": "wrong-password"},
        )
        assert failed.status_code == 401

    limited = client.post(
        "/admin/login",
        data={"username": "admin", "password": "wrong-password"},
    )
    assert limited.status_code == 429

    audit_actions = [event["action"] for event in AdminService(settings).list_audit_events()]
    assert audit_actions.count("admin.login.failed") == 2
    assert "admin.login.rate_limited" in audit_actions


def test_admin_successful_login_is_audited(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = TestClient(create_app(settings))

    _login(client)

    audit_actions = [event["action"] for event in AdminService(settings).list_audit_events()]
    assert "admin.login.succeeded" in audit_actions


def test_production_admin_requires_password_hash_and_totp(tmp_path: Path) -> None:
    missing_totp = _settings(tmp_path, app_env="production", admin_totp_secret=None)
    missing_totp_response = TestClient(create_app(missing_totp)).get("/admin/login")
    assert missing_totp_response.status_code == 503

    plaintext_password = _settings(
        tmp_path / "plaintext",
        app_env="production",
        admin_password_hash=None,
        admin_password="correct-password",
        admin_totp_secret="JBSWY3DPEHPK3PXP",
    )
    plaintext_response = TestClient(create_app(plaintext_password)).get("/admin/login")
    assert plaintext_response.status_code == 503


def test_security_headers_are_set(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        app_env="production",
        admin_totp_secret="JBSWY3DPEHPK3PXP",
    )
    response = TestClient(create_app(settings)).get("/health")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "camera=()" in response.headers["Permissions-Policy"]
    assert response.headers["Strict-Transport-Security"].startswith("max-age=31536000")


def test_admin_web_disabled_until_configured(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        admin_username=None,
        admin_password_hash=None,
        admin_session_secret=None,
    )
    client = TestClient(create_app(settings))

    response = client.get("/admin/login")
    assert response.status_code == 503
    assert "Admin access is not configured" in response.text
