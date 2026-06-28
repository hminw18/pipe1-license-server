from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    contact_email: Mapped[str | None] = mapped_column(String(255))
    memo: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    licenses: Mapped[list["License"]] = relationship(back_populates="organization")


class License(Base):
    __tablename__ = "licenses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    plan: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    seat_model: Mapped[str] = mapped_column(String(64), nullable=False, default="device")
    device_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    offline_grace_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    memo: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped[Organization] = relationship(back_populates="licenses")
    keys: Mapped[list["LicenseKey"]] = relationship(back_populates="license")
    activations: Mapped[list["DeviceActivation"]] = relationship(back_populates="license")


class LicenseKey(Base):
    __tablename__ = "license_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id"), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    key_type: Mapped[str] = mapped_column(String(32), nullable=False, default="production")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_key_id: Mapped[str | None] = mapped_column(String(36))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    license: Mapped[License] = relationship(back_populates="keys")


class DeviceActivation(Base):
    __tablename__ = "device_activations"
    __table_args__ = (UniqueConstraint("license_id", "device_id", name="ux_license_device"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id"), nullable=False)
    license_key_id: Mapped[str] = mapped_column(ForeignKey("license_keys.id"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    device_name: Mapped[str | None] = mapped_column(String(255))
    os_name: Mapped[str | None] = mapped_column(String(64))
    os_version: Mapped[str | None] = mapped_column(String(64))
    app_version: Mapped[str | None] = mapped_column(String(64))
    upload_token_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    license: Mapped[License] = relationship(back_populates="activations")


class LicenseFeature(Base):
    __tablename__ = "license_features"
    __table_args__ = (UniqueConstraint("license_id", "feature_key", name="ux_license_feature"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id"), nullable=False)
    feature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LicenseUsageQuota(Base):
    __tablename__ = "license_usage_quotas"
    __table_args__ = (UniqueConstraint("license_id", "feature_key", name="ux_license_quota"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id"), nullable=False)
    feature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(32), nullable=False, default="monthly")
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="credit")
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overage_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="block")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LicenseUsageEvent(Base):
    __tablename__ = "license_usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id"), nullable=False)
    device_activation_id: Mapped[str | None] = mapped_column(ForeignKey("device_activations.id"))
    feature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntitlementSnapshot(Base):
    __tablename__ = "entitlement_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    license_id: Mapped[str] = mapped_column(ForeignKey("licenses.id"), nullable=False)
    device_activation_id: Mapped[str] = mapped_column(ForeignKey("device_activations.id"), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    offline_grace_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(128), nullable=False)


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrainingSnapshot(Base):
    __tablename__ = "training_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "license_id",
            "device_id",
            "local_report_id",
            "report_fingerprint",
            name="ux_training_snapshot_source",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    license_id: Mapped[str] = mapped_column(String(36), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    local_report_id: Mapped[str] = mapped_column(String(64), nullable=False)
    report_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    export_type: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrainingConsent(Base):
    __tablename__ = "training_consents"
    __table_args__ = (
        UniqueConstraint(
            "license_id",
            "device_id",
            "consent_type",
            name="ux_training_consent_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    license_id: Mapped[str] = mapped_column(String(36), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    consent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    app_version: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TrainingSample(Base):
    __tablename__ = "training_samples"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("training_snapshots.id"), nullable=False)
    local_defect_id: Mapped[str] = mapped_column(String(64), nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(128), nullable=False)
    image_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    labels_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
