# Pipe1 라이센스 서버 명령어 가이드

이 문서는 현재 구현된 라이센스 서버와 `deploy/lightsail/pipe1` 운영 래퍼 기준입니다. 운영 서버에서는 `docker compose ...`를 직접 치지 말고 이 래퍼를 사용합니다.

예시는 서버의 프로젝트 루트가 `/opt/pipe1-license-server`라고 가정합니다.

## 1. 기본 위치

```bash
cd /opt/pipe1-license-server/deploy/lightsail
```

이 디렉터리에서 아래 명령을 실행합니다.

```bash
./pipe1 help
```

## 2. 최초 환경 설정

```bash
cp .env.example .env
chmod 600 .env
```

`.env`에서 운영값을 설정합니다.

```bash
PIPE1_LICENSE_DOMAIN=license.example.com
PIPE1_LICENSE_SIGNING_PRIVATE_KEY=
PIPE1_SIGNING_KEY_ID=license-signing-key-001
POSTGRES_DB=pipe1_license
POSTGRES_USER=pipe1
POSTGRES_PASSWORD=change-this-password
DATABASE_URL=postgresql+psycopg://pipe1:change-this-password@db:5432/pipe1_license
```

서명 비밀키 생성:

```bash
./pipe1 private-key
```

출력값을 `.env`의 `PIPE1_LICENSE_SIGNING_PRIVATE_KEY`에 넣습니다.

데스크톱 앱 배포용 공개키 확인:

```bash
./pipe1 public-key
```

공개키는 데스크톱 앱의 `PIPE1_LICENSE_PUBLIC_KEYS`에 `kid`와 함께 넣습니다.

```json
{"license-signing-key-001":"PUBLIC_KEY_HERE"}
```

주의:

- `PIPE1_LICENSE_SIGNING_PRIVATE_KEY`는 서버 전용 비밀키입니다.
- 원본 라이센스 키 전체는 서버 DB에 저장되지 않습니다. 발급 또는 교체 직후 출력에서만 확인할 수 있습니다.

## 3. 서버 운영

시작 또는 갱신 배포:

```bash
./pipe1 start
```

상태 확인:

```bash
./pipe1 status
```

로그 확인:

```bash
./pipe1 logs api
./pipe1 logs proxy
./pipe1 logs db
```

헬스 체크:

```bash
./pipe1 health
```

재시작:

```bash
./pipe1 restart api
./pipe1 restart proxy
```

DB 접속:

```bash
./pipe1 db
```

백업:

```bash
BACKUP_DIR=./backups ./pipe1 backup
```

API 컨테이너 시작 시 DB 테이블은 자동 생성됩니다. 현재 별도 마이그레이션 명령은 없습니다.

## 4. 라이센스 발급 흐름

조직 생성:

```bash
./pipe1 org create --name "ABC Construction" --contact-email ops@example.com
```

라이센스 생성:

```bash
./pipe1 license create --org ORG_ID --plan standard --device-limit 5 --expires-at 2027-06-30T23:59:59Z
```

라이센스 키 발급:

```bash
./pipe1 key generate --license LICENSE_ID --type production
```

기능 켜기:

```bash
./pipe1 feature set --license LICENSE_ID --feature local_report --enabled true
./pipe1 feature set --license LICENSE_ID --feature excel_export --enabled true
./pipe1 feature set --license LICENSE_ID --feature pdf_export --enabled true
./pipe1 feature set --license LICENSE_ID --feature training_upload --enabled true
```

AI 쿼터 설정:

```bash
./pipe1 quota set --license LICENSE_ID --feature ai_assist --unit credit --limit 10000 --period monthly
```

모든 CLI 출력은 JSON 한 줄입니다. 생성, 변경 작업은 `admin_audit_events`에 감사 로그를 남깁니다.

## 5. 현재 정보 조회

조직 목록:

```bash
./pipe1 org list
```

라이센스 목록:

```bash
./pipe1 license list
```

특정 조직의 라이센스:

```bash
./pipe1 license list --org ORG_ID
```

라이센스 키 목록:

```bash
./pipe1 key list
./pipe1 key list --license LICENSE_ID
./pipe1 key list --status active
```

키 조회 출력에는 `key_hash`가 포함되지 않습니다. 전체 원본 키도 다시 볼 수 없습니다.

디바이스 목록:

```bash
./pipe1 devices list
./pipe1 devices list --license LICENSE_ID
./pipe1 devices list --license LICENSE_ID --all
```

기본 조회는 `active` 디바이스만 보여줍니다. `--all`을 붙이면 `deactivated` 상태도 포함합니다.

기능 플래그 조회:

```bash
./pipe1 feature list
./pipe1 feature list --license LICENSE_ID
```

쿼터 조회:

```bash
./pipe1 quota list
./pipe1 quota list --license LICENSE_ID
```

사용량 이벤트 조회:

```bash
./pipe1 usage list --license LICENSE_ID
```

감사 로그 조회:

```bash
./pipe1 audit list
```

## 6. 회수와 교체

라이센스 키 회수:

```bash
./pipe1 key revoke --key-prefix PIPE1-ABCD
```

라이센스 키 교체:

```bash
./pipe1 key rotate --key-prefix PIPE1-ABCD
```

기기 비활성화:

```bash
./pipe1 device deactivate --activation ACTIVATION_ID
```

비활성화된 기기는 라이센스의 기기 사용량에서 빠집니다.

## 7. API 점검

활성화 테스트:

```bash
curl -sS -X POST https://license.example.com/licenses/activate \
  -H "Content-Type: application/json" \
  -d '{
    "license_key": "PIPE1-ABCD-EFGH-IJKL-MNOP",
    "device_id": "pipe1-test-device-001",
    "device_name": "test-pc",
    "os_name": "Windows",
    "os_version": "11",
    "app_version": "0.1.0"
  }'
```

검증 테스트:

```bash
curl -sS -X POST https://license.example.com/licenses/validate \
  -H "Content-Type: application/json" \
  -d '{
    "activation_id": "act_xxx",
    "device_id": "pipe1-test-device-001",
    "app_version": "0.1.0"
  }'
```

대표 에러 코드:

- `INVALID_LICENSE_KEY`: 키가 없음
- `REVOKED_LICENSE_KEY`: 키가 회수됨
- `EXPIRED_LICENSE`: 만료됨
- `DEVICE_LIMIT_EXCEEDED`: 활성 기기 수 초과
- `INVALID_ACTIVATION`: activation id 또는 device id 불일치
- `RATE_LIMITED`: 활성화 시도 제한 초과

## 8. 현재 제한

- 라이센스 정지, 조직 정지 명령은 아직 없습니다.
- 라이센스 생성 시 feature나 quota를 같이 넣는 옵션은 없습니다. 생성 후 `feature set`, `quota set`을 사용합니다.
- 키 회수 명령에는 `--reason` 옵션이 없습니다.
- 복구는 아직 래퍼 명령으로 만들지 않았습니다. 필요 시 백업 SQL을 `./pipe1 db` 또는 `psql`로 직접 복구합니다.
