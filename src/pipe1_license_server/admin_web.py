from __future__ import annotations

import html
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pipe1_license_server.admin import AdminService
from pipe1_license_server.admin_auth import (
    SESSION_COOKIE_NAME,
    AdminSession,
    admin_auth_is_configured,
    create_admin_session,
    load_admin_session,
    verify_admin_login,
    verify_csrf,
)
from pipe1_license_server.settings import ServerSettings


DEFAULT_FEATURE_KEYS = (
    "local_report",
    "excel_export",
    "pdf_export",
    "training_upload",
    "ai_assist",
)


def create_admin_router(settings: ServerSettings) -> APIRouter:
    service = AdminService(settings)
    router = APIRouter(prefix="/admin", include_in_schema=False)
    login_attempts: dict[tuple[str, str], list[float]] = {}

    @router.get("")
    @router.get("/")
    def admin_index(request: Request) -> Response:
        admin_session = _current_session(request, settings)
        if admin_session is None:
            return _redirect("/admin/login")
        return _redirect("/admin/licenses")

    @router.get("/login")
    def login_form(request: Request, error: str | None = None) -> Response:
        admin_session = _current_session(request, settings)
        if admin_session is not None:
            return _redirect("/admin/licenses")
        return _render_login(settings, error=error)

    @router.post("/login")
    async def login(request: Request) -> Response:
        if not admin_auth_is_configured(settings):
            return _render_login(
                settings,
                error="Admin access is not configured.",
                status_code=503,
            )
        form = await _read_form(request)
        username = form.get("username", "").strip()
        password = form.get("password", "")
        totp_code = form.get("totp_code", "").strip()
        client_host = _client_host(request)
        if _admin_login_rate_limited(
            login_attempts,
            settings,
            client_host=client_host,
            username=username,
        ):
            _record_admin_audit(
                service,
                actor=username or "unknown",
                action="admin.login.rate_limited",
                target_id=username or "unknown",
                metadata={"client_host": client_host},
            )
            return _render_login(
                settings,
                error="Too many failed login attempts. Try again later.",
                status_code=429,
            )
        if not verify_admin_login(
            settings,
            username=username,
            password=password,
            totp_code=totp_code,
        ):
            _record_admin_login_failure(
                login_attempts,
                settings,
                client_host=client_host,
                username=username,
            )
            _record_admin_audit(
                service,
                actor=username or "unknown",
                action="admin.login.failed",
                target_id=username or "unknown",
                metadata={"client_host": client_host},
            )
            return _render_login(
                settings,
                error="Invalid admin credentials.",
                status_code=401,
            )
        _clear_admin_login_failures(
            login_attempts,
            client_host=client_host,
            username=username,
        )
        _record_admin_audit(
            service,
            actor=username,
            action="admin.login.succeeded",
            target_id=username,
            metadata={"client_host": client_host},
        )
        response = _redirect("/admin/licenses")
        response.set_cookie(
            SESSION_COOKIE_NAME,
            create_admin_session(settings, username),
            **_session_cookie_options(settings),
        )
        return response

    @router.post("/logout")
    async def logout(request: Request) -> Response:
        admin_session, form, response = await _require_form(
            request, settings, "/admin/licenses"
        )
        redirect = response if response is not None else _redirect("/admin/login")
        redirect.delete_cookie(SESSION_COOKIE_NAME, path="/admin")
        return redirect

    @router.get("/licenses")
    def licenses(
        request: Request, notice: str | None = None, error: str | None = None
    ) -> Response:
        admin_session = _current_session(request, settings)
        if admin_session is None:
            return _redirect("/admin/login")
        body = _licenses_body(
            service.list_organizations(),
            service.list_licenses(),
            admin_session.csrf_token,
        )
        return _layout(
            "Licenses",
            "licenses",
            admin_session,
            body,
            notice=notice,
            error=error,
        )

    @router.post("/organizations")
    async def create_organization(request: Request) -> Response:
        admin_session, form, response = await _require_form(
            request, settings, "/admin/licenses"
        )
        if response is not None or admin_session is None:
            return response or _redirect("/admin/login")
        name = form.get("name", "").strip()
        contact_email = form.get("contact_email", "").strip() or None
        if not name:
            return _redirect("/admin/licenses", error="Organization name is required.")
        try:
            service.create_organization(
                name,
                contact_email,
                actor=admin_session.username,
            )
        except ValueError as exc:
            return _redirect("/admin/licenses", error=str(exc))
        return _redirect("/admin/licenses", notice="Organization created.")

    @router.post("/licenses")
    async def create_license(request: Request) -> Response:
        admin_session, form, response = await _require_form(
            request, settings, "/admin/licenses"
        )
        if response is not None or admin_session is None:
            return response or _redirect("/admin/login")
        try:
            device_limit = int(form.get("device_limit", "0"))
            if device_limit <= 0:
                raise ValueError("Device limit must be greater than zero.")
            license_id = service.create_license(
                organization_id=form.get("organization_id", "").strip(),
                plan=form.get("plan", "").strip() or "standard",
                device_limit=device_limit,
                expires_at=_normalize_datetime_input(form.get("expires_at", "")),
                actor=admin_session.username,
            )
        except ValueError as exc:
            return _redirect("/admin/licenses", error=str(exc))
        return _redirect(
            f"/admin/licenses/{quote(license_id)}", notice="License created."
        )

    @router.get("/licenses/{license_id}")
    def license_detail(
        request: Request,
        license_id: str,
        notice: str | None = None,
        error: str | None = None,
    ) -> Response:
        admin_session = _current_session(request, settings)
        if admin_session is None:
            return _redirect("/admin/login")
        detail = service.get_license_detail(license_id)
        if detail is None:
            return _layout(
                "License not found",
                "licenses",
                admin_session,
                "<section class='panel'><p>License was not found.</p></section>",
                error="License was not found.",
                status_code=404,
            )
        return _layout(
            f"License {license_id}",
            "licenses",
            admin_session,
            _license_detail_body(detail, admin_session.csrf_token),
            notice=notice,
            error=error,
        )

    @router.post("/licenses/{license_id}/keys")
    async def generate_key(request: Request, license_id: str) -> Response:
        admin_session, form, response = await _require_form(
            request, settings, f"/admin/licenses/{quote(license_id)}"
        )
        if response is not None or admin_session is None:
            return response or _redirect("/admin/login")
        try:
            raw_key = service.generate_license_key(
                license_id,
                key_type=form.get("key_type", "production").strip() or "production",
                actor=admin_session.username,
            )
        except ValueError as exc:
            return _redirect(f"/admin/licenses/{quote(license_id)}", error=str(exc))
        return _render_license_detail_with_secret(
            service,
            admin_session,
            license_id,
            f"New license key: {raw_key}",
        )

    @router.post("/licenses/{license_id}/keys/revoke")
    async def revoke_key(request: Request, license_id: str) -> Response:
        admin_session, form, response = await _require_form(
            request, settings, f"/admin/licenses/{quote(license_id)}"
        )
        if response is not None or admin_session is None:
            return response or _redirect("/admin/login")
        try:
            service.revoke_license_key(
                form.get("key_prefix", "").strip(),
                actor=admin_session.username,
                reason=form.get("reason", "").strip() or None,
            )
        except ValueError as exc:
            return _redirect(f"/admin/licenses/{quote(license_id)}", error=str(exc))
        return _redirect(f"/admin/licenses/{quote(license_id)}", notice="Key revoked.")

    @router.post("/licenses/{license_id}/keys/rotate")
    async def rotate_key(request: Request, license_id: str) -> Response:
        admin_session, form, response = await _require_form(
            request, settings, f"/admin/licenses/{quote(license_id)}"
        )
        if response is not None or admin_session is None:
            return response or _redirect("/admin/login")
        try:
            raw_key = service.rotate_license_key(
                form.get("key_prefix", "").strip(),
                actor=admin_session.username,
            )
        except ValueError as exc:
            return _redirect(f"/admin/licenses/{quote(license_id)}", error=str(exc))
        return _render_license_detail_with_secret(
            service,
            admin_session,
            license_id,
            f"Replacement license key: {raw_key}",
        )

    @router.post("/licenses/{license_id}/devices/deactivate")
    async def deactivate_device(request: Request, license_id: str) -> Response:
        admin_session, form, response = await _require_form(
            request, settings, f"/admin/licenses/{quote(license_id)}"
        )
        if response is not None or admin_session is None:
            return response or _redirect("/admin/login")
        try:
            service.deactivate_device(
                form.get("activation_id", "").strip(),
                actor=admin_session.username,
                reason=form.get("reason", "").strip() or None,
            )
        except ValueError as exc:
            return _redirect(f"/admin/licenses/{quote(license_id)}", error=str(exc))
        return _redirect(
            f"/admin/licenses/{quote(license_id)}", notice="Device deactivated."
        )

    @router.post("/licenses/{license_id}/features")
    async def set_feature(request: Request, license_id: str) -> Response:
        admin_session, form, response = await _require_form(
            request, settings, f"/admin/licenses/{quote(license_id)}"
        )
        if response is not None or admin_session is None:
            return response or _redirect("/admin/login")
        try:
            service.set_feature(
                license_id,
                form.get("feature_key", "").strip(),
                form.get("enabled", "false") == "true",
                actor=admin_session.username,
            )
        except ValueError as exc:
            return _redirect(f"/admin/licenses/{quote(license_id)}", error=str(exc))
        return _redirect(f"/admin/licenses/{quote(license_id)}", notice="Feature set.")

    @router.post("/licenses/{license_id}/quotas")
    async def set_quota(request: Request, license_id: str) -> Response:
        admin_session, form, response = await _require_form(
            request, settings, f"/admin/licenses/{quote(license_id)}"
        )
        if response is not None or admin_session is None:
            return response or _redirect("/admin/login")
        try:
            limit = int(form.get("limit", "0"))
            used = int(form.get("used", "0") or "0")
            service.set_ai_quota(
                license_id,
                feature_key=form.get("feature_key", "").strip() or "ai_assist",
                limit=limit,
                used=used,
                unit=form.get("unit", "").strip() or "credit",
                period=form.get("period", "").strip() or "monthly",
                overage_policy=form.get("overage_policy", "").strip() or "block",
                reset_at=form.get("reset_at", "").strip() or None,
                actor=admin_session.username,
            )
        except ValueError as exc:
            return _redirect(f"/admin/licenses/{quote(license_id)}", error=str(exc))
        return _redirect(f"/admin/licenses/{quote(license_id)}", notice="Quota set.")

    @router.get("/training")
    def training(
        request: Request, notice: str | None = None, error: str | None = None
    ) -> Response:
        admin_session = _current_session(request, settings)
        if admin_session is None:
            return _redirect("/admin/login")
        body = _training_body(service.list_training_snapshots())
        return _layout(
            "Training Uploads",
            "training",
            admin_session,
            body,
            notice=notice,
            error=error,
        )

    @router.get("/audit")
    def audit(
        request: Request, notice: str | None = None, error: str | None = None
    ) -> Response:
        admin_session = _current_session(request, settings)
        if admin_session is None:
            return _redirect("/admin/login")
        body = _audit_body(service.list_audit_events())
        return _layout(
            "Audit Logs",
            "audit",
            admin_session,
            body,
            notice=notice,
            error=error,
        )

    return router


