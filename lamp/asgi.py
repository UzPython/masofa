# my_app/asgi.py (my_app o'rniga o'z loyihangiz nomini yozasiz)
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from routing import websocket_urlpatterns  # routing faylimiz yo'li

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'my_app.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": URLRouter(
        websocket_urlpatterns
    ),
})