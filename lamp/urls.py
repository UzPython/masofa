from django.urls import path
from .views import index_view, GetCommandAPIView

urlpatterns = [
    # Bosh sahifa (Veb interfeysi / HTML)
    path('', index_view, name='home'),
    
    # Kompyuter (Agent) uchun API manzili
    path('api/command/', GetCommandAPIView.as_view(), name='api-command'),
]