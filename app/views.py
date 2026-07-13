from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.db import models
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from djust import LiveView
from djust.decorators import event_handler

from config.zdebug import ZDebugViewMixin


class DbRenderMixin:
    """djust 1.1 은 뷰 상태(self.*)가 변해야 재렌더한다 — DB 만 바꾸는
    핸들러는 noop 처리되어 화면이 멈춘다. 핸들러 끝에서 touch() 를 호출해
    리비전을 올리면 재렌더가 강제되고 get_context_data 가 새로 실행된다."""

    def touch(self):
        self.rev = getattr(self, "rev", 0) + 1

    def touch_if_changed(self, fingerprint):
        """폴링 핸들러용: 데이터 핑거프린트가 바뀐 경우에만 재렌더한다.
        평소에는 noop 이 되어 화면이 완전히 조용해진다 (밑줄 속성 = 비직렬화)."""
        if getattr(self, "_last_fp", None) != fingerprint:
            self._last_fp = fingerprint
            self.touch()


def _hhmm(dt):
    """러스트 렌더러는 |date 필터를 지원하지 않는다 — 파이썬에서 포맷."""
    from django.utils import timezone as _tz
    return _tz.localtime(dt).strftime("%H:%M")


def _hms(dt):
    from django.utils import timezone as _tz
    return _tz.localtime(dt).strftime("%H:%M:%S")

from .blocks import BLOCK_TYPES, default_config, render_context
from .models import RESERVED_SLUGS, Block, Inquiry, World


# ─────────────────────────────────────────────────────────────
# 공개 영역 (일반 Django 뷰 — 방문자는 WebSocket 불필요)
# ─────────────────────────────────────────────────────────────


class LandingView(DbRenderMixin, ZDebugViewMixin, LiveView):
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


