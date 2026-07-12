from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from djust import LiveView
from djust.decorators import event_handler

from config.zdebug import ZDebugViewMixin

from .blocks import BLOCK_TYPES, default_config, render_context
from .models import RESERVED_SLUGS, Block, Inquiry, World


# ─────────────────────────────────────────────────────────────
# 공개 영역 (일반 Django 뷰 — 방문자는 WebSocket 불필요)
# ─────────────────────────────────────────────────────────────


class LandingView(ZDebugViewMixin, LiveView):
    """zerobase 홈 — 서비스 소개 + 시작 버튼."""

    template_name = "app/landing.html"
    login_required = False

    def mount(self, request, **kwargs):
        pass  # 상태 없음 — 컨텍스트는 get_context_data 에서 조회

    def get_context_data(self, **kwargs):
        return {"world_count": World.objects.count()}


def world_page(request, slug):
    """방문자에게 보이는 월드 페이지 (서버 렌더, WS 없음)."""
    world = get_object_or_404(World, slug=slug)
    blocks = [{"type": b.type, "cfg": render_context(b)} for b in world.blocks.all()]
    return render(
        request,
        "app/world_page.html",
        {"world": world, "blocks": blocks, "sent": request.GET.get("sent")},
    )


def world_inquiry(request, slug):
    """문의폼 제출 (plain POST — 공개 페이지는 WS 에 의존하지 않는다)."""
    world = get_object_or_404(World, slug=slug)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:60]
        contact = request.POST.get("contact", "").strip()[:120]
        message = request.POST.get("message", "").strip()[:2000]
        if name and message:
            Inquiry.objects.create(world=world, name=name, contact=contact, message=message)
        return redirect(f"/w/{world.slug}/?sent=1#contact")
    return redirect(f"/w/{world.slug}/")


def signup(request):
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        # 인증 백엔드가 복수(axes + ModelBackend)라 명시 필요
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("studio")
    return render(request, "app/signup.html", {"form": form})


# ─────────────────────────────────────────────────────────────
# 스튜디오 (LiveView — 실시간 편집)
# ─────────────────────────────────────────────────────────────


def _unique_slug(name: str) -> str:
    base = slugify(name, allow_unicode=False)[:30] or "world"
    if base in RESERVED_SLUGS:
        base = f"{base}-w"
    slug, n = base, 2
    while World.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


class StudioView(ZDebugViewMixin, LiveView):
    """내 월드 목록 + 새 월드 생성."""

    template_name = "app/studio.html"
    login_required = True

    def mount(self, request, **kwargs):
        self.new_name = ""
        self.error = ""

    def get_context_data(self, **kwargs):
        worlds = World.objects.filter(owner=self.request.user).annotate(
            inquiry_count=models.Count("inquiries")
        )
        return {"worlds": worlds, "new_name": self.new_name, "error": self.error}

    @event_handler()
    def set_name(self, value: str = "", **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        self.new_name = value[:60]
        self.error = ""

    @event_handler()
    def create_world(self, **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        name = self.new_name.strip()
        if len(name) < 2:
            self.error = "이름을 2자 이상 입력해주세요."
            return
        world = World.objects.create(
            owner=self.request.user, name=name, slug=_unique_slug(name)
        )
        # 시작 블록 세트 — 비즈니스 사이트의 최소 구성
        for i, t in enumerate(["hero", "about", "services", "contact"]):
            Block.objects.create(world=world, type=t, position=i, config=default_config(t))
        self.new_name = ""


class WorldEditView(ZDebugViewMixin, LiveView):
    """월드 에디터 — 왼쪽 편집 폼, 오른쪽 실시간 미리보기.

    모든 변경은 즉시 DB 에 저장된다 (별도 저장 버튼 없음).
    """

    template_name = "app/world_edit.html"
    login_required = True

    def mount(self, request, world_id=None, **kwargs):
        self._world_id = int(world_id)

    def _world(self):
        return get_object_or_404(World, id=self._world_id, owner=self.request.user)

    def get_context_data(self, **kwargs):
        world = self._world()
        blocks = []
        for b in world.blocks.all():
            spec = BLOCK_TYPES[b.type]
            cfg = {**default_config(b.type), **(b.config or {})}
            blocks.append(
                {
                    "id": b.id,
                    "type": b.type,
                    "label": spec["label"],
                    "fields": [{**f, "value": cfg.get(f["key"], "")} for f in spec["fields"]],
                    "cfg": render_context(b),
                }
            )
        return {
            "world": world,
            "blocks": blocks,
            "block_types": [{"type": t, "label": s["label"]} for t, s in BLOCK_TYPES.items()],
            "inquiries": world.inquiries.all()[:20],
        }

    @event_handler()
    def update_world(self, value: str = "", field: str = "", **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        world = self._world()
        if field in ("name", "tagline", "theme_color"):
            setattr(world, field, value[:120])
            world.save(update_fields=[field, "updated_at"])

    @event_handler()
    def update_block(self, value: str = "", block_id: int = 0, field: str = "", **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        block = get_object_or_404(Block, id=block_id, world__owner=self.request.user)
        allowed = {f["key"] for f in BLOCK_TYPES[block.type]["fields"]}
        if field in allowed:
            block.config = {**(block.config or {}), field: value}
            block.save(update_fields=["config"])

    @event_handler()
    def add_block(self, block_type: str = "", **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        if block_type not in BLOCK_TYPES:
            return
        world = self._world()
        last = world.blocks.aggregate(m=models.Max("position"))["m"] or 0
        Block.objects.create(
            world=world, type=block_type, position=last + 1, config=default_config(block_type)
        )

    @event_handler()
    def remove_block(self, block_id: int = 0, **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        Block.objects.filter(id=block_id, world__owner=self.request.user).delete()

    @event_handler()
    def move_block(self, block_id: int = 0, direction: str = "up", **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        world = self._world()
        blocks = list(world.blocks.all())
        idx = next((i for i, b in enumerate(blocks) if b.id == block_id), None)
        if idx is None:
            return
        j = idx - 1 if direction == "up" else idx + 1
        if not 0 <= j < len(blocks):
            return
        blocks[idx], blocks[j] = blocks[j], blocks[idx]
        for pos, b in enumerate(blocks):
            if b.position != pos:
                b.position = pos
                b.save(update_fields=["position"])
