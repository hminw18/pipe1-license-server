from __future__ import annotations

import hashlib
import base64
import secrets
import time
from io import BytesIO
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pipe1_license_server.admin import hash_license_key
from pipe1_license_server.admin_web import create_admin_router
from pipe1_license_server.db import create_session_factory, init_db
from pipe1_license_server.models import (
    AppRelease,
    DeviceActivation,
    EntitlementSnapshot,
    License,
    LicenseFeature,
    LicenseKey,
    LicenseUsageQuota,
    TrainingConsent,
    TrainingSample,
    TrainingSnapshot,
)
from pipe1_license_server.releases import (
    compare_versions,
    latest_published_release,
    normalize_release_target,
    validate_version,
)
from pipe1_license_server.settings import ServerSettings
from pipe1_license_server.signing import EntitlementSigner, canonical_json_bytes


ALLOWED_TRAINING_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    id_prefix = f"{prefix}_"
    if len(id_prefix) >= 36:
        raise ValueError("id prefix is too long")
    return f"{id_prefix}{uuid4().hex[: 36 - len(id_prefix)]}"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware(value).isoformat()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message},
    )


def _generate_upload_token() -> str:
    return f"put_{secrets.token_urlsafe(32)}"


def _hash_upload_token(upload_token: str) -> str:
    return hashlib.sha256(upload_token.encode("utf-8")).hexdigest()


def _extract_bearer_token(authorization: str | None) -> str | JSONResponse:
    if not authorization:
        return _error("MISSING_UPLOAD_TOKEN", "Upload token is required.", 401)
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return _error("MISSING_UPLOAD_TOKEN", "Bearer upload token is required.", 401)
    return token


def _activation_rate_limit_error(
    attempts: dict[tuple[str, str], list[float]],
    settings: ServerSettings,
    *,
    client_host: str,
    key_hint: str,
) -> JSONResponse | None:
    now = time.monotonic()
    window_started = now - settings.activation_rate_limit_window_seconds
    bucket_key = (client_host, key_hint)
    bucket = [
        item for item in attempts.get(bucket_key, []) if item >= window_started
    ]
    if len(bucket) >= settings.activation_rate_limit_attempts:
        attempts[bucket_key] = bucket
        return _error("RATE_LIMITED", "Too many activation attempts.", 429)
    bucket.append(now)
    attempts[bucket_key] = bucket
    return None


class ActivateRequest(BaseModel):
    license_key: str
    device_id: str
    device_name: str | None = None
    os_name: str
    os_version: str
    app_version: str


class ValidateRequest(BaseModel):
    activation_id: str
    device_id: str
    app_version: str


class TrainingSnapshotRequest(BaseModel):
    license_id: str
    device_id: str
    local_report_id: str
    report_fingerprint: str
    export_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrainingSampleRequest(BaseModel):
    local_defect_id: str
    image_sha256: str
    image_filename: str
    labels: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    image_base64: str | None = None