class StudioView(DbRenderMixin, ZDebugViewMixin, LiveView):
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
        self.touch()
        self.new_name = value[:60]
        self.error = ""

    @event_handler()
    def create_world(self, **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        self.touch()
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


class WorldEditView(DbRenderMixin, ZDebugViewMixin, LiveView):
    """월드 에디터 — 왼쪽 편집 폼, 오른쪽 실시간 미리보기.

    모든 변경은 즉시 DB 에 저장된다 (별도 저장 버튼 없음).
    """

    template_name = "app/world_edit.html"
    login_required = True

    def mount(self, request, world_id=None, **kwargs):
        self.world_id = int(world_id)

    def _world(self):
        return get_object_or_404(World, id=self.world_id, owner=self.request.user)

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
                    "tpl": f"app/blocks/{b.type}.html",
                }
            )
        return {
            "world": world,
            "blocks": blocks,
            "block_types": [{"type": t, "label": s["label"]} for t, s in BLOCK_TYPES.items()],
            "inquiries": [
                {"name": q.name, "contact": q.contact, "message": q.message,
                 "when": _hhmm(q.created_at)}
                for q in world.inquiries.all()[:20]
            ],
            "inquiries_count": world.inquiries.count(),
        }

    @event_handler()
    def update_world(self, value: str = "", field: str = "", **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        self.touch()
        world = self._world()
        if field in ("name", "tagline", "theme_color"):
            setattr(world, field, value[:120])
            world.save(update_fields=[field, "updated_at"])

    @event_handler()
    def update_block(self, value: str = "", block_id: int = 0, field: str = "", **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        self.touch()
        block = get_object_or_404(Block, id=block_id, world__owner=self.request.user)
        allowed = {f["key"] for f in BLOCK_TYPES[block.type]["fields"]}
        if field in allowed:
            block.config = {**(block.config or {}), field: value}
            block.save(update_fields=["config"])

    @event_handler()
    def add_block(self, block_type: str = "", **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        self.touch()
        if block_type not in BLOCK_TYPES:
            return
        world = self._world()
        last = world.blocks.aggregate(m=models.Max("position"))["m"] or 0
        Block.objects.create(
            world=world, type=block_type, position=last + 1, config=default_config(block_type)
        )

    @event_handler()
    def remove_block(self, block_id: int = 0, **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        self.touch()
        Block.objects.filter(id=block_id, world__owner=self.request.user).delete()

    @event_handler()
    def move_block(self, block_id: int = 0, direction: str = "up", **kwargs):  # noqa: S009 — 뷰 레벨 인증 + owner 필터
        self.touch()
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


class ShowcaseView(DbRenderMixin, ZDebugViewMixin, LiveView):
    """아뜰리에 오븐 — 실시간(관람자/재고/투표/방명록) + 3D 히어로 쇼케이스."""

    template_name = "app/showcase.html"
    login_required = False

    def mount(self, request, **kwargs):
        self.client_key = uuid.uuid4().hex
        self.voted = False
        self.reserved = False
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
            "notes": [
                {"name": n.name, "message": n.message, "hhmm": _hhmm(n.created_at)}
                for n in DemoGuestNote.objects.all()[:10]
            ],
            "voted": self.voted,
            "reserved": self.reserved,
            "note_name": self.note_name,
            "note_message": self.note_message,
        }

    @event_handler()
    def heartbeat(self, **kwargs):
        """dj-poll(3초)로 호출 — 내 존재를 알리고, 바뀐 게 있을 때만 재렌더."""
        DemoPresence.objects.update_or_create(client_key=self.client_key)
        DemoPresence.objects.filter(
            last_seen__lt=timezone.now() - timedelta(minutes=2)
        ).delete()
        cutoff = timezone.now() - timedelta(seconds=30)
        self.touch_if_changed((
            DemoPresence.objects.filter(last_seen__gte=cutoff).count(),
            DemoStock.objects.values_list("remaining", flat=True).first(),
            tuple(DemoPollOption.objects.values_list("id", "votes")),
            DemoGuestNote.objects.values_list("id", flat=True).first(),
        ))

    @event_handler()
    def vote(self, option_id: int = 0, **kwargs):
        self.touch()
        if self.voted:
            return
        updated = DemoPollOption.objects.filter(id=option_id).update(votes=F("votes") + 1)
        if updated:
            self.voted = True

    @event_handler()
    def reserve(self, **kwargs):
        self.touch()
        if self.reserved:
            return
        updated = DemoStock.objects.filter(remaining__gt=0).update(remaining=F("remaining") - 1)
        if updated:
            self.reserved = True

    @event_handler()
    def add_note(self, name: str = "", message: str = "", **kwargs):
        self.touch()
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


class ErpShowcaseView(DbRenderMixin, ZDebugViewMixin, LiveView):
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
        # 데모 셀프리셋: 딜이 없거나 전부 계약 완료면 시드로 복원
        if not ErpDeal.objects.filter(stage__lt=3).exists():
            ErpDeal.objects.all().delete()
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
                "index": i, "label": label,
                "deals": [
                    {"id": d.id, "name": d.name, "company": d.company,
                     "amount_fmt": f"{d.amount:,}"}
                    for d in col
                ],
                "total_fmt": f"{sum(d.amount for d in col):,}",
                "is_last": i == len(ErpDeal.STAGES) - 1,
            })
        return {
            "stages": stages,
            "pipeline_total_fmt": f"{sum(d.amount for d in deals if d.stage < 3):,}",
            "won_total_fmt": f"{sum(d.amount for d in deals if d.stage == 3):,}",
            # 오래된 것부터 (Slack 순서). 러스트 미지원 필터(first/date) 대신 프리컴퓨트
            "messages": [
                {"author": m.author, "initial": m.author[:1], "text": m.text,
                 "hhmm": _hhmm(m.created_at)}
                for m in list(ErpMessage.objects.all()[:12])[::-1]
            ],
        }

    @event_handler()
    def refresh(self, **kwargs):
        self.touch_if_changed((
            tuple(ErpDeal.objects.values_list("id", "stage").order_by("id")),
            ErpMessage.objects.values_list("id", flat=True).first(),  # 최신 id
        ))

    @event_handler()
    def advance_deal(self, deal_id: int = 0, **kwargs):
        self.touch()
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
        self.touch()
        author, text = author.strip()[:30] or "익명", text.strip()[:200]
        if text:
            ErpMessage.objects.create(author=author, text=text)
            old = ErpMessage.objects.all()[60:]
            if old:
                ErpMessage.objects.filter(id__in=[m.id for m in old]).delete()


class InventoryShowcaseView(DbRenderMixin, ZDebugViewMixin, LiveView):
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
        # 데모 셀프리셋: 품목이 없거나 재고가 비정상(0 또는 99 초과)이면 시드 복원
        from django.db.models import Q
        if (not InvItem.objects.exists()
                or InvItem.objects.filter(Q(stock=0) | Q(stock__gt=99)).exists()):
            InvItem.objects.all().delete()
            InvEvent.objects.all().delete()
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
            "events": [
                {"item_name": e.item_name, "delta": e.delta, "hms": _hms(e.created_at)}
                for e in InvEvent.objects.all()[:14]
            ],
            "events_count": InvEvent.objects.count(),
        }

    @event_handler()
    def refresh(self, **kwargs):
        self.touch_if_changed((
            tuple(InvItem.objects.values_list("id", "stock").order_by("id")),
            InvEvent.objects.values_list("id", flat=True).first(),
        ))

    @event_handler()
    def receive(self, item_id: int = 0, **kwargs):
        self.touch()
        item = InvItem.objects.filter(id=item_id).first()
        if item:
            item.stock = F("stock") + 10
            item.save(update_fields=["stock"])
            InvEvent.objects.create(item_name=item.name, delta=10)

    @event_handler()
    def ship(self, item_id: int = 0, **kwargs):
        self.touch()
        updated = InvItem.objects.filter(id=item_id, stock__gte=5).update(stock=F("stock") - 5)
        if updated:
            item = InvItem.objects.get(id=item_id)
            InvEvent.objects.create(item_name=item.name, delta=-5)


