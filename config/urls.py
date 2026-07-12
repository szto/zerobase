from django.contrib import admin
from django.http import JsonResponse
from django.urls import path

from app.views import CounterView


def healthz(request):
    """헬스체크 — 프록시/컨테이너 healthcheck 가 사용한다."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("", CounterView.as_view(), name="home"),
]
