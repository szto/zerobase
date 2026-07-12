# 운영 표준: 백업/복구, 롤백, 트러블슈팅

## 백업 체계 (3중)

| 계층 | 도구 | 주기 | 복구 시점 |
|---|---|---|---|
| DB | Litestream → R2 | 실시간 (초 단위) | 장애 직전 |
| 컨테이너/볼륨 | PVE LXC 스냅샷 | 변경 작업 전 수동 + 주기적 | 스냅샷 시점 |
| 전체 | PVE vzdump 백업 | 주 1회 권장 | 백업 시점 |

Redis 는 djust 세션 상태(휘발성에 가까움)만 담으므로 R2 백업 대상에서 제외한다.
appendonly 로 재시작 유실만 방지하면 충분하다.

## DB 복구 절차

**시나리오 A — 볼륨이 날아감 (LXC 재구축 포함):**
아무것도 안 해도 된다. entrypoint 가 시작 시 `litestream restore -if-db-not-exists` 를
실행하므로, 빈 볼륨으로 컨테이너를 올리면 R2 에서 자동 복원된다.

**시나리오 B — 특정 시점으로 되돌리기 (잘못된 마이그레이션/데이터 사고):**

```bash
# 앱 컨테이너 안에서 (또는 litestream 이 설치된 곳에서)
litestream restore -timestamp 2026-07-11T09:00:00Z -o /data/restored.sqlite3 /data/db.sqlite3
# 확인 후 교체
docker compose stop app
mv /data/restored.sqlite3 /data/db.sqlite3   # 볼륨 마운트 경로에서
docker compose start app
```

**복구 훈련**: 새 서비스를 처음 배포하면 반드시 한 번은
빈 볼륨에서 자동 복원이 되는지 확인한다 (`docker compose down -v && up`).

## 배포 롤백

- Coolify: 서비스 → Deployments → 이전 배포 선택 → **Redeploy**
- DB 마이그레이션이 포함된 롤백은 코드 롤백만으로 안 될 수 있다 →
  마이그레이션은 가능하면 **후방 호환**(컬럼 추가는 nullable, 삭제는 2단계)으로 작성

## 로그 / 모니터링

- 로그: Coolify UI 의 서비스 로그 (또는 `docker compose logs -f app`)
- 헬스체크: 모든 서비스 `GET /healthz` — 외부 모니터링(Uptime Kuma 권장) 에 등록
- 리소스: 서비스당 예상 사용량 app 200~400MB + redis ~30MB.
  pve2 RAM 여유를 보고 서비스 증설 판단 (현재 16GB 노드 기준 10개 내외가 한계)

## SQLite 운영 한계와 이전 기준

SQLite 는 쓰기가 단일 스레드다. 아래 신호가 보이면 해당 서비스만 PostgreSQL 로 이전:

- 로그에 `database is locked` 가 busy_timeout(5s) 이후에도 반복
- 쓰기 위주 트래픽이 지속적으로 초당 수십 건 이상

이전 시에도 나머지 구조(compose, 프록시, 배포 플로우)는 그대로 유지된다.

## 트러블슈팅 체크리스트

**WebSocket 이 안 붙을 때 (djust 화면이 반응 없음):**
1. 브라우저 콘솔에서 `wss://.../ws/live/` 연결 상태 확인
2. Cloudflare 대시보드에서 WebSocket 활성화 확인 (Network 탭 — 기본 on)
3. `CSRF_TRUSTED_ORIGINS` / `SERVICE_DOMAIN` 이 실제 도메인과 일치하는지
4. 워커 2개 이상인데 `REDIS_URL` 이 비어 있지 않은지 (재연결이 다른 워커에 떨어지면 상태 유실)

**배포 후 500 에러:**
1. `docker compose logs app` 에서 migrate 실패 여부
2. `SECRET_KEY` 등 필수 환경변수 누락 여부
3. `/healthz` 는 되는데 페이지만 500 이면 템플릿/정적 파일 문제 → collectstatic 로그 확인

**Docker 가 LXC 에서 안 뜰 때 (PVE 업그레이드 후):**
1. `features: nesting=1,keyctl=1` 유지 확인
2. `dmesg | grep -i apparmor` 로 차단 로그 확인
3. 최후: 스냅샷 롤백 후 PVE 포럼에서 해당 커널 이슈 확인

**Litestream 이 복제를 안 할 때:**
1. `docker compose logs app | grep litestream` — 인증/엔드포인트 오류 확인
2. R2 토큰 권한 (Object Read & Write), 엔드포인트에 account-id 포함 여부
