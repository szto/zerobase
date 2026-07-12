from django.urls import path

from config.zdebug import ZDebugConsumer

websocket_urlpatterns = [
    # ZDebugConsumer = LiveViewConsumer + ?zdebug 디버그 패널 지원 (config/zdebug.py)
    path("ws/live/", ZDebugConsumer.as_asgi()),
]
