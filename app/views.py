"""
데모 LiveView — 새 서비스를 만들 때 이 파일을 실제 뷰로 교체한다.
djust LiveView 작성 규칙: mount() 로 초기 상태, @event_handler() 로 이벤트,
템플릿에는 dj-view / dj-root 속성이 반드시 있어야 한다.
"""

from djust import LiveView
from djust.decorators import event_handler


class CounterView(LiveView):
    template_name = "app/counter.html"
    login_required = False  # 의도적 공개 뷰 — 인증 필요 시 True 로

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"count": self.count}

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1

    @event_handler()
    def decrement(self, **kwargs):
        self.count -= 1