def _render_license_detail_with_secret(
    service: AdminService,
    admin_session: AdminSession,
    license_id: str,
    secret_notice: str,
) -> HTMLResponse:
    detail = service.get_license_detail(license_id)
    if detail is None:
        return _layout(
            "License not found",
            "licenses",
            admin_session,
            "<section class='panel'><p>License was not found.</p></section>",
            error="License was not found.",
            status_code=404,
        )
    return _layout(
        f"License {license_id}",
        "licenses",
        admin_session,
        _license_detail_body(
            detail,
            admin_session.csrf_token,
            secret_notice=secret_notice,
        ),
    )


async def _require_form(
    request: Request,
    settings: ServerSettings,
    error_path: str,
) -> tuple[AdminSession | None, dict[str, str], RedirectResponse | None]:
    admin_session = _current_session(request, settings)
    if admin_session is None:
        return None, {}, _redirect("/admin/login")
    form = await _read_form(request)
    if not verify_csrf(admin_session, form.get("csrf_token")):
        return admin_session, form, _redirect(error_path, error="Invalid CSRF token.")
    return admin_session, form, None


def _admin_login_rate_limited(
    attempts: dict[tuple[str, str], list[float]],
    settings: ServerSettings,
    *,
    client_host: str,
    username: str,
) -> bool:
    limit = max(1, int(settings.admin_login_rate_limit_attempts))
    window_seconds = max(1, int(settings.admin_login_rate_limit_window_seconds))
    bucket = _recent_login_failures(
        attempts,
        client_host=client_host,
        username=username,
        window_seconds=window_seconds,
    )
    attempts[(client_host, username)] = bucket
    return len(bucket) >= limit


