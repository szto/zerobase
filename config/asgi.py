import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.asgi import get_asgi_application

django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

import config.routing

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # AllowedHostsOriginValidator: CSWSH(교차 사이트 WebSocket 하이재킹) 방지
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(
                URLRouter(config.routing.websocket_urlpatterns)
            )
        ),
    }
)
