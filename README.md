# djust Service Template

새 서비스를 **아이디어에서 배포까지 10분** 안에 올리기 위한 표준 템플릿.
모든 서비스는 이 구조를 그대로 사용한다 — 배포·백업·라우팅·운영 방식이 전부 동일해진다.

## 스택 표준

| 레이어 | 표준 | 비고 |
|---|---|---|
| 웹 프레임워크 | Django 5.1+ + **djust** (LiveView) | 서버 주도 반응형 UI, WebSocket 필수 |
| 클라이언트 | **Alpine.js** (클라이언트 전용 상태) + **Tailwind CSS v4** | 빌드스텝 없음 — standalone 바이너리 |
| 서버 | **uvicorn** `--workers 2 --loop uvloop --ws websockets` | SQLite 특성상 워커 2가 기본값 |
| DB | **SQLite** (WAL + busy_timeout, `/data` 볼륨) | 서비스당 DB 하나, 외부 DB 서버 없음 |
| DB 백업 | **Litestream** → Cloudflare R2 실시간 복제 | 컨테이너 시작 시 자동 복원 |
| 상태/캐시 | **Redis 7** (compose 사이드카, appendonly) | djust 세션 상태 + Channels 레이어 |
| 배포 | **docker-compose** + GitOps (Coolify) | push → 자동 빌드 → 배포 |
| 라우팅 | Cloudflare Tunnel → Traefik/Caddy (라벨 자동 라우팅) | TLS 는 Cloudflare 엣지 종료 |

역할 구분 원칙: **서버 상태는 djust, 클라이언트 전용 UI 상태(토글/드롭다운)는 Alpine.js, 스타일은 Tailwind.**

## 리포 구조

```
├── config/              # Django 프로젝트 (settings/asgi/routing/urls)
├── app/                 # 메인 앱 — 여기에 LiveView 를 작성한다
│   ├── views.py         # 데모 CounterView (교체 대상)
│   └── templates/app/   # base.html 상속 구조
├── static/css/input.css # Tailwind 진입점 (@source 로 템플릿 스캔)
├── Dockerfile           # 멀티스테이지: 에셋 빌드 → 앱 이미지 (litestream 포함)
├── docker-compose.yml   # app + redis 표준 스택
├── entrypoint.sh        # restore → migrate → litestream replicate -exec uvicorn
├── litestream.yml       # R2 복제 설정 (환경변수 주입)
├── .env.example         # 전체 환경변수 목록
└── docs/
    ├── DEPLOYMENT.md    # 인프라 셋업 + 새 서비스 배포 절차
    └── OPERATIONS.md    # 백업/복구, 롤백, 트러블슈팅
```

## 새 서비스 만들기 (표준 절차)

1. GitHub 에서 이 리포의 **Use this template** → 새 리포 생성 (이름 = 서비스 이름)
2. Coolify 에서 **+ New Resource → 해당 리포 선택** (GitHub App 연동 시 목록에 뜸)
3. Coolify UI 에서 도메인 입력 + `.env.example` 의 변수들을 Environment Variables 에 입력
4. **Deploy** — 이후 `git push` 할 때마다 자동 배포

상세 절차와 인프라(LXC/Coolify/Cloudflare Tunnel) 최초 셋업은 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) 참고.

## 로컬 개발

```bash
# 1) 의존성
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) 프론트 에셋 (최초 1회 다운로드)
curl -fsSL -o tailwindcss https://github.com/tailwindlabs/tailwindcss/releases/download/v4.1.11/tailwindcss-linux-x64 && chmod +x tailwindcss
curl -fsSL -o static/js/alpine.min.js https://cdn.jsdelivr.net/npm/alpinejs@3.14.9/dist/cdn.min.js

# 3) 실행 (터미널 2개)
./tailwindcss -i static/css/input.css -o static/css/output.css --watch
python manage.py migrate && uvicorn config.asgi:application --reload
```

Redis 없이도 로컬은 동작한다 (InMemory 채널 레이어, 워커 1개 한정).
컨테이너로 확인하려면: `cp .env.example .env` 후 `docker compose up --build` → http://localhost:8000

## 환경변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `SERVICE_NAME` | ✅ | 서비스 식별자 (리포 이름과 동일하게) |
| `SERVICE_DOMAIN` | ✅ | 서비스 도메인 — ALLOWED_HOSTS/CSRF 에 사용 |
| `SECRET_KEY` | ✅ | `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `LITESTREAM_BUCKET` | 프로덕션 ✅ | R2 버킷 이름. 비우면 복제 없이 기동 |
| `LITESTREAM_PATH` | 프로덕션 ✅ | 버킷 내 경로 = 서비스 이름 |
| `LITESTREAM_ENDPOINT` | 프로덕션 ✅ | `https://<account-id>.r2.cloudflarestorage.com` |
| `LITESTREAM_ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` | 프로덕션 ✅ | R2 API 토큰 |
| `WEB_CONCURRENCY` | | uvicorn 워커 수 (기본 2) |
| `DEBUG` / `LOG_LEVEL` | | 기본 `false` / `INFO` |
| `DJUST_TRUSTED_PROXIES` | | 프록시 IP 대역 (쉼표 구분) |
| `REDIS_URL` | 자동 | compose 가 주입 — 직접 설정하지 않는다 |

## 지켜야 할 표준 (요약)

**인프라:**
- 앱 컨테이너는 **8000 포트**, 헬스체크는 **`GET /healthz`**
- 영속 데이터는 전부 **`/data` 볼륨** (SQLite + 업로드 파일)
- 마이그레이션은 entrypoint 에서 자동 실행 — 별도 배포 스텝 없음
- 프로덕션에서 `LITESTREAM_BUCKET` 미설정 금지 (백업 없는 서비스 금지)

**djust LiveView 작성 규칙:**
- 템플릿의 최상위 콘텐츠 요소에 `dj-view="app.views.MyView"` (dotted path) + `dj-root` 를 함께 붙인다 — base.html 은 순수 레이아웃만
- 새 LiveView 모듈을 만들면 settings 의 `LIVEVIEW_ALLOWED_MODULES` 에 추가 (fail-closed 화이트리스트)
- 공개 뷰는 `login_required = False` 를 명시한다 (안 하면 check 경고)
- Alpine.js 이벤트는 `@click` 이 아닌 **`x-on:click`** 표기 사용 (djust 구문과 충돌 방지)
- 새 LiveView 마다 `LiveViewTestClient` 테스트 최소 1개 (`app/tests.py` 참고)

**품질 게이트:**
- `python manage.py check` 와 `python manage.py test` 가 깨끗해야 push 한다.
  djust 가 보안/구성 검사(CSWSH, 인증 누락, 템플릿 문법)를 check 에서 잡아준다
- 새 Django 앱 추가 시 `static/css/input.css` 에 `@source` 경로 추가
- 알려진 무해 경고: 데모 수준에서 CSS 가 10KB 미만이면 `djust.C011`(output.css stale) 이
  뜬다 — 실제 페이지가 늘어나면 자연히 사라진다
