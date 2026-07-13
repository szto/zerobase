"""
?zdebug — DEBUG=False 프로덕션에서도 URL 에 ?zdebug 를 붙이면
djust 디버그 패널(상태/이벤트/패치 히스토리)을 띄운다.

동작 원리: djust 는 DEBUG=True 일 때만 debug-panel.js + client-dev.js 를
주입하고 WebSocket 응답에 _debug 페이로드를 붙인다. 여기서는 같은 에셋
구성을 요청 단위(?zdebug)로 재현한다.

⚠️ 패널은 뷰 내부 상태를 브라우저에 노출한다. 외부 공개 서비스에서는
환경변수 ZDEBUG_ENABLED=false 로 꺼둘 것.

NOTE: ZDebugConsumer 는 djust 1.1.0rc7 의 _attach_debug_payload 에서
DEBUG 게이트만 제거한 복제다 — djust 업그레이드 시 원본과 대조할 것.
"""

import json

from django.conf import settings
from django.templatetags.static import static

from djust.websocket import LiveViewConsumer


def _zdebug_requested(view) -> bool:
    if not getattr(settings, "ZDEBUG_ENABLED", True):
        return False
    request = getattr(view, "request", None)
    return request is not None and "zdebug" in getattr(request, "GET", {})


def _static_url(name: str) -> str:
    try:
        return static(name)
    except (ValueError, AttributeError):
        return f"/static/{name}"


class ZDebugViewMixin:
    """LiveView 앞에 섞는다: class MyView(ZDebugViewMixin, LiveView)."""

    @property
    def _zdebug(self) -> bool:
        return _zdebug_requested(self)

    def _inject_client_script(self, html: str) -> str:
        html = super()._inject_client_script(html)
        # DEBUG 모드는 djust 가 이미 전부 주입한다
        if settings.DEBUG or not self._zdebug:
            return html

        from djust.security import escape_json_for_script

        debug_info = escape_json_for_script(json.dumps(self.get_debug_info()))
        css = (
            f'<link rel="stylesheet" href="{_static_url("djust/debug-panel.css")}">'
        )
        # 실행 순서 보장: 인라인(즉시) → debug-panel.js → client-dev.js (defer 는 DOM 순서 실행)
        scripts = (
            "<script>"
            f"window.DJUST_DEBUG_INFO = {debug_info};"
            "window.djustDebug = true; window.DEBUG_MODE = true;"
            "</script>"
            f'<script src="{_static_url("djust/debug-panel.js")}" defer></script>'
            f'<script src="{_static_url("djust/client-dev.js")}" defer></script>'
        )
        if "</head>" in html:
            html = html.replace("</head>", f"{css}</head>")
        if "</body>" in html:
            html = html.replace("</body>", f"{scripts}</body>")
        else:
            html += scripts
        return html


class ZDebugConsumer(LiveViewConsumer):
    """?zdebug 로 마운트된 뷰에는 DEBUG=False 에서도 _debug 페이로드를 붙인다."""

    def _attach_debug_payload(self, response, event_name=None, performance=None):
        if getattr(settings, "DEBUG", False):
            return super()._attach_debug_payload(response, event_name, performance)

        view = getattr(self, "view_instance", None)
        if view is None or not _zdebug_requested(view):
            return
        if not getattr(self, "_debug_panel_active", True):
            return

        try:
            debug_info = view.get_debug_update()
            if event_name:
                debug_info["_eventName"] = event_name
            if performance:
                debug_info["performance"] = performance
            if "patches" in response:
                debug_info["patches"] = response["patches"]
            response["_debug"] = debug_info
        except Exception:
            pass


def build_header_middleware(get_response):
    """모든 응답에 X-ZB-Build 헤더 — 사용자가 보는 빌드를 즉시 판별."""
    from django.conf import settings as _s

    def middleware(request):
        response = get_response(request)
        response["X-ZB-Build"] = _s.BUILD_SHA
        return response

    return middleware