class UnmannedShowcaseView(DbRenderMixin, ZDebugViewMixin, LiveView):
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
        self.cart = {}  # {product_id: qty} — 연결(고객)별 장바구니
        self.entered = False
        if not UmProduct.objects.exists():
            for i, (n, e, p, s) in enumerate(self.SEED_PRODUCTS):
                UmProduct.objects.create(name=n, emoji=e, price=p, stock=s, order=i)

    def get_context_data(self, **kwargs):
        products = list(UmProduct.objects.all())
        cart_items = []
        total = 0
        for p in products:
            qty = self.cart.get(p.id, 0)
            if qty:
                cart_items.append({"name": p.name, "emoji": p.emoji, "qty": qty,
                                   "sum_fmt": f"{p.price * qty:,}"})
                total += p.price * qty
        return {
            "products": [
                {"id": p.id, "name": p.name, "emoji": p.emoji,
                 "price_fmt": f"{p.price:,}",
                 "stock": p.stock, "in_cart": self.cart.get(p.id, 0)}
                for p in products
            ],
            "cart_items": cart_items,
            "cart_total_fmt": f"{total:,}",
            "entered": self.entered,
            "events": [
                {"kind": e.kind, "text": e.text, "hhmm": _hhmm(e.created_at)}
                for e in UmEvent.objects.all()[:12]
            ],
        }

    @event_handler()
    def refresh(self, **kwargs):
        self.touch_if_changed((
            tuple(UmProduct.objects.values_list("id", "stock").order_by("id")),
            UmEvent.objects.values_list("id", flat=True).first(),
        ))

    @event_handler()
    def enter_store(self, **kwargs):
        self.touch()
        if not self.entered:
            self.entered = True
            UmEvent.objects.create(kind="entry", text="문이 열렸습니다 · 고객 1명 입장 🚪")

    @event_handler()
    def add_to_cart(self, product_id: int = 0, **kwargs):
        self.touch()
        p = UmProduct.objects.filter(id=product_id, stock__gt=0).first()
        if p and self.cart.get(p.id, 0) < p.stock:
            self.cart[p.id] = self.cart.get(p.id, 0) + 1

    @event_handler()
    def checkout(self, **kwargs):
        self.touch()
        if not self.cart:
            return
        total, lines = 0, []
        for pid, qty in list(self.cart.items()):
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
        self.cart = {}
        old = UmEvent.objects.all()[40:]
        if old:
            UmEvent.objects.filter(id__in=[e.id for e in old]).delete()
