# Pipe1 License Server MVP Implementation

## 1. Purpose

This document defines the implementable MVP scope for the Pipe1 license server.

The license server MVP exists to support:

- license key issuance
- desktop device activation
- entitlement validation
- offline-capable signed entitlement payloads
- internal manual administration before a web portal exists
- hidden AI quota and feature management fields for future use

The MVP does not include an end-user web portal.

## 2. MVP Product Rule

Pipe1 desktop should feel like a local Windows application.

The license server should be contacted only when needed:

- first license activation
- periodic background validation
- license renewal or revocation check
- server-cost features such as future AI calls
- training-data upload authorization

The desktop app should not send the raw license key on every app launch.

## 3. Recommended Stack

Server runtime:

- Python 3.12+
- FastAPI
- Pydantic settings
- Uvicorn/Gunicorn

Database:

- PostgreSQL
- SQLAlchemy ORM
- Alembic migrations

Security:

- Ed25519-signed entitlement payloads
- private signing key stored only on the server
- public verification key embedded in the desktop app
- HTTPS only

Deployment:

- Docker image
- AWS Lightsail instance for MVP
- Docker Compose on Lightsail
- PostgreSQL on the same instance for pilot, then Lightsail managed database when customer usage grows
- Caddy or Nginx reverse proxy with HTTPS

### 3.1 Deployment Cost Guidance

The license server has low expected traffic in the first release.

Selected MVP hosting:

```text
AWS Lightsail MVP
  - One Linux Lightsail instance
  - Docker Compose
  - FastAPI container
  - PostgreSQL container or host PostgreSQL for first pilot
  - Caddy or Nginx HTTPS reverse proxy
  - Lightsail static IP
  - DNS record for license API domain
  - Lightsail snapshots/backups
```

Recommendation:

- Start with Lightsail to keep monthly cost predictable.
- Keep the application containerized so it can move to ECS/Fargate later without rewriting the server.
- Use same-instance PostgreSQL only for pilot and low-volume first customers.
- Move PostgreSQL to Lightsail managed database before broad customer rollout.
- Move to ECS/Fargate + RDS only when operational scale or uptime requirements justify it.

Cost drivers to watch:

- Lightsail instance monthly bundle.
- Lightsail managed database monthly bundle, when separated.
- Snapshot and backup storage.
- Data transfer for training image upload.
- Object storage, if training images are moved out of the app server.

Lambda note:

- Lambda can be cheaper for very low request volume.
- Lambda still needs a durable database.
- Lambda with RDS often needs RDS Proxy in production, which can erase much of the compute savings.
- Lambda is acceptable for a license-only API but less convenient for Alembic migrations, admin CLI workflows, training upload intake, and future AI usage metering.

### 3.2 Lightsail MVP Deployment Requirements

Required Lightsail resources:

- Linux Lightsail instance.
- Static IP.
- DNS record for API domain.
- HTTPS certificate through reverse proxy automation or external certificate management.
- Automatic instance snapshots.
- Firewall allowing only SSH, HTTP, and HTTPS publicly.

Recommended first instance size:

- 1 GB RAM minimum for internal testing.
- 2 GB RAM recommended if PostgreSQL runs on the same instance.

Recommended service layout:

```text
/opt/pipe1-license-server
  docker-compose.yml
  .env
  data/
    postgres/
  backups/
  logs/
```

Containers:

- `api`: FastAPI application.
- `db`: PostgreSQL, pilot only.
- `proxy`: Caddy or Nginx.

Rules:

- Store signing private key and database password outside git.
- Back up PostgreSQL regularly.
- Test database restore before first paid customer rollout.
- Keep Docker image build reproducible.
- Use log rotation or capped container logs.
- Do not expose PostgreSQL publicly.
- Use SSH key authentication, not password login.

Migration path:

```text
Stage 1
  Lightsail instance
  API + PostgreSQL on same instance

Stage 2
  Lightsail instance
  API on instance
  PostgreSQL in Lightsail managed database

Stage 3
  ECS/Fargate API
  RDS PostgreSQL
  S3/object storage for training images
```

## 4. Server Entities

### 4.1 `organizations`

Represents a customer company or internal account.

Fields:

- `id`
- `name`
- `status`: `active`, `suspended`, `closed`
- `contact_email`, optional
- `memo`, optional internal note
- `created_at`
- `updated_at`

### 4.2 `licenses`

Represents a purchasable or manually issued usage right.

Fields:

- `id`
- `organization_id`
- `plan`: `trial`, `standard`, `pro`, `enterprise`, `internal`
- `status`: `active`, `expired`, `revoked`, `suspended`
- `seat_model`: `device`, `named_user`, `floating`, `enterprise_offline`
- `device_limit`
- `starts_at`
- `expires_at`
- `offline_grace_days`
- `memo`, optional internal note
- `created_at`
- `updated_at`