def _record_admin_login_failure(
    attempts: dict[tuple[str, str], list[float]],
    settings: ServerSettings,
    *,
    client_host: str,
    username: str,
) -> None:
    window_seconds = max(1, int(settings.admin_login_rate_limit_window_seconds))
    bucket = _recent_login_failures(
        attempts,
        client_host=client_host,
        username=username,
        window_seconds=window_seconds,
    )
    bucket.append(time.monotonic())
    attempts[(client_host, username)] = bucket


def _clear_admin_login_failures(
    attempts: dict[tuple[str, str], list[float]],
    *,
    client_host: str,
    username: str,
) -> None:
    attempts.pop((client_host, username), None)


def _recent_login_failures(
    attempts: dict[tuple[str, str], list[float]],
    *,
    client_host: str,
    username: str,
    window_seconds: int,
) -> list[float]:
    window_started = time.monotonic() - window_seconds
    return [
        item
        for item in attempts.get((client_host, username), [])
        if item >= window_started
    ]


def _record_admin_audit(
    service: AdminService,
    *,
    actor: str,
    action: str,
    target_id: str,
    metadata: dict[str, Any],
) -> None:
    service.record_audit_event(
        actor=actor,
        action=action,
        target_type="admin_session",
        target_id=target_id,
        metadata=metadata,
    )


