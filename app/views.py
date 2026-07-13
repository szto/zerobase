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
    if slug == "demo":  # 데모는 플래그십 쇼케이스로
        return redirect("/showcase/")
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


# ─────────────────────────────────────────────────────────────
# 쇼케이스 (/showcase/) — djust 로 만들 수 있는 것의 플래그십 데모
# 가상의 프리미엄 베이커리: 방문자들이 서로를 실시간으로 느끼는 사이트
# ─────────────────────────────────────────────────────────────

import uuid

from django.db.models import F
from django.utils import timezone
from datetime import timedelta

from .models import DemoGuestNote, DemoPollOption, DemoPresence, DemoStock

SHOWCASE_POLL_SEED = [
    ("쑥 크림 도넛", "🍩"),
    ("무화과 캄파뉴", "🥖"),
    ("옥수수 치즈 스콘", "🌽"),
]


class ShowcaseView(ZDebugViewMixin, LiveView):
    """아뜰리에 오븐 — 실시간(관람자/재고/투표/방명록) + 3D 히어로 쇼케이스."""

    template_name = "app/showcase.html"
    login_required = False

    def mount(self, request, **kwargs):
        self._client_key = uuid.uuid4().hex
        self._voted = False
        self._reserved = False
        self.note_name = ""
        self.note_message = ""
        # 시드 (최초 1회)
        if not DemoPollOption.objects.exists():
            for i, (name, emoji) in enumerate(SHOWCASE_POLL_SEED):
                DemoPollOption.objects.create(name=name, emoji=emoji, order=i)
        if not DemoStock.objects.exists():
            DemoStock.objects.create(remaining=24)

    def get_context_data(self, **kwargs):
        cutoff = timezone.now() - timedelta(seconds=30)
        options = list(DemoPollOption.objects.all())
        total = sum(o.votes for o in options) or 1
        return {
            "viewers": DemoPresence.objects.filter(last_seen__gte=cutoff).count(),
            "stock": DemoStock.objects.first(),
            "options": [
                {"id": o.id, "name": o.name, "emoji": o.emoji, "votes": o.votes,
                 "percent": round(o.votes * 100 / total)}
                for o in options
            ],
            "total_votes": sum(o.votes for o in options),
            "notes": DemoGuestNote.objects.all()[:10],
            "voted": self._voted,
            "reserved": self._reserved,
            "note_name": self.note_name,
            "note_message": self.note_message,
        }

    @event_handler()
    def heartbeat(self, **kwargs):
        """dj-poll(3초)로 호출 — 내 존재를 알리고 화면을 최신 상태로."""
        DemoPresence.objects.update_or_create(client_key=self._client_key)
        # 오래된 관람자 정리 (지연 삭제)
        DemoPresence.objects.filter(
            last_seen__lt=timezone.now() - timedelta(minutes=2)
        ).delete()

    @event_handler()
    def vote(self, option_id: int = 0, **kwargs):
        if self._voted:
            return
        updated = DemoPollOption.objects.filter(id=option_id).update(votes=F("votes") + 1)
        if updated:
            self._voted = True

    @event_handler()
    def reserve(self, **kwargs):
        if self._reserved:
            return
        updated = DemoStock.objects.filter(remaining__gt=0).update(remaining=F("remaining") - 1)
        if updated:
            self._reserved = True

    @event_handler()
    def add_note(self, name: str = "", message: str = "", **kwargs):
        name, message = name.strip()[:30], message.strip()[:120]
        if name and message:
            DemoGuestNote.objects.create(name=name, message=message)
            # 최근 60개만 유지
            old = DemoGuestNote.objects.all()[60:]
            if old:
                DemoGuestNote.objects.filter(id__in=[n.id for n in old]).delete()


# ─────────────────────────────────────────────────────────────
# 업종별 쇼케이스: 스타트업 ERP / 도소매 재고 / 무인마켓
# 공통 패턴: dj-poll 하트비트로 전 방문자 화면 동기화
# ─────────────────────────────────────────────────────────────

from .models import ErpDeal, ErpMessage, InvEvent, InvItem, UmEvent, UmProduct


def showcase_hub(request):
    """업종 선택 허브 (정적 렌더)."""
    return render(request, "app/showcase_hub.html")