Rules:

- MVP default `seat_model` is `device`.
- MVP should not require named end-user accounts.
- Revoked or suspended licenses must not create new activations.

### 4.3 `license_keys`

Represents activation keys issued to customers.

Fields:

- `id`
- `license_id`
- `key_prefix`
- `key_hash`
- `status`: `active`, `revoked`, `expired`, `replaced`
- `key_type`: `production`, `trial`, `demo`, `internal`, `support`
- `issued_at`
- `revoked_at`, optional
- `replaced_by_key_id`, optional
- `last_used_at`, optional
- `created_at`
- `updated_at`

Rules:

- Store only a strong hash of the full license key.
- Store a short prefix for support lookup.
- Never store raw full license keys after generation.
- Return the raw full key only once at generation time.
- License keys should be high-entropy random values.
- License key comparison must use constant-time comparison where practical.

Suggested customer-facing format:

```text
PIPE1-XXXX-XXXX-XXXX-XXXX
```

The visible format is only for usability. Security must come from entropy, not from formatting.

### 4.4 `device_activations`

Represents a desktop installation activated by a license.

Fields:

- `id`
- `license_id`
- `license_key_id`
- `device_id`
- `device_name`, optional
- `os_name`
- `os_version`
- `app_version`
- `status`: `active`, `deactivated`, `revoked`
- `activated_at`
- `last_validated_at`
- `deactivated_at`, optional
- `created_at`
- `updated_at`

Rules:

- A license can have at most `device_limit` active devices.
- A deactivated device should free a seat.
- Device id is generated by the desktop app and must be stable for the installation.
- Device id is not a secret.

### 4.5 `license_features`

Represents feature flags attached to a license.

Fields:

- `id`
- `license_id`
- `feature_key`
- `enabled`
- `metadata_json`
- `created_at`
- `updated_at`

MVP feature keys:

- `local_report`
- `pdf_export`
- `excel_export`
- `training_upload`
- `ai_assist`

Rules:

- `ai_assist` may exist as hidden server data before the desktop UI exposes AI.
- Disabled features should be present in entitlement payloads as false or omitted consistently.

### 4.6 `license_usage_quotas`

Represents quota limits for future paid AI or server-cost features.

Fields:

- `id`
- `license_id`
- `feature_key`
- `period`: `monthly`, `annual`, `contract`
- `unit`: `credit`, `image`, `request`, `token`
- `limit`
- `used`
- `reset_at`
- `overage_policy`: `block`, `allow_and_invoice`, `manual_approval`
- `created_at`
- `updated_at`

MVP rule:

- Implement the table and admin commands now.
- Do not expose quota UI to normal desktop users yet.

### 4.7 `license_usage_events`

Represents usage records for future paid server-side operations.

Fields:

- `id`
- `license_id`
- `device_activation_id`, optional
- `feature_key`
- `quantity`
- `unit`
- `request_id`
- `metadata_json`
- `created_at`

Rules:

- Server must decide whether usage is allowed.
- Desktop app must not make billing decisions locally.

### 4.8 `entitlement_snapshots`

Stores issued entitlement payloads for audit and debugging.

Fields:

- `id`
- `license_id`
- `device_activation_id`
- `payload_json`
- `payload_hash`
- `issued_at`
- `expires_at`
- `offline_grace_until`
- `signing_key_id`

Rules:

- Store the exact payload or enough hash data to audit what was issued.
- Do not store private signing keys in this table.

### 4.9 `admin_audit_events`

Records all manual operator actions.

Fields:

- `id`
- `actor`
- `action`
- `target_type`
- `target_id`
- `metadata_json`
- `created_at`

Required audited actions:

- organization created or updated
- license created, extended, suspended, revoked
- license key generated, revoked, rotated, replaced
- device deactivated or revoked
- feature toggled
- AI quota changed

## 5. API Endpoints

### 5.1 `POST /licenses/activate`

Used by the desktop app on first activation or reactivation.

Request:

```json
{
  "license_key": "PIPE1-XXXX-XXXX-XXXX-XXXX",
  "device_id": "generated-device-id",
  "device_name": "optional-machine-name",
  "os_name": "Windows",
  "os_version": "11",
  "app_version": "0.1.0"
}
```

Response success:

