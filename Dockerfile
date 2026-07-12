# syntax=docker/dockerfile:1

# ── Stage 1: 프론트엔드 에셋 (Node 불필요 — standalone 바이너리) ──
FROM debian:bookworm-slim AS assets
ARG TARGETARCH
ARG TAILWIND_VERSION=4.1.11
ARG ALPINEJS_VERSION=3.14.9

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH=$([ "$TARGETARCH" = "arm64" ] && echo "arm64" || echo "x64") \
    && curl -fsSL -o /usr/local/bin/tailwindcss \
       "https://github.com/tailwindlabs/tailwindcss/releases/download/v${TAILWIND_VERSION}/tailwindcss-linux-${ARCH}" \
    && chmod +x /usr/local/bin/tailwindcss

WORKDIR /build
COPY static/ static/
COPY assets/ assets/
COPY app/templates/ app/templates/

RUN mkdir -p static/css static/js \
    && tailwindcss -i assets/css/input.css -o static/css/output.css --minify \
    && curl -fsSL -o static/js/alpine.min.js \
       "https://cdn.jsdelivr.net/npm/alpinejs@${ALPINEJS_VERSION}/dist/cdn.min.js"

# ── Stage 2: 애플리케이션 ────────────────────────────────────────
FROM python:3.13-slim

# litestream — SQLite 실시간 복제 (entrypoint 에서 restore/replicate)
COPY --from=litestream/litestream:0.3.13 /usr/local/bin/litestream /usr/local/bin/litestream

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=assets /build/static/ static/
RUN cp litestream.yml /etc/litestream.yml && chmod +x entrypoint.sh

# collectstatic 은 빌드 시점에 수행 (WhiteNoise 가 서빙)
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN useradd -m -u 1000 app && mkdir -p /data && chown -R app:app /data /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

ENTRYPOINT ["./entrypoint.sh"]
