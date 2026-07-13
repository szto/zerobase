from django.conf import settings
from django.db import models

# /w/<slug>/ 와 충돌하거나 혼란을 주는 슬러그 금지
RESERVED_SLUGS = {"studio", "admin", "w", "login", "logout", "signup", "healthz", "static", "ws"}


class World(models.Model):
    """사용자가 소유한 하나의 비즈니스 사이트 (Phase 1: 단일 페이지 + 블록들)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="worlds"
    )
    slug = models.SlugField(unique=True, max_length=40)
    name = models.CharField("사이트 이름", max_length=60)
    tagline = models.CharField("한 줄 소개", max_length=120, blank=True)
    theme_color = models.CharField(max_length=7, default="#2563eb")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name} (/{self.slug})"


class Block(models.Model):
    """월드를 구성하는 블록. type 별 설정은 config(JSON) 에 저장한다.

    스키마/기본값/편집 필드는 app/blocks.py 의 BLOCK_TYPES 레지스트리가 정의.
    """

    class Type(models.TextChoices):
        HERO = "hero", "히어로"
        ABOUT = "about", "소개"
        SERVICES = "services", "서비스/상품"
        CONTACT = "contact", "문의폼"

    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="blocks")
    type = models.CharField(max_length=20, choices=Type.choices)
    position = models.PositiveIntegerField(default=0)
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.world.slug}#{self.position} {self.type}"


class Inquiry(models.Model):
    """문의폼 블록으로 수집된 방문자 문의."""

    world = models.ForeignKey(World, on_delete=models.CASCADE, related_name="inquiries")
    name = models.CharField("이름", max_length=60)
    contact = models.CharField("연락처(이메일/전화)", max_length=120)
    message = models.TextField("문의 내용")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.world.slug}] {self.name}: {self.message[:30]}"


# ─────────────────────────────────────────────────────────────
# 쇼케이스 (/showcase/) — 플랫폼의 가능성을 보여주는 플래그십 데모
# ─────────────────────────────────────────────────────────────


class DemoPollOption(models.Model):
    """쇼케이스 라이브 투표 선택지."""

    name = models.CharField(max_length=60)
    emoji = models.CharField(max_length=8, default="🍞")
    votes = models.PositiveIntegerField(default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]


class DemoGuestNote(models.Model):
    """쇼케이스 라이브 방명록."""

    name = models.CharField(max_length=30)
    message = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class DemoPresence(models.Model):
    """쇼케이스 실시간 관람자 (WS 연결별 heartbeat)."""

    client_key = models.CharField(max_length=40, unique=True)
    last_seen = models.DateTimeField(auto_now=True)


class DemoStock(models.Model):
    """오늘의 빵 남은 수량 — 모든 방문자가 같은 숫자를 실시간으로 본다."""

    remaining = models.PositiveIntegerField(default=24)