class ErpShowcaseView(ZDebugViewMixin, LiveView):
    """Slack 스타일 영업/고객/협업 허브 데모."""

    template_name = "app/showcase_erp.html"
    login_required = False

    SEED_DEALS = [
        ("클라우드 도입 문의", "한강물산", 1200, 0),
        ("연간 라이선스 갱신", "누리테크", 3400, 1),
        ("PoC 진행", "빛나래AI", 800, 1),
        ("전사 확대 제안", "그린모빌리티", 5600, 2),
        ("1차 계약 체결", "서울로지스", 2500, 3),
    ]
    SEED_MSGS = [
        ("김세일", "그린모빌리티 제안서 v2 공유했습니다 — 피드백 부탁!"),
        ("박고객", "누리테크 CS 티켓 3건 오늘 모두 해결 ✅"),
        ("이대표", "이번 주 파이프라인 리뷰는 금요일 10시입니다"),
    ]

    def mount(self, request, **kwargs):
        self.msg_author = ""
        self.msg_text = ""
        if not ErpDeal.objects.exists():
            for n, c, a, s in self.SEED_DEALS:
                ErpDeal.objects.create(name=n, company=c, amount=a, stage=s)
        if not ErpMessage.objects.exists():
            for a, t in self.SEED_MSGS:
                ErpMessage.objects.create(author=a, text=t)

    def get_context_data(self, **kwargs):
        deals = list(ErpDeal.objects.all())
        stages = []
        for i, label in enumerate(ErpDeal.STAGES):
            col = [d for d in deals if d.stage == i]
            stages.append({
                "index": i, "label": label, "deals": col,
                "total": sum(d.amount for d in col),
                "is_last": i == len(ErpDeal.STAGES) - 1,
            })
        return {
            "stages": stages,
            "pipeline_total": sum(d.amount for d in deals if d.stage < 3),
            "won_total": sum(d.amount for d in deals if d.stage == 3),
            "messages": list(ErpMessage.objects.all()[:12])[::-1],  # 오래된 것부터 (Slack 순서)
        }

    @event_handler()
    def refresh(self, **kwargs):
        pass  # dj-poll — 재렌더만으로 전 방문자 동기화

    @event_handler()
    def advance_deal(self, deal_id: int = 0, **kwargs):
        deal = ErpDeal.objects.filter(id=deal_id).first()
        if deal and deal.stage < len(ErpDeal.STAGES) - 1:
            deal.stage += 1
            deal.save(update_fields=["stage", "updated_at"])
            if deal.stage == len(ErpDeal.STAGES) - 1:
                ErpMessage.objects.create(
                    author="파이프라인봇",
                    text=f"🎉 {deal.company} · {deal.name} 계약 전환! (+{deal.amount:,}만원)",
                )

    @event_handler()
    def post_message(self, author: str = "", text: str = "", **kwargs):
        author, text = author.strip()[:30] or "익명", text.strip()[:200]
        if text:
            ErpMessage.objects.create(author=author, text=text)
            old = ErpMessage.objects.all()[60:]
            if old:
                ErpMessage.objects.filter(id__in=[m.id for m in old]).delete()


class InventoryShowcaseView(ZDebugViewMixin, LiveView):
    """도소매 실시간 재고 현황 데모."""

    template_name = "app/showcase_inventory.html"
    login_required = False

    SEED_ITEMS = [
        ("제주 감귤 5kg", "🍊", 42, 20),
        ("스테인리스 텀블러", "🥤", 17, 25),
        ("프리미엄 A4 용지", "📄", 88, 30),
        ("무선 이어폰", "🎧", 9, 15),
        ("유기농 원두 1kg", "☕️", 33, 20),
    ]

    def mount(self, request, **kwargs):
        if not InvItem.objects.exists():
            for i, (n, e, s, sf) in enumerate(self.SEED_ITEMS):
                InvItem.objects.create(name=n, emoji=e, stock=s, safety=sf, order=i)

    def get_context_data(self, **kwargs):
        items = list(InvItem.objects.all())
        max_stock = max([i.stock for i in items] + [1])
        return {
            "items": [
                {"id": i.id, "name": i.name, "emoji": i.emoji, "stock": i.stock,
                 "safety": i.safety, "low": i.stock < i.safety,
                 "percent": round(i.stock * 100 / max_stock)}
                for i in items
            ],
            "low_count": sum(1 for i in items if i.stock < i.safety),
            "total_stock": sum(i.stock for i in items),
            "events": InvEvent.objects.all()[:14],
        }

    @event_handler()
    def refresh(self, **kwargs):
        pass

    @event_handler()
    def receive(self, item_id: int = 0, **kwargs):
        item = InvItem.objects.filter(id=item_id).first()
        if item:
            item.stock = F("stock") + 10
            item.save(update_fields=["stock"])
            InvEvent.objects.create(item_name=item.name, delta=10)

    @event_handler()
    def ship(self, item_id: int = 0, **kwargs):
        updated = InvItem.objects.filter(id=item_id, stock__gte=5).update(stock=F("stock") - 5)
        if updated:
            item = InvItem.objects.get(id=item_id)
            InvEvent.objects.create(item_name=item.name, delta=-5)