class TrainingConsentRequest(BaseModel):
    license_id: str
    device_id: str
    consent_type: str
    consent_version: str
    accepted: bool
    app_version: str


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    if settings is None:
        settings = ServerSettings()  # pragma: no cover - production entrypoint
    init_db(settings)
    session_factory = create_session_factory(settings)
    signer = EntitlementSigner(settings.signing_private_key, settings.signing_key_id)
    disable_docs = settings.app_env == "production"
    app = FastAPI(
        title="Pipe1 License API",
        version="0.1.0",
        docs_url=None if disable_docs else "/docs",
        redoc_url=None if disable_docs else "/redoc",
        openapi_url=None if disable_docs else "/openapi.json",
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        _set_security_headers(response, production=settings.app_env == "production")
        return response

    app.include_router(create_admin_router(settings))
    activation_attempts: dict[tuple[str, str], list[float]] = {}

    def get_session() -> Session:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @app.exception_handler(ValueError)
    async def _value_error_handler(
        request: Request, exc: ValueError
    ) -> JSONResponse:
        return _error("BAD_REQUEST", str(exc), 400)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    @app.get("/app/releases/latest")
    def latest_app_release(
        platform: str,
        arch: str,
        current_version: str,
        channel: str = "stable",
        session: Session = Depends(get_session),
    ) -> Any:
        try:
            current_version = validate_version(current_version)
        except ValueError as exc:
            return _error("BAD_VERSION", str(exc), 400)
        try:
            platform, arch, channel = normalize_release_target(platform, arch, channel)
        except ValueError as exc:
            return _error("BAD_RELEASE_TARGET", str(exc), 400)
        rows = session.execute(
            select(AppRelease).where(
                AppRelease.platform == platform,
                AppRelease.arch == arch,
                AppRelease.channel == channel,
                AppRelease.status == "published",
            )
        ).scalars()
        release = latest_published_release(rows)
        return _release_update_payload(release, current_version)

    @app.post("/licenses/activate")
    def activate_license(
        body: ActivateRequest,
        request: Request,
        session: Session = Depends(get_session),
    ) -> Any:
        rate_limit_error = _activation_rate_limit_error(
            activation_attempts,
            settings,
            client_host=request.client.host if request.client else "unknown",
            key_hint=body.license_key[:10],
        )
        if rate_limit_error is not None:
            return rate_limit_error
        key = session.execute(
            select(LicenseKey).where(
                LicenseKey.key_hash == hash_license_key(body.license_key)
            )
        ).scalar_one_or_none()
        if key is None:
            return _error("INVALID_LICENSE_KEY", "License key was not found.", 404)
        if key.status == "revoked":
            return _error("REVOKED_LICENSE_KEY", "License key has been revoked.", 403)
        if key.status != "active":
            return _error("INACTIVE_LICENSE_KEY", "License key is inactive.", 403)

        license_row = key.license
        now = _now()
        if license_row.status != "active":
            return _error("INACTIVE_LICENSE", "License is inactive.", 403)
        if license_row.organization.status != "active":
            return _error("SUSPENDED_ORGANIZATION", "Organization is inactive.", 403)
        if license_row.starts_at and now < _aware(license_row.starts_at):
            return _error("LICENSE_NOT_STARTED", "License is not active yet.", 403)
        if license_row.expires_at and now > _aware(license_row.expires_at):
            return _error("EXPIRED_LICENSE", "License has expired.", 403)

        activation = session.execute(
            select(DeviceActivation).where(
                DeviceActivation.license_id == license_row.id,
                DeviceActivation.device_id == body.device_id,
            )
        ).scalar_one_or_none()
        if activation is None:
            active_count = session.scalar(
                select(func.count(DeviceActivation.id)).where(
                    DeviceActivation.license_id == license_row.id,
                    DeviceActivation.status == "active",
                )
            )
            if int(active_count or 0) >= license_row.device_limit:
                return _error(
                    "DEVICE_LIMIT_EXCEEDED",
                    "The license has reached its device limit.",
                    409,
                )
            activation = DeviceActivation(
                id=_id("act"),
                license_id=license_row.id,
                license_key_id=key.id,
                device_id=body.device_id,
                device_name=body.device_name,
                os_name=body.os_name,
                os_version=body.os_version,
                app_version=body.app_version,
                upload_token_hash="",
                status="active",
                activated_at=now,
                last_validated_at=now,
            )
            session.add(activation)
        else:
            activation.license_key_id = key.id
            activation.device_name = body.device_name
            activation.os_name = body.os_name
            activation.os_version = body.os_version
            activation.app_version = body.app_version
            activation.status = "active"
            activation.activated_at = now
            activation.last_validated_at = now
            activation.deactivated_at = None
        key.last_used_at = now
        upload_token = _generate_upload_token()
        activation.upload_token_hash = _hash_upload_token(upload_token)
        entitlement = _make_entitlement(session, signer, key.id, activation, now)
        return {
            "activation_id": activation.id,
            "device_upload_token": upload_token,
            "entitlement": entitlement,
        }

    @app.post("/licenses/validate")
    def validate_license(
        body: ValidateRequest, session: Session = Depends(get_session)
    ) -> Any:
        activation = session.get(DeviceActivation, body.activation_id)
        if activation is None or activation.device_id != body.device_id:
            return _error("INVALID_ACTIVATION", "Activation was not found.", 404)
        if activation.status != "active":
            return _error("INACTIVE_ACTIVATION", "Activation is inactive.", 403)
        license_row = activation.license
        now = _now()
        if license_row.status != "active":
            return _error("INACTIVE_LICENSE", "License is inactive.", 403)
        if license_row.organization.status != "active":
            return _error("SUSPENDED_ORGANIZATION", "Organization is inactive.", 403)
        if license_row.expires_at and now > _aware(license_row.expires_at):
            return _error("EXPIRED_LICENSE", "License has expired.", 403)
        activation.app_version = body.app_version
        activation.last_validated_at = now
        upload_token = _generate_upload_token()
        activation.upload_token_hash = _hash_upload_token(upload_token)
        entitlement = _make_entitlement(
            session, signer, activation.license_key_id, activation, now
        )
        return {
            "status": "valid",
            "device_upload_token": upload_token,
            "entitlement": entitlement,
        }

    @app.post("/training/consents")
    def set_training_consent(
        body: TrainingConsentRequest,
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ) -> Any:
        authorization_error = _training_identity_authorization_error(
            session,
            license_id=body.license_id,
            device_id=body.device_id,
            authorization=authorization,
            require_training_feature=True,
        )
        if authorization_error is not None:
            return authorization_error
        now = _now()
        consent = session.execute(
            select(TrainingConsent).where(
                TrainingConsent.license_id == body.license_id,
                TrainingConsent.device_id == body.device_id,
                TrainingConsent.consent_type == body.consent_type,
            )
        ).scalar_one_or_none()
        if consent is None:
            consent = TrainingConsent(
                id=_id("trc"),
                license_id=body.license_id,
                device_id=body.device_id,
                consent_type=body.consent_type,
                consent_version=body.consent_version,
                accepted=body.accepted,
                app_version=body.app_version,
                accepted_at=now if body.accepted else None,
                revoked_at=None if body.accepted else now,
            )
            session.add(consent)
        else:
            consent.consent_version = body.consent_version
            consent.accepted = body.accepted
            consent.app_version = body.app_version
            if body.accepted:
                consent.accepted_at = consent.accepted_at or now
                consent.revoked_at = None
            else:
                consent.revoked_at = now
        return {
            "status": "accepted" if body.accepted else "revoked",
            "consent_type": body.consent_type,
            "consent_version": body.consent_version,
        }

    @app.post("/training/snapshots")
    def create_training_snapshot(
        body: TrainingSnapshotRequest,
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ) -> Any:
        authorization_error = _training_authorization_error(
            session, body, authorization
        )
        if authorization_error is not None:
            return authorization_error
        snapshot = session.execute(
            select(TrainingSnapshot).where(
                TrainingSnapshot.license_id == body.license_id,
                TrainingSnapshot.device_id == body.device_id,
                TrainingSnapshot.local_report_id == body.local_report_id,
                TrainingSnapshot.report_fingerprint == body.report_fingerprint,
            )
        ).scalar_one_or_none()
        if snapshot is None:
            snapshot = TrainingSnapshot(
                id=_id("trs"),
                license_id=body.license_id,
                device_id=body.device_id,
                local_report_id=body.local_report_id,
                report_fingerprint=body.report_fingerprint,
                export_type=body.export_type,
                metadata_json=body.metadata,
                status="receiving",
            )
            session.add(snapshot)
        return {"snapshot_id": snapshot.id, "status": snapshot.status}

    @app.post("/training/snapshots/{snapshot_id}/samples")
    def create_training_sample(
        snapshot_id: str,
        body: TrainingSampleRequest,
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ) -> Any:
        snapshot = session.get(TrainingSnapshot, snapshot_id)
        if snapshot is None:
            return _error("SNAPSHOT_NOT_FOUND", "Training snapshot was not found.", 404)
        authorization_error = _training_snapshot_authorization_error(
            session, snapshot, authorization
        )
        if authorization_error is not None:
            return authorization_error
        image_metadata = _decode_and_validate_training_image(
            body.image_base64,
            body.image_sha256,
            max_bytes=settings.max_training_image_bytes,
            max_pixels=settings.max_training_image_pixels,
        )
        if isinstance(image_metadata, JSONResponse):
            return image_metadata
        label_error = _validate_training_labels(body.labels)
        if label_error is not None:
            return label_error
        sample = TrainingSample(
            id=_id("sample"),
            snapshot_id=snapshot_id,
            local_defect_id=body.local_defect_id,
            image_sha256=body.image_sha256,
            image_filename=body.image_filename,
            labels_json=body.labels,
            metadata_json={
                **body.metadata,
                **image_metadata,
            },
        )
        session.add(sample)
        return {"sample_id": sample.id}

    @app.post("/training/snapshots/{snapshot_id}/complete")
    def complete_training_snapshot(
        snapshot_id: str,
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ) -> Any:
        snapshot = session.get(TrainingSnapshot, snapshot_id)
        if snapshot is None:
            return _error("SNAPSHOT_NOT_FOUND", "Training snapshot was not found.", 404)
        authorization_error = _training_snapshot_authorization_error(
            session, snapshot, authorization
        )
        if authorization_error is not None:
            return authorization_error
        snapshot.status = "completed"
        snapshot.completed_at = _now()
        return {"snapshot_id": snapshot.id, "status": snapshot.status}

    @app.get("/training/snapshots/{snapshot_id}")
    def get_training_snapshot(
        snapshot_id: str,
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ) -> Any:
        snapshot = session.get(TrainingSnapshot, snapshot_id)
        if snapshot is None:
            return _error("SNAPSHOT_NOT_FOUND", "Training snapshot was not found.", 404)
        authorization_error = _training_snapshot_authorization_error(
            session, snapshot, authorization
        )
        if authorization_error is not None:
            return authorization_error
        samples = session.execute(
            select(TrainingSample).where(TrainingSample.snapshot_id == snapshot_id)
        ).scalars()
        sample_payloads = [
            {
                "sample_id": sample.id,
                "local_defect_id": sample.local_defect_id,
                "image_sha256": sample.image_sha256,
                "image_filename": sample.image_filename,
                "labels": sample.labels_json,
                "metadata": {
                    key: value
                    for key, value in sample.metadata_json.items()
                    if key != "image_base64"
                },
            }
            for sample in samples
        ]
        return {
            "snapshot_id": snapshot.id,
            "status": snapshot.status,
            "sample_count": len(sample_payloads),
            "samples": sample_payloads,
        }

    return app


def _release_update_payload(
    release: AppRelease | None, current_version: str
) -> dict[str, Any]:
    if release is None:
        return {
            "update_available": False,
            "current_version": current_version,
            "latest_version": None,
            "mandatory": False,
            "min_supported_version": None,
            "download_url": None,
            "sha256": None,
            "size_bytes": None,
            "release_notes": None,
            "published_at": None,
        }
    update_available = compare_versions(release.version, current_version) > 0
    mandatory = False
    if release.min_supported_version:
        mandatory = mandatory or compare_versions(
            current_version, release.min_supported_version
        ) < 0
    mandatory = update_available and (mandatory or bool(release.mandatory))
    return {
        "update_available": update_available,
        "current_version": current_version,
        "latest_version": release.version,
        "mandatory": mandatory,
        "min_supported_version": release.min_supported_version,
        "download_url": release.download_url if update_available else None,
        "sha256": release.sha256 if update_available else None,
        "size_bytes": release.size_bytes if update_available else None,
        "release_notes": release.release_notes if update_available else None,
        "published_at": _iso(release.published_at) if update_available else None,
    }


def _training_authorization_error(
    session: Session, body: TrainingSnapshotRequest, authorization: str | None
) -> JSONResponse | None:
    identity_error = _training_identity_authorization_error(
        session,
        license_id=body.license_id,
        device_id=body.device_id,
        authorization=authorization,
        require_training_feature=True,
    )
    if identity_error is not None:
        return identity_error
    consent = body.metadata.get("consent") if isinstance(body.metadata, dict) else None
    if not isinstance(consent, dict) or consent.get("accepted") is not True:
        return _error(
            "TRAINING_CONSENT_REQUIRED",
            "Training upload consent is required.",
            403,
        )
    consent_type = consent.get("type")
    consent_version = consent.get("version")
    if not consent_version or not consent_type:
        return _error(
            "TRAINING_CONSENT_REQUIRED",
            "Training upload consent version and type are required.",
            403,
        )
    server_consent = session.execute(
        select(TrainingConsent).where(
            TrainingConsent.license_id == body.license_id,
            TrainingConsent.device_id == body.device_id,
            TrainingConsent.consent_type == str(consent_type),
            TrainingConsent.accepted.is_(True),
        )
    ).scalar_one_or_none()
    if server_consent is None:
        return _error(
            "TRAINING_CONSENT_REQUIRED",
            "Training upload consent is not recorded on the server.",
            403,
        )
    return None


def _set_security_headers(response: Response, *, production: bool) -> None:
    response.headers.setdefault(
        "Content-Security-Policy",
        (
            "default-src 'none'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'"
        ),
    )
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=()",
    )
    if production:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )


def _training_identity_authorization_error(
    session: Session,
    *,
    license_id: str,
    device_id: str,
    authorization: str | None,
    require_training_feature: bool,
) -> JSONResponse | None:
    bearer_token = _extract_bearer_token(authorization)
    if isinstance(bearer_token, JSONResponse):
        return bearer_token
    license_row = session.get(License, license_id)
    if license_row is None:
        return _error("LICENSE_NOT_FOUND", "License was not found.", 404)
    now = _now()
    if license_row.status != "active":
        return _error("INACTIVE_LICENSE", "License is inactive.", 403)
    if license_row.organization.status != "active":
        return _error("SUSPENDED_ORGANIZATION", "Organization is inactive.", 403)
    if license_row.expires_at and now > _aware(license_row.expires_at):
        return _error("EXPIRED_LICENSE", "License has expired.", 403)

    activation = session.execute(
        select(DeviceActivation).where(
            DeviceActivation.license_id == license_id,
            DeviceActivation.device_id == device_id,
            DeviceActivation.status == "active",
        )
    ).scalar_one_or_none()
    if activation is None:
        return _error("DEVICE_NOT_ACTIVATED", "Device is not activated.", 403)
    token_error = _validate_upload_token(activation, bearer_token)
    if token_error is not None:
        return token_error

    if require_training_feature:
        training_feature = session.execute(
            select(LicenseFeature).where(
                LicenseFeature.license_id == license_id,
                LicenseFeature.feature_key == "training_upload",
                LicenseFeature.enabled.is_(True),
            )
        ).scalar_one_or_none()
        if training_feature is None:
            return _error(
                "TRAINING_UPLOAD_NOT_ENABLED",
                "License does not include training upload.",
                403,
            )
    return None


