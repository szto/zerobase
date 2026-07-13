from django.contrib.auth.models import User
from django.test import TestCase

from djust.testing import LiveViewTestClient

from app.blocks import default_config, parse_service_items
from app.models import Block, Inquiry, World
from app.views import StudioView


def make_world(owner, name="테스트 가게", slug="test-shop"):
    world = World.objects.create(owner=owner, name=name, slug=slug)
    for i, t in enumerate(["hero", "about", "services", "contact"]):
        Block.objects.create(world=world, type=t, position=i, config=default_config(t))
    return world


class WorldPublicTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="pw12345!")
        self.world = make_world(self.user)

    def test_world_page_renders_blocks(self):
        html = self.client.get("/w/test-shop/").content.decode()
        self.assertIn("테스트 가게", html)
        self.assertIn("환영합니다", html)  # hero 기본값
        self.assertIn("문의하기", html)  # contact 블록

    def test_inquiry_post_creates_record(self):
        res = self.client.post(
            "/w/test-shop/inquiry/",
            {"name": "홍길동", "contact": "010-0000-0000", "message": "예약 문의합니다"},
        )
        self.assertEqual(res.status_code, 302)
        q = Inquiry.objects.get()
        self.assertEqual(q.world, self.world)
        self.assertEqual(q.name, "홍길동")

    def test_unknown_world_404(self):
        self.assertEqual(self.client.get("/w/nope/").status_code, 404)


class StudioTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="pw12345!")

    def test_studio_requires_login(self):
        res = self.client.get("/studio/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login/", res.url)

    def test_create_world_via_liveview(self):
        client = LiveViewTestClient(StudioView, user=self.user)
        client.mount()
        client.send_event("set_name", value="로이 베이커리")
        client.send_event("create_world")
        world = World.objects.get()
        self.assertEqual(world.owner, self.user)
        self.assertEqual(world.blocks.count(), 4)  # 시작 블록 세트

    def test_signup_flow(self):
        res = self.client.post(
            "/signup/",
            {"username": "newbie", "password1": "s3cure-pass!9", "password2": "s3cure-pass!9"},
        )
        self.assertEqual(res.status_code, 302)
        self.assertTrue(User.objects.filter(username="newbie").exists())


class BlockHelperTests(TestCase):
    def test_parse_service_items(self):
        items = parse_service_items("커트 | 남성 커트 | 15,000원\n펌 |  | 60,000원\n\n이름만")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["name"], "커트")
        self.assertEqual(items[1]["price"], "60,000원")
        self.assertEqual(items[2], {"name": "이름만", "description": "", "price": ""})


class ZDebugTests(TestCase):
    """?zdebug 쿼리로 디버그 패널이 조건부 주입되는지 (DEBUG=False 기준)."""

    def test_panel_injected_with_zdebug(self):
        html = self.client.get("/?zdebug").content.decode()
        self.assertIn("debug-panel.js", html)
        self.assertIn("DJUST_DEBUG_INFO", html)

    def test_panel_absent_without_zdebug(self):
        html = self.client.get("/").content.decode()
        self.assertNotIn("debug-panel.js", html)


class ShowcaseTests(TestCase):
    """쇼케이스 라이브 기능 (투표/재고/방명록)."""

    def test_showcase_page_renders(self):
        html = self.client.get("/showcase/bakery/").content.decode()
        self.assertIn("아뜰리에 오븐", html) if "아뜰리에 오븐" in html else self.assertIn("ATELIER OVEN", html)
        self.assertIn("hero3d", html)

    def test_demo_world_redirects_to_showcase(self):
        res = self.client.get("/w/demo/")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, "/showcase/")

    def test_vote_once_per_connection(self):
        from app.models import DemoPollOption
        from app.views import ShowcaseView

        client = LiveViewTestClient(ShowcaseView)
        client.mount()
        opt = DemoPollOption.objects.first()
        client.send_event("vote", option_id=opt.id)
        client.send_event("vote", option_id=opt.id)  # 두 번째는 무시
        opt.refresh_from_db()
        self.assertEqual(opt.votes, 1)

    def test_reserve_decrements_shared_stock(self):
        from app.models import DemoStock
        from app.views import ShowcaseView

        client = LiveViewTestClient(ShowcaseView)
        client.mount()
        before = DemoStock.objects.first().remaining
        client.send_event("reserve")
        client.send_event("reserve")  # 연결당 1회
        self.assertEqual(DemoStock.objects.first().remaining, before - 1)

    def test_guest_note(self):
        from app.models import DemoGuestNote
        from app.views import ShowcaseView

        client = LiveViewTestClient(ShowcaseView)
        client.mount()
        client.send_event("add_note", name="지나가던 빵순이", message="깜빠뉴 최고!")
        self.assertEqual(DemoGuestNote.objects.count(), 1)


