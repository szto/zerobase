"""
표준 테스트 예시 — LiveViewTestClient 로 브라우저 없이 이벤트를 검증한다.
새 LiveView 를 만들면 최소한 mount + 핵심 이벤트 하나는 테스트를 남긴다.
"""

from django.test import TestCase
from djust.testing import LiveViewTestClient

from app.views import CounterView


class CounterViewTests(TestCase):
    def test_mount_initial_state(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        client.assert_state(count=0)

    def test_increment_and_decrement(self):
        client = LiveViewTestClient(CounterView)
        client.mount()
        client.send_event("increment")
        client.send_event("increment")
        client.assert_state(count=2)
        client.send_event("decrement")
        client.assert_state(count=1)