def _client_host(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


async def _read_form(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _current_session(
    request: Request, settings: ServerSettings
) -> AdminSession | None:
    return load_admin_session(settings, request.cookies.get(SESSION_COOKIE_NAME))


def _session_cookie_options(settings: ServerSettings) -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": settings.app_env == "production",
        "samesite": "strict",
        "path": "/admin",
        "max_age": max(300, int(settings.admin_session_ttl_seconds)),
    }


def _redirect(path: str, **params: str | None) -> RedirectResponse:
    clean = {key: value for key, value in params.items() if value}
    if clean:
        separator = "&" if "?" in path else "?"
        path = f"{path}{separator}{urlencode(clean)}"
    return RedirectResponse(path, status_code=303)


def _render_login(
    settings: ServerSettings,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    if not admin_auth_is_configured(settings):
        error = "Admin access is not configured. Set admin username, password hash, and session secret."
        status_code = 503
    mfa_field = ""
    if settings.admin_totp_secret:
        mfa_field = """
          <label>
            <span>TOTP code</span>
            <input name="totp_code" inputmode="numeric" autocomplete="one-time-code" />
          </label>
        """
    body = f"""
      <main class="login-shell">
        <section class="login-panel">
          <div class="brand">Pipe1 Admin</div>
          <h1>Sign in</h1>
          {_alert(error, "error") if error else ""}
          <form method="post" action="/admin/login" class="stack-form">
            <label>
              <span>Username</span>
              <input name="username" autocomplete="username" required />
            </label>
            <label>
              <span>Password</span>
              <input name="password" type="password" autocomplete="current-password" required />
            </label>
            {mfa_field}
            <button type="submit">Sign in</button>
          </form>
        </section>
      </main>
    """
    return HTMLResponse(_document("Pipe1 Admin Login", body), status_code=status_code)


def _layout(
    title: str,
    active: str,
    admin_session: AdminSession,
    body: str,
    *,
    notice: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    nav = "".join(
        _nav_item(label, path, active == key)
        for key, label, path in (
            ("licenses", "Licenses", "/admin/licenses"),
            ("training", "Training", "/admin/training"),
            ("audit", "Audit", "/admin/audit"),
        )
    )
    page = f"""
      <div class="app-shell">
        <aside class="sidebar">
          <div class="brand">Pipe1</div>
          <nav>{nav}</nav>
        </aside>
        <main class="content">
          <header class="topbar">
            <div>
              <div class="eyebrow">Internal Operations</div>
              <h1>{_h(title)}</h1>
            </div>
            <form method="post" action="/admin/logout">
              <input type="hidden" name="csrf_token" value="{_h(admin_session.csrf_token)}" />
              <button class="secondary" type="submit">Sign out</button>
            </form>
          </header>
          {_alert(notice, "notice") if notice else ""}
          {_alert(error, "error") if error else ""}
          {body}
        </main>
      </div>
    """
    return HTMLResponse(_document(title, page), status_code=status_code)


def _licenses_body(
    organizations: list[dict[str, Any]],
    licenses: list[dict[str, Any]],
    csrf_token: str,
) -> str:
    org_options = "".join(
        f"<option value='{_h(org['id'])}'>{_h(org['name'])}</option>"
        for org in organizations
    )
    license_rows = [
        [
            f"<a href='/admin/licenses/{quote(str(row['id']))}'>{_h(row['id'])}</a>",
            _h(row["organization_name"]),
            _h(row["plan"]),
            _pill(row["status"]),
            f"{row['active_devices']} / {row['device_limit']}",
            str(row["key_count"]),
            _h(_short_dt(row["expires_at"])),
        ]
        for row in licenses
    ]
    return f"""
      <section class="grid two">
        <div class="panel">
          <h2>Create Organization</h2>
          <form method="post" action="/admin/organizations" class="stack-form compact">
            {_csrf(csrf_token)}
            <label><span>Name</span><input name="name" required /></label>
            <label><span>Contact email</span><input name="contact_email" type="email" /></label>
            <button type="submit">Create</button>
          </form>
        </div>
        <div class="panel">
          <h2>Create License</h2>
          <form method="post" action="/admin/licenses" class="stack-form compact">
            {_csrf(csrf_token)}
            <label><span>Organization</span><select name="organization_id" required>{org_options}</select></label>
            <label><span>Plan</span><input name="plan" value="standard" required /></label>
            <label><span>Device limit</span><input name="device_limit" type="number" min="1" value="1" required /></label>
            <label><span>Expires at</span><input name="expires_at" type="date" required /></label>
            <button type="submit">Create</button>
          </form>
        </div>
      </section>
      <section class="panel">
        <h2>Licenses</h2>
        {_table(["License", "Organization", "Plan", "Status", "Devices", "Keys", "Expires"], license_rows)}
      </section>
      <section class="panel">
        <h2>Organizations</h2>
        {_table(
            ["Organization", "Status", "Contact", "Licenses", "Created"],
            [
                [
                    _h(org["name"]),
                    _pill(org["status"]),
                    _h(org.get("contact_email") or ""),
                    str(org["license_count"]),
                    _h(_short_dt(org["created_at"])),
                ]
                for org in organizations
            ],
        )}
      </section>
    """


def _license_detail_body(
    detail: dict[str, Any],
    csrf_token: str,
    *,
    secret_notice: str | None = None,
) -> str:
    license_id = str(detail["id"])
    feature_options = "".join(
        f"<option value='{_h(feature)}'>{_h(feature)}</option>"
        for feature in DEFAULT_FEATURE_KEYS
    )
    secret_panel = ""
    if secret_notice:
        label, _, raw_key = secret_notice.partition(": ")
        secret_panel = f"""
          <section class="secret-panel">
            <strong>{_h(label)}</strong>
            <code>{_h(raw_key)}</code>
          </section>
        """
    key_rows = []
    for key in detail["keys"]:
        actions = ""
        if key["status"] == "active":
            actions = f"""
              <div class="inline-actions">
                <form method="post" action="/admin/licenses/{quote(license_id)}/keys/revoke">
                  {_csrf(csrf_token)}
                  <input type="hidden" name="key_prefix" value="{_h(key['key_prefix'])}" />
                  <input name="reason" placeholder="reason" />
                  <button class="danger" type="submit">Revoke</button>
                </form>
                <form method="post" action="/admin/licenses/{quote(license_id)}/keys/rotate">
                  {_csrf(csrf_token)}
                  <input type="hidden" name="key_prefix" value="{_h(key['key_prefix'])}" />
                  <button class="secondary" type="submit">Rotate</button>
                </form>
              </div>
            """
        key_rows.append(
            [
                _h(key["key_prefix"]),
                _pill(key["status"]),
                _h(key["key_type"]),
                _h(_short_dt(key["issued_at"])),
                _h(_short_dt(key["last_used_at"])),
                actions,
            ]
        )
    device_rows = []
    for device in detail["devices"]:
        actions = ""
        if device["status"] == "active":
            actions = f"""
              <form method="post" action="/admin/licenses/{quote(license_id)}/devices/deactivate" class="inline-form">
                {_csrf(csrf_token)}
                <input type="hidden" name="activation_id" value="{_h(device['id'])}" />
                <input name="reason" placeholder="reason" />
                <button class="danger" type="submit">Deactivate</button>
              </form>
            """
        device_rows.append(
            [
                _h(device["device_name"] or ""),
                _h(device["device_id"]),
                _pill(device["status"]),
                _h(device["os_name"] or ""),
                _h(device["app_version"] or ""),
                _h(_short_dt(device["last_validated_at"])),
                actions,
            ]
        )
    return f"""
      {secret_panel}
      <section class="metrics">
        {_metric("Organization", detail["organization_name"])}
        {_metric("Plan", detail["plan"])}
        {_metric("Status", detail["status"])}
        {_metric("Devices", f"{detail['active_devices']} / {detail['device_limit']}")}
        {_metric("Expires", _short_dt(detail["expires_at"]))}
      </section>
      <section class="grid two">
        <div class="panel">
          <h2>Issue Key</h2>
          <form method="post" action="/admin/licenses/{quote(license_id)}/keys" class="stack-form compact">
            {_csrf(csrf_token)}
            <label><span>Type</span><input name="key_type" value="production" required /></label>
            <button type="submit">Generate key</button>
          </form>
        </div>
        <div class="panel">
          <h2>Feature Flag</h2>
          <form method="post" action="/admin/licenses/{quote(license_id)}/features" class="stack-form compact">
            {_csrf(csrf_token)}
            <label><span>Feature</span><select name="feature_key">{feature_options}</select></label>
            <label><span>Enabled</span><select name="enabled"><option value="true">true</option><option value="false">false</option></select></label>
            <button type="submit">Set feature</button>
          </form>
        </div>
      </section>
      <section class="panel">
        <h2>AI / Server Quota</h2>
        <form method="post" action="/admin/licenses/{quote(license_id)}/quotas" class="inline-grid-form">
          {_csrf(csrf_token)}
          <label><span>Feature</span><input name="feature_key" value="ai_assist" /></label>
          <label><span>Period</span><input name="period" value="monthly" /></label>
          <label><span>Unit</span><input name="unit" value="credit" /></label>
          <label><span>Limit</span><input name="limit" type="number" min="0" value="0" /></label>
          <label><span>Used</span><input name="used" type="number" min="0" value="0" /></label>
          <label><span>Overage</span><select name="overage_policy"><option value="block">block</option><option value="allow">allow</option></select></label>
          <button type="submit">Set quota</button>
        </form>
      </section>
      <section class="panel"><h2>Keys</h2>{_table(["Prefix", "Status", "Type", "Issued", "Last used", "Actions"], key_rows)}</section>
      <section class="panel"><h2>Devices</h2>{_table(["Name", "Device ID", "Status", "OS", "App", "Last validated", "Actions"], device_rows)}</section>
      <section class="grid two">
        <div class="panel"><h2>Features</h2>{_table(["Feature", "Enabled"], [[_h(item["feature_key"]), _pill(str(item["enabled"]).lower())] for item in detail["features"]])}</div>
        <div class="panel"><h2>Quotas</h2>{_table(["Feature", "Period", "Limit", "Used", "Remaining"], [[_h(item["feature_key"]), _h(item["period"]), str(item["limit"]), str(item["used"]), str(item["remaining"])] for item in detail["quotas"]])}</div>
      </section>
      <section class="panel"><h2>Training Snapshots</h2>{_table(["Snapshot", "Device", "Report", "Status", "Created"], [[_h(item["id"]), _h(item["device_id"]), _h(item["local_report_id"]), _pill(item["status"]), _h(_short_dt(item["created_at"]))] for item in detail["training_snapshots"]])}</section>
      <section class="panel"><h2>Usage Events</h2>{_table(["Feature", "Quantity", "Unit", "Request", "Created"], [[_h(item["feature_key"]), str(item["quantity"]), _h(item["unit"]), _h(item["request_id"] or ""), _h(_short_dt(item["created_at"]))] for item in detail["usage_events"]])}</section>
    """


def _training_body(rows: list[dict[str, Any]]) -> str:
    return f"""
      <section class="panel">
        <h2>Training Uploads</h2>
        {_table(
            ["Snapshot", "License", "Device", "Report", "Samples", "Status", "Created"],
            [
                [
                    _h(row["id"]),
                    f"<a href='/admin/licenses/{quote(str(row['license_id']))}'>{_h(row['license_id'])}</a>",
                    _h(row["device_id"]),
                    _h(row["local_report_id"]),
                    str(row["sample_count"]),
                    _pill(row["status"]),
                    _h(_short_dt(row["created_at"])),
                ]
                for row in rows
            ],
        )}
      </section>
    """


def _audit_body(rows: list[dict[str, Any]]) -> str:
    return f"""
      <section class="panel">
        <h2>Audit Logs</h2>
        {_table(
            ["Created", "Actor", "Action", "Target", "Metadata"],
            [
                [
                    _h(_short_dt(row["created_at"])),
                    _h(row["actor"]),
                    _h(row["action"]),
                    _h(f"{row['target_type']}:{row['target_id']}"),
                    _h(str(row["metadata"])),
                ]
                for row in rows
            ],
        )}
      </section>
    """


def _document(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_h(title)}</title>
  <style>{_css()}</style>
</head>
<body>{body}</body>
</html>"""


def _css() -> str:
    return """
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1d2433; background: #f6f7f9; letter-spacing: 0; }
a { color: #0f6b71; text-decoration: none; font-weight: 650; }
a:hover { text-decoration: underline; }
.app-shell { min-height: 100vh; display: grid; grid-template-columns: 236px minmax(0, 1fr); }
.sidebar { background: #10202b; color: #f8fbfc; padding: 24px 18px; }
.brand { font-size: 20px; font-weight: 800; margin-bottom: 28px; }
nav { display: grid; gap: 6px; }
.nav-item { display: block; padding: 10px 12px; border-radius: 6px; color: #c9d6db; font-weight: 700; }
.nav-item.active, .nav-item:hover { background: #1d3a49; color: #ffffff; text-decoration: none; }
.content { padding: 26px; min-width: 0; }
.topbar { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 20px; }
.eyebrow { color: #667085; font-size: 12px; font-weight: 800; text-transform: uppercase; }
h1 { margin: 2px 0 0; font-size: 28px; line-height: 1.2; }
h2 { margin: 0 0 14px; font-size: 17px; }
.grid { display: grid; gap: 16px; margin-bottom: 16px; }
.grid.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.panel, .secret-panel { background: #ffffff; border: 1px solid #d8dee6; border-radius: 8px; padding: 16px; margin-bottom: 16px; box-shadow: 0 1px 2px rgba(16, 32, 43, 0.04); }
.secret-panel { border-color: #b3832f; background: #fff8e8; display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.secret-panel code { padding: 8px 10px; border-radius: 6px; background: #fff; border: 1px solid #dfc27d; font-weight: 800; }
.metrics { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.metric { background: #ffffff; border: 1px solid #d8dee6; border-radius: 8px; padding: 14px; }
.metric span { display: block; color: #667085; font-size: 12px; font-weight: 800; margin-bottom: 6px; }
.metric strong { display: block; font-size: 17px; overflow-wrap: anywhere; }
.alert { border-radius: 8px; padding: 12px 14px; margin-bottom: 16px; font-weight: 700; }
.alert.notice { background: #e9f7ef; color: #166534; border: 1px solid #a7e3ba; }
.alert.error { background: #fff0f0; color: #a11d1d; border: 1px solid #f1b1b1; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 10px 9px; border-bottom: 1px solid #e5e9ef; text-align: left; vertical-align: top; }
th { color: #526070; font-size: 12px; text-transform: uppercase; background: #f8fafb; }
tr:last-child td { border-bottom: 0; }
.empty { color: #667085; margin: 0; }
.pill { display: inline-flex; align-items: center; min-height: 24px; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 800; background: #edf2f7; color: #334155; }
.pill.active, .pill.true, .pill.completed, .pill.receiving { background: #dff7e8; color: #166534; }
.pill.revoked, .pill.false, .pill.deactivated, .pill.error { background: #ffe5e5; color: #991b1b; }
.stack-form { display: grid; gap: 12px; }
.stack-form.compact { gap: 10px; }
.inline-grid-form { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)) auto; gap: 10px; align-items: end; }
.inline-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.inline-form { display: flex; gap: 8px; align-items: center; }
label span { display: block; color: #536170; font-size: 12px; font-weight: 800; margin-bottom: 5px; }
input, select { width: 100%; min-height: 36px; border: 1px solid #cbd5df; border-radius: 6px; padding: 7px 9px; background: #ffffff; color: #1d2433; font: inherit; }
button { min-height: 36px; border: 0; border-radius: 6px; padding: 8px 12px; background: #0f6b71; color: #ffffff; font: inherit; font-weight: 800; cursor: pointer; white-space: nowrap; }
button.secondary { background: #475467; }
button.danger { background: #b42318; }
.login-shell { min-height: 100vh; display: grid; place-items: center; padding: 24px; background: #eef2f5; }
.login-panel { width: min(420px, 100%); background: #ffffff; border: 1px solid #d8dee6; border-radius: 8px; padding: 24px; box-shadow: 0 8px 30px rgba(16, 32, 43, 0.12); }
@media (max-width: 920px) {
  .app-shell { grid-template-columns: 1fr; }
  .sidebar { position: static; padding: 16px; }
  nav { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .content { padding: 16px; }
  .grid.two, .metrics, .inline-grid-form { grid-template-columns: 1fr; }
  .topbar { align-items: flex-start; }
}
"""


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "<p class='empty'>No records.</p>"
    head = "".join(f"<th>{_h(header)}</th>" for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def _nav_item(label: str, path: str, active: bool) -> str:
    cls = "nav-item active" if active else "nav-item"
    return f"<a class='{cls}' href='{_h(path)}'>{_h(label)}</a>"


def _alert(message: str | None, kind: str) -> str:
    return f"<div class='alert {_h(kind)}'>{_h(message or '')}</div>"


def _metric(label: str, value: Any) -> str:
    return f"<div class='metric'><span>{_h(label)}</span><strong>{_h(str(value or ''))}</strong></div>"


def _pill(value: Any) -> str:
    text = str(value)
    cls = "".join(ch if ch.isalnum() else "-" for ch in text.lower())
    return f"<span class='pill {_h(cls)}'>{_h(text)}</span>"


def _csrf(token: str) -> str:
    return f"<input type='hidden' name='csrf_token' value='{_h(token)}' />"


def _short_dt(value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    return text.replace("T", " ")[:19]


def _normalize_datetime_input(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("expires_at is required")
    if len(normalized) == 10 and normalized[4] == "-" and normalized[7] == "-":
        return f"{normalized}T23:59:59+00:00"
    return normalized


def _h(value: Any) -> str:
    return html.escape(str(value), quote=True)