```json
{
  "activation_id": "server-activation-id",
  "entitlement": {
    "license_id": "license-id",
    "license_key_id": "license-key-id",
    "organization_id": "organization-id",
    "license_status": "active",
    "plan": "standard",
    "features": {
      "local_report": true,
      "excel_export": true,
      "pdf_export": true,
      "training_upload": false,
      "ai_assist": false
    },
    "seat_model": "device",
    "ai_quota": {
      "enabled": false,
      "period": "monthly",
      "limit": 0,
      "used": 0,
      "remaining": 0,
      "reset_at": null,
      "overage_policy": "block"
    },
    "expires_at": "2027-06-30T23:59:59Z",
    "offline_grace_until": "2026-07-09T00:00:00Z",
    "device_id": "generated-device-id",
    "issued_at": "2026-06-25T00:00:00Z",
    "signature": "base64url-signature"
  }
}
```

Validation rules:

- license key exists and is active
- parent license is active and within validity period
- organization is active
- device limit is not exceeded
- device activation is created or reused idempotently for the same license and device id
- response returns signed entitlement

Failure cases:

- invalid key
- revoked key
- expired license
- suspended organization
- device limit exceeded
- unsupported app version, optional
- rate limited

### 5.2 `POST /licenses/validate`

Used by the desktop app for periodic background validation.

Request:

```json
{
  "activation_id": "server-activation-id",
  "device_id": "generated-device-id",
  "app_version": "0.1.0"
}
```

Response:

```json
{
  "status": "valid",
  "entitlement": {}
}
```

Rules:

- Do not require raw license key.
- Return a fresh signed entitlement when validation succeeds.
- Update `last_validated_at`.
- If revoked, return a clear revoked state.

### 5.3 `GET /health`

Used for operational health checks.

Response:

```json
{
  "status": "ok"
}
```

### 5.4 Internal Admin Web

The MVP includes an internal `/admin` web portal on the FastAPI server. It is disabled until administrator credentials and a session signing secret are configured.

Required operations:

- create organization
- create license
- generate license key
- revoke license key
- rotate or replace license key
- list device activations
- deactivate device
- set feature
- set AI quota
- inspect AI usage events

Required controls:

- Admin login is required for every `/admin` page.
- Session cookies must be signed, HTTP-only, SameSite strict, and secure in production.
- Every mutation form must require a CSRF token.
- TOTP MFA is required for production administrators.
- Raw license keys must be displayed only immediately after issue or rotation.
- Admin pages must not be included in OpenAPI schema output.
- `/admin` must be protected by a reverse-proxy IP allowlist, VPN, or equivalent private access rule.
- Admin login attempts must be rate limited.
- Admin login success, failure, and rate-limit events must be written to the audit log.
- Production must use `PIPE1_ADMIN_PASSWORD_HASH`; plaintext `PIPE1_ADMIN_PASSWORD` is development-only.
- Production responses must include security headers such as CSP, `X-Frame-Options`, `X-Content-Type-Options`, referrer policy, permissions policy, and HSTS.

Required environment:

```bash
PIPE1_ADMIN_USERNAME=admin
PIPE1_ADMIN_PASSWORD_HASH=pbkdf2_sha256:...
PIPE1_ADMIN_SESSION_SECRET=long-random-secret-at-least-32-chars
PIPE1_ADMIN_TOTP_SECRET=required-base32-secret-in-production
PIPE1_ADMIN_ALLOWED_IPS="203.0.113.10/32 10.0.0.0/8"
PIPE1_ADMIN_LOGIN_RATE_LIMIT_ATTEMPTS=5
PIPE1_ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS=300
```

Generate the password hash on the server or a trusted local machine:

```bash
python -c "from pipe1_license_server.admin_auth import hash_admin_password; import getpass; print(hash_admin_password(getpass.getpass()))"
```

## 6. Entitlement Signing

The server must sign entitlement payloads.

Requirements:

- Use Ed25519 or equivalent modern asymmetric signing.
- Server stores private signing key securely.
- Desktop app contains only the public verification key.
- Include `issued_at`, `expires_at`, and `offline_grace_until`.
- Include `device_id` in the signed payload.
- Include a key id field if signing key rotation is planned.
- Desktop app must reject modified or invalidly signed payloads.

Suggested payload envelope:

```json
{
  "payload": {},
  "signature": "base64url-signature",
  "alg": "EdDSA",
  "kid": "license-signing-key-2026-01"
}
```

MVP key rotation:

- Support `kid` in the payload from day one.
- Manual key rotation is acceptable for MVP.
- Keep old public keys available in desktop app only when required by existing active entitlements.

## 7. Internal CLI Requirements

The MVP should provide admin CLI commands before a web portal exists.

Suggested commands:

