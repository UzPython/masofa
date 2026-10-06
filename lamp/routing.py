# control_app/routing.py
from django.urls import re_path
from .consumers import ScreenConsumer

websocket_urlpatterns = [
    re_path(r'^ws/screen/(?P<agent_id>[^/]+)/?$', ScreenConsumer.as_asgi()),
]