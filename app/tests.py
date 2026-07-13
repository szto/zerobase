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
        html = self.client.get("/showcase/").content.decode()
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
