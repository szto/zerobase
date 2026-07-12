# 배포 인프라 셋업 & 서비스 배포 절차

## 전체 아키텍처

```
GitHub (템플릿 리포 → 서비스 리포들)
   │ git push / PR
   ▼
Coolify  ─ LXC on pve2 (Docker)
   ├─ 자동 빌드 → docker-compose 배포
   ├─ Traefik/Caddy 프록시 (도메인별 자동 라우팅, WebSocket 지원)
   ├─ cloudflared (Cloudflare Tunnel, 와일드카드 *.example.com)
   └─ 서비스 스택 × N: [app(djust+uvicorn+litestream) + redis]
                          │
                          └─ SQLite → Litestream → Cloudflare R2 (백업)
```

- 인바운드 포트 개방 없음 — 모든 트래픽은 Cloudflare Tunnel 로만 진입
- TLS 는 Cloudflare 엣지에서 종료, 내부는 HTTP
- 서비스 추가 시 터널/프록시 설정 변경 불필요 (와일드카드 + 자동 라우팅)

---

## 최초 1회: 인프라 셋업

### 1. pve2 에 LXC 생성

권장 스펙: **4 vCPU / RAM 8GB / 디스크 100GB**, Ubuntu 24.04 템플릿.

Docker in LXC 필수 옵션 (`/etc/pve/lxc/<vmid>.conf`):

```
features: nesting=1,keyctl=1
unprivileged: 1
```

주의: PVE 커널 업그레이드 후 Docker 가 깨지는 사례가 간혹 있다.
**배포 환경 변경(업그레이드) 전에 LXC 스냅샷을 습관화**할 것.

### 2. Docker + Coolify 설치 (LXC 내부)

```bash
curl -fsSL https://get.docker.com | sh
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
```

설치 후 `http://<lxc-ip>:8000` 접속 → 관리자 계정 생성.
Coolify 설정에서 프록시를 확인한다 (기본 Traefik — 그대로 사용).

### 3. Cloudflare Tunnel

1. Cloudflare Zero Trust → Networks → Tunnels → **Create tunnel** (cloudflared, Docker 실행 방식 선택)
2. LXC 에서 cloudflared 컨테이너 실행 (대시보드가 주는 명령 사용)
3. Public Hostname 추가:
   - `*.example.com` → `http://<coolify-proxy>:80` (Traefik 컨테이너)
   - Coolify 대시보드용: `coolify.example.com` → `http://localhost:8000`
4. DNS 에 와일드카드 CNAME 이 생성됐는지 확인

> WebSocket 은 Cloudflare Tunnel 과 Traefik 모두 기본 지원 — 별도 설정 불필요.

### 4. GitHub 연동

Coolify → Sources → **+ Add GitHub App** → 조직/계정에 설치.
이후 리포 목록에서 바로 선택해 배포할 수 있고, push 웹훅도 자동 구성된다.

### 5. Cloudflare R2 (백업 버킷)

1. R2 → 버킷 생성 (예: `service-backups` 하나를 전 서비스가 공유, 서비스별 경로 분리)
2. R2 API 토큰 생성 (Object Read & Write) → `LITESTREAM_ACCESS_KEY_ID` / `SECRET_ACCESS_KEY`
3. 엔드포인트: `https://<account-id>.r2.cloudflarestorage.com`

---

## 새 서비스 배포 (매번, ~10분)

1. **리포 생성**: 템플릿 리포에서 *Use this template* → 서비스 이름으로 생성
2. **Coolify 등록**: + New Resource → GitHub App → 리포 선택 → Build Pack: **Docker Compose**
   - **Docker Compose Location 을 `/docker-compose.yml` 로 수정** (기본값이 `.yaml` 이라 못 찾음)
3. **도메인**: 서비스 목록에서 **app 서비스의 Domains** 필드에 입력:
   `http://myservice.example.com:8000`
   - **반드시 `http://`** — Cloudflare Tunnel 이 TLS 를 처리하므로 `https://` 로 넣으면
     Traefik 의 https 리다이렉트와 무한루프가 생긴다
   - `:8000` 은 컨테이너 내부 포트 지정
4. **Advanced 탭 → "Connect To Predefined Network" ON** — 이게 꺼져 있으면
   앱이 Traefik(coolify 네트워크)에 연결되지 않아 404 가 난다
5. **환경변수** (Environment Variables 탭):
   - `SECRET_KEY` 새로 생성 (필수)
   - Litestream 변수들 (`LITESTREAM_BUCKET/PATH/ENDPOINT/키`) — 프로덕션 필수
   - 도메인은 자동: Coolify 가 주입하는 `SERVICE_FQDN_APP` 을 앱이 읽는다
     (`SERVICE_` 접두사는 Coolify 예약이라 직접 정의해도 무시됨)
6. **Deploy** 클릭 → 이후 `git push` 마다 자동 배포 (웹훅 설정 시)

체크: `https://myservice.example.com/healthz` 가 `{"status": "ok"}` 반환하면 완료.

---

## 대안: DIY 모드 (Komodo + Traefik 직접 운용)

Coolify 대신 파이프라인을 직접 소유하고 싶을 때:

1. Traefik 을 `proxy` 라는 외부 Docker 네트워크로 실행
2. `docker-compose.yml` 의 주석 처리된 `labels` / `networks` 블록을 해제
3. Komodo 를 설치하고 리포를 Stack 으로 등록 → 웹훅으로 push 시 자동 `compose up`

이 템플릿은 두 모드 모두에서 수정 없이(주석 해제만으로) 동작하도록 유지한다.
