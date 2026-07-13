from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.urls import path

from app.views import (
    ErpShowcaseView,
    InventoryShowcaseView,
    LandingView,
    ShowcaseView,
    UnmannedShowcaseView,
    showcase_hub,
    StudioView,
    WorldEditView,
    signup,
    world_inquiry,
    world_page,
)


def healthz(request):
    """헬스체크 — 프록시/컨테이너 healthcheck 가 사용한다."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("", LandingView.as_view(), name="home"),
    path("showcase/", showcase_hub, name="showcase"),
    path("showcase/bakery/", ShowcaseView.as_view(), name="showcase_bakery"),
    path("showcase/erp/", ErpShowcaseView.as_view(), name="showcase_erp"),
    path("showcase/inventory/", InventoryShowcaseView.as_view(), name="showcase_inventory"),
    path("showcase/unmanned/", UnmannedShowcaseView.as_view(), name="showcase_unmanned"),
    # 인증
    path("signup/", signup, name="signup"),
    path("login/", auth_views.LoginView.as_view(template_name="app/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # 스튜디오 (소유자 편집 영역)
    path("studio/", StudioView.as_view(), name="studio"),
    path("studio/<int:world_id>/", WorldEditView.as_view(), name="world_edit"),
    # 공개 월드
    path("w/<slug:slug>/", world_page, name="world_page"),
    path("w/<slug:slug>/inquiry/", world_inquiry, name="world_inquiry"),
]
