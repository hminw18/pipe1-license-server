from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    database_url: str = "sqlite+pysqlite:///./pipe1_license_server.db"
    signing_private_key: str
    signing_key_id: str = "license-signing-key-001"
    app_env: str = "dev"
    default_offline_grace_days: int = 14
    max_training_image_bytes: int = 5 * 1024 * 1024
    max_training_image_pixels: int = 12_000_000
    activation_rate_limit_attempts: int = 30
    activation_rate_limit_window_seconds: int = 60
    admin_username: str | None = None
    admin_password_hash: str | None = None
    admin_password: str | None = None
    admin_totp_secret: str | None = None
    admin_session_secret: str | None = None
    admin_session_ttl_seconds: int = 8 * 60 * 60
    admin_login_rate_limit_attempts: int = 5
    admin_login_rate_limit_window_seconds: int = 5 * 60

    model_config = SettingsConfigDict(
        env_prefix="PIPE1_",
        env_file=".env",
        extra="ignore",
    )