def _training_snapshot_authorization_error(
    session: Session, snapshot: TrainingSnapshot, authorization: str | None
) -> JSONResponse | None:
    bearer_token = _extract_bearer_token(authorization)
    if isinstance(bearer_token, JSONResponse):
        return bearer_token
    body = TrainingSnapshotRequest(
        license_id=snapshot.license_id,
        device_id=snapshot.device_id,
        local_report_id=snapshot.local_report_id,
        report_fingerprint=snapshot.report_fingerprint,
        export_type=snapshot.export_type,
        metadata=snapshot.metadata_json,
    )
    return _training_authorization_error(session, body, authorization)


def _validate_upload_token(
    activation: DeviceActivation, bearer_token: str
) -> JSONResponse | None:
    expected_hash = activation.upload_token_hash
    if not expected_hash:
        return _error("UPLOAD_TOKEN_NOT_ISSUED", "Upload token was not issued.", 403)
    actual_hash = _hash_upload_token(bearer_token)
    if not secrets.compare_digest(actual_hash, expected_hash):
        return _error("INVALID_UPLOAD_TOKEN", "Upload token is invalid.", 403)
    return None


def _decode_and_validate_training_image(
    image_base64: str | None,
    image_sha256: str,
    *,
    max_bytes: int,
    max_pixels: int,
) -> dict[str, Any] | JSONResponse:
    if not image_base64:
        return _error("MISSING_IMAGE", "Training sample image is required.", 400)
    estimated_size = (len(image_base64) * 3) // 4
    if estimated_size > max_bytes:
        return _error("IMAGE_TOO_LARGE", "Training image is too large.", 413)
    try:
        image_bytes = base64.b64decode(image_base64.encode("ascii"), validate=True)
    except Exception:
        return _error("INVALID_IMAGE_BASE64", "Image payload is malformed.", 400)
    if len(image_bytes) > max_bytes:
        return _error("IMAGE_TOO_LARGE", "Training image is too large.", 413)
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    if image_hash != image_sha256:
        return _error(
            "IMAGE_CHECKSUM_MISMATCH",
            "Image checksum does not match request metadata.",
            400,
        )
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        return _error("INVALID_IMAGE", "Training image is not a supported image.", 400)
    if image_format not in ALLOWED_TRAINING_IMAGE_FORMATS:
        return _error("UNSUPPORTED_IMAGE_TYPE", "Training image type is unsupported.", 400)
    if width <= 0 or height <= 0 or width * height > max_pixels:
        return _error("INVALID_IMAGE_DIMENSIONS", "Training image dimensions are invalid.", 400)
    return {
        "image_size_bytes": len(image_bytes),
        "image_width": width,
        "image_height": height,
        "image_format": image_format.lower(),
    }