class VerticalShowcaseTests(TestCase):
    """업종별 쇼케이스 (허브/ERP/재고/무인)."""

    def test_hub_lists_verticals(self):
        html = self.client.get("/showcase/").content.decode()
        for path in ["/showcase/bakery/", "/showcase/erp/", "/showcase/inventory/", "/showcase/unmanned/"]:
            self.assertIn(path, html)

    def test_erp_advance_deal_and_bot_message(self):
        from app.models import ErpDeal, ErpMessage
        from app.views import ErpShowcaseView

        client = LiveViewTestClient(ErpShowcaseView)
        client.mount()
        deal = ErpDeal.objects.filter(stage=2).first()  # 제안 단계
        client.send_event("advance_deal", deal_id=deal.id)
        deal.refresh_from_db()
        self.assertEqual(deal.stage, 3)
        self.assertTrue(ErpMessage.objects.filter(author="파이프라인봇").exists())

    def test_inventory_receive_and_ship(self):
        from app.models import InvEvent, InvItem
        from app.views import InventoryShowcaseView

        client = LiveViewTestClient(InventoryShowcaseView)
        client.mount()
        item = InvItem.objects.first()
        before = item.stock
        client.send_event("receive", item_id=item.id)
        client.send_event("ship", item_id=item.id)
        item.refresh_from_db()
        self.assertEqual(item.stock, before + 10 - 5)
        self.assertEqual(InvEvent.objects.count(), 2)

    def test_unmanned_checkout_flow(self):
        from app.models import UmEvent, UmProduct
        from app.views import UnmannedShowcaseView

        client = LiveViewTestClient(UnmannedShowcaseView)
        client.mount()
        client.send_event("enter_store")
        p = UmProduct.objects.first()
        before = p.stock
        client.send_event("add_to_cart", product_id=p.id)
        client.send_event("add_to_cart", product_id=p.id)
        client.send_event("checkout")
        p.refresh_from_db()
        self.assertEqual(p.stock, before - 2)
        kinds = set(UmEvent.objects.values_list("kind", flat=True))
        self.assertIn("entry", kinds)
        self.assertIn("pay", kinds)


class DjRootTemplateContractTests(TestCase):
    """djust 1.0.8 계약: dj-root 는 반드시 <div> 태그.

    djust 의 루트 추출 정규식(_extract_liveview_content 등)은 div 만 인식한다.
    다른 태그(main 등)를 쓰면 추출이 실패해 mount 시 문서 전체가 클라이언트로
    전송되고, morph 가 루트를 자기 자신 안에 중첩시켜 레이아웃이 무너진다
    (ERP 보드 컬럼이 데스크톱에서 절반 폭으로 쪼그라들던 버그의 근본 원인).
    주석 속 태그 리터럴("<div dj-root>")도 원본 소스 스캔에 오매칭하므로 금지.
    """

    def _template_sources(self):
        import pathlib

        root = pathlib.Path(__file__).parent / "templates"
        for path in sorted(root.rglob("*.html")):
            source = path.read_text()
            if "dj-root" in source:
                yield path.name, source

    def test_dj_root_element_is_div(self):
        import re

        found = 0
        for name, source in self._template_sources():
            for m in re.finditer(r"<([a-zA-Z0-9-]+)\b[^>]*\bdj-root(?=[\s=>/])", source):
                found += 1
                self.assertEqual(
                    m.group(1), "div",
                    f"{name}: dj-root 는 <div> 여야 한다 (djust 추출 정규식이 div 만 인식) — <{m.group(1)}> 발견",
                )
        self.assertGreater(found, 0, "dj-root 템플릿을 하나도 찾지 못했다 — 테스트 경로 확인")

    def test_djust_can_extract_root_from_every_template(self):
        """주석 리터럴 오매칭 등으로 추출이 통째로 실패하는 회귀를 잡는다."""
        from djust.mixins.template import TemplateMixin

        class _T(TemplateMixin):
            template = None
            template_name = None

        t = _T()
        for name, source in self._template_sources():
            extracted = t._extract_liveview_root_with_wrapper(source)
            self.assertNotEqual(
                extracted, source,
                f"{name}: djust 가 dj-root 를 추출하지 못했다 — 태그/주석 리터럴 확인",
            )
            self.assertTrue(
                extracted.lstrip().startswith("<div"),
                f"{name}: 추출 결과가 div 로 시작하지 않는다: {extracted[:80]!r}",
            )