```bash
pipe1-admin org create --name "ABC Construction" --contact-email ops@example.com
pipe1-admin license create --org ORG_ID --plan standard --device-limit 5 --expires-at 2027-06-30
pipe1-admin key generate --license LICENSE_ID --type production
pipe1-admin key revoke --key-prefix PIPE1-ABCD --reason "lost key"
pipe1-admin key rotate --key-prefix PIPE1-ABCD
pipe1-admin devices list --license LICENSE_ID
pipe1-admin device deactivate --activation ACTIVATION_ID
pipe1-admin feature set --license LICENSE_ID --feature training_upload --enabled true
pipe1-admin quota set --license LICENSE_ID --feature ai_assist --unit credit --limit 10000 --period monthly
pipe1-admin release create --version 1.2.3 --download-url https://license.example.com/downloads/Pipe1-1.2.3-x64.msi --file /srv/pipe1-downloads/Pipe1-1.2.3-x64.msi
pipe1-admin release publish --version 1.2.3
pipe1-admin release disable --version 1.2.3
```

Rules:

- Every CLI mutation must write an `admin_audit_events` row.
- CLI must print generated raw license keys only once.
- CLI output should be copyable for customer support.
- CLI must not print secrets unnecessarily.

Update release rules:

- Installer files are uploaded to the server filesystem out of band, then registered by URL, size, and SHA-256.
- Caddy serves `/downloads/*` from the configured read-only downloads directory.
- Only `published` releases are visible through `GET /app/releases/latest`.
- Release versions must use `MAJOR.MINOR.PATCH`.

## 8. Security Requirements

Required MVP controls:

- HTTPS only in production.
- Strong random license key generation.
- Hash stored license keys.
- Rate-limit activation attempts by IP and key prefix.
- Do not log raw license keys.
- Do not log entitlement private keys.
- Validate request schema strictly.
- Use structured error responses.
- Do not reveal whether a specific customer exists.
- Back up PostgreSQL.

Recommended controls:

- Admin VPN or restricted IP for admin CLI/API access.
- Separate production and staging signing keys.
- Server-side request ids in logs.
- Daily database backup.

## 9. Desktop Integration Contract

The desktop app expects the license server to provide:

- activation endpoint
- validation endpoint
- signed entitlement payload
- stable activation id
- clear error codes

The desktop app will:

- send raw license key only on activation or reactivation
- cache entitlement locally
- verify entitlement signature locally
- allow offline use until `offline_grace_until`
- validate in background when possible
- not make AI billing decisions locally

## 10. Error Codes

Required error codes:

- `INVALID_LICENSE_KEY`
- `REVOKED_LICENSE_KEY`
- `EXPIRED_LICENSE`
- `SUSPENDED_LICENSE`
- `SUSPENDED_ORGANIZATION`
- `DEVICE_LIMIT_EXCEEDED`
- `DEVICE_REVOKED`
- `UNSUPPORTED_APP_VERSION`
- `RATE_LIMITED`
- `SERVER_ERROR`

Errors should include:

- machine-readable `code`
- user-safe Korean message
- support-safe request id

## 11. Testing Requirements

Unit tests:

- license key generation and hashing
- activation validation
- device limit enforcement
- entitlement signing and verification
- revoked/expired/suspended state handling
- AI quota field serialization

Integration tests:

- activate valid license
- activate same device twice idempotently
- reject invalid key
- reject device limit exceeded
- validate active activation
- reject revoked activation
- generate key through CLI
- revoke key through CLI

Security tests:

- raw license key is not stored
- raw license key is not logged
- malformed payload rejected
- rate limit behavior, if available in test harness

## 12. MVP Implementation Phases

### Phase 1 - Server Skeleton

- Create backend package.
- Add FastAPI app.
- Add settings.
- Add PostgreSQL connection.
- Add Alembic migrations.
- Add health endpoint.

### Phase 2 - License Data Model

- Add organization, license, license key, device activation tables.
- Add feature and AI quota tables.
- Add audit event table.
- Add migrations.

### Phase 3 - License Activation

- Add license key generation.
- Add key hashing and lookup.
- Add `POST /licenses/activate`.
- Add device limit enforcement.
- Add entitlement signing.

### Phase 4 - Validation and Admin CLI

- Add `POST /licenses/validate`.
- Add admin CLI commands.
- Add audit logging.
- Add AI quota admin commands.

### Phase 5 - Hardening

- Add rate limiting.
- Add structured error responses.
- Add deployment Dockerfile.
- Add backup and environment setup docs.
- Add production/staging key separation.

## 13. Open Decisions

- Exact license key length and format.
- Whether activation requires device name.
- Whether device id should be generated per install or per workspace.
- Default device limit for first customers.
- Default offline grace period.
- Whether to support enterprise offline license files in the first release.
- Whether admin CLI runs directly against DB or through protected admin APIs.
- Which hosting provider will run the MVP server.