def _validate_training_labels(labels: dict[str, Any]) -> JSONResponse | None:
    sample_type = labels.get("sample_type")
    if sample_type not in {"condition", "defect"}:
        return _error("INVALID_LABELS", "Training sample_type is invalid.", 400)
    if not labels.get("item_category"):
        return _error("INVALID_LABELS", "Training item_category is required.", 400)
    if sample_type == "defect" and not labels.get("defect_item"):
        return _error("INVALID_LABELS", "Defect sample requires defect_item.", 400)
    if sample_type == "condition" and not labels.get("condition_item"):
        return _error("INVALID_LABELS", "Condition sample requires condition_item.", 400)
    grade = labels.get("grade")
    if grade not in {None, "", "대", "중", "소"}:
        return _error("INVALID_LABELS", "Training grade is invalid.", 400)
    return None


def _make_entitlement(
    session: Session,
    signer: EntitlementSigner,
    license_key_id: str,
    activation: DeviceActivation,
    issued_at: datetime,
) -> dict[str, Any]:
    session.flush()
    license_row = activation.license or session.get(License, activation.license_id)
    if license_row is None:
        raise ValueError("license not found for activation")
    organization = license_row.organization
    features = {
        "local_report": False,
        "excel_export": False,
        "pdf_export": False,
        "training_upload": False,
        "ai_assist": False,
    }
    features.update(
        {
            row.feature_key: row.enabled
            for row in session.execute(
                select(LicenseFeature).where(LicenseFeature.license_id == license_row.id)
            ).scalars()
        }
    )
    quota = session.execute(
        select(LicenseUsageQuota)
        .where(LicenseUsageQuota.license_id == license_row.id)
        .order_by(LicenseUsageQuota.feature_key.asc())
    ).scalar_one_or_none()
    if quota is None:
        ai_quota = {
            "enabled": False,
            "period": "monthly",
            "limit": 0,
            "used": 0,
            "remaining": 0,
            "reset_at": None,
            "overage_policy": "block",
        }
    else:
        ai_quota = {
            "enabled": True,
            "feature_key": quota.feature_key,
            "period": quota.period,
            "limit": quota.limit,
            "used": quota.used,
            "remaining": max(0, quota.limit - quota.used),
            "reset_at": _iso(quota.reset_at),
            "overage_policy": quota.overage_policy,
            "unit": quota.unit,
        }
    offline_grace_until = issued_at + timedelta(days=license_row.offline_grace_days)
    payload = {
        "license_id": license_row.id,
        "license_key_id": license_key_id,
        "organization_id": organization.id,
        "organization_name": organization.name,
        "license_status": license_row.status,
        "plan": license_row.plan,
        "features": features,
        "ai_quota": ai_quota,
        "device_id": activation.device_id,
        "activation_id": activation.id,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(license_row.expires_at),
        "offline_grace_until": _iso(offline_grace_until),
    }
    envelope = signer.sign(payload)
    payload_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    session.add(
        EntitlementSnapshot(
            id=_id("ent"),
            license_id=license_row.id,
            device_activation_id=activation.id,
            payload_json=payload,
            payload_hash=payload_hash,
            issued_at=issued_at,
            expires_at=_aware(license_row.expires_at),
            offline_grace_until=offline_grace_until,
            signing_key_id=signer.key_id,
        )
    )
    return envelope
