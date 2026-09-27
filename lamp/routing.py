from django.urls import path
from .consumers import ScreenConsumer

websocket_urlpatterns = [
    path('ws/screen/', ScreenConsumer.as_asgi()),
]