class UnmannedShowcaseView(ZDebugViewMixin, LiveView):
    """무인마켓 앱 + 사장님 카카오톡 알림 데모."""

    template_name = "app/showcase_unmanned.html"
    login_required = False

    SEED_PRODUCTS = [
        ("바나나우유", "🥛", 1800, 12),
        ("삼각김밥 참치", "🍙", 1500, 8),
        ("아이스 아메리카노", "🧊", 2500, 15),
        ("초코 스낵", "🍫", 1200, 10),
        ("컵라면", "🍜", 1600, 9),
        ("생수 500ml", "💧", 900, 20),
    ]

    def mount(self, request, **kwargs):
        self._cart = {}  # {product_id: qty} — 연결(고객)별 장바구니
        self._entered = False
        if not UmProduct.objects.exists():
            for i, (n, e, p, s) in enumerate(self.SEED_PRODUCTS):
                UmProduct.objects.create(name=n, emoji=e, price=p, stock=s, order=i)

    def get_context_data(self, **kwargs):
        products = list(UmProduct.objects.all())
        cart_items = []
        total = 0
        for p in products:
            qty = self._cart.get(p.id, 0)
            if qty:
                cart_items.append({"name": p.name, "emoji": p.emoji, "qty": qty,
                                   "sum": p.price * qty})
                total += p.price * qty
        return {
            "products": [
                {"id": p.id, "name": p.name, "emoji": p.emoji, "price": p.price,
                 "stock": p.stock, "in_cart": self._cart.get(p.id, 0)}
                for p in products
            ],
            "cart_items": cart_items,
            "cart_total": total,
            "entered": self._entered,
            "events": UmEvent.objects.all()[:12],
        }

    @event_handler()
    def refresh(self, **kwargs):
        pass

    @event_handler()
    def enter_store(self, **kwargs):
        if not self._entered:
            self._entered = True
            UmEvent.objects.create(kind="entry", text="문이 열렸습니다 · 고객 1명 입장 🚪")

    @event_handler()
    def add_to_cart(self, product_id: int = 0, **kwargs):
        p = UmProduct.objects.filter(id=product_id, stock__gt=0).first()
        if p and self._cart.get(p.id, 0) < p.stock:
            self._cart[p.id] = self._cart.get(p.id, 0) + 1

    @event_handler()
    def checkout(self, **kwargs):
        if not self._cart:
            return
        total, lines = 0, []
        for pid, qty in list(self._cart.items()):
            p = UmProduct.objects.filter(id=pid).first()
            if not p:
                continue
            qty = min(qty, p.stock)
            if qty <= 0:
                continue
            p.stock -= qty
            p.save(update_fields=["stock"])
            total += p.price * qty
            lines.append(f"{p.name}×{qty}")
            if p.stock <= 3:
                UmEvent.objects.create(
                    kind="low", text=f"⚠️ {p.name} 재고 {p.stock}개 — 채워주세요!"
                )
        if lines:
            UmEvent.objects.create(
                kind="pay", text=f"💳 결제 완료 {total:,}원 · {', '.join(lines)}"
            )
        self._cart = {}
        old = UmEvent.objects.all()[40:]
        if old:
            UmEvent.objects.filter(id__in=[e.id for e in old]).delete()
