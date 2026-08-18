"""ASGI entrypoint.

Channels is wired up and the Redis channel layer is configured, but Phase 1
ships no consumers — WebSocket channels are Phase 11 work (SRS §9.5).
One ASGI process serves HTTP; a separate WebSocket deployment comes later
(SRS §6.6).
"""

import os

from channels.routing import ProtocolTypeRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
    }
)
