from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_lightsail_deployment_assets_exist_and_reference_expected_services() -> None:
    compose = ROOT / "deploy" / "lightsail" / "docker-compose.yml"
    caddyfile = ROOT / "deploy" / "lightsail" / "Caddyfile"
    env_example = ROOT / "deploy" / "lightsail" / ".env.example"
    backup = ROOT / "deploy" / "lightsail" / "backup_postgres.sh"
    wrapper = ROOT / "deploy" / "lightsail" / "pipe1"
    dockerfile = ROOT / "server" / "license_server" / "Dockerfile"

    for path in (compose, caddyfile, env_example, backup, wrapper, dockerfile):
        assert path.exists(), path

    compose_text = compose.read_text(encoding="utf-8")
    assert "api:" in compose_text
    assert "db:" in compose_text
    assert "proxy:" in compose_text
    assert "pipe1-license-api" in compose_text
    assert "read_only: true" in compose_text
    assert "no-new-privileges:true" in compose_text
    assert "cap_drop:" in compose_text

    caddy_text = caddyfile.read_text(encoding="utf-8")
    assert "{$PIPE1_LICENSE_DOMAIN}" in caddy_text
    assert "max_size 8MB" in caddy_text
    assert "path /admin*" in caddy_text
    assert "not remote_ip {$PIPE1_ADMIN_ALLOWED_IPS" in caddy_text
    assert "respond @adminDenied 404" in caddy_text
    assert "reverse_proxy api:8000" in caddy_text

    env_text = env_example.read_text(encoding="utf-8")
    assert "PIPE1_LICENSE_SIGNING_PRIVATE_KEY=" in env_text
    assert "DATABASE_URL=" in env_text
    assert "PIPE1_LICENSE_DOMAIN=" in env_text
    assert "PIPE1_ADMIN_ALLOWED_IPS=" in env_text
    assert "PIPE1_ADMIN_LOGIN_RATE_LIMIT_ATTEMPTS=" in env_text

    wrapper_text = wrapper.read_text(encoding="utf-8")
    assert "docker compose exec -T api pipe1-admin" in wrapper_text
    assert "./pipe1 org list" in wrapper_text
