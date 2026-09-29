from django.urls import path
from .views import index_view, GetCommandAPIView, login_view, logout_view, register_view, admin_create_view

urlpatterns = [
    # Avtorizatsiya va akkaunt boshqaruvi
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('register/', register_view, name='register'),
    path('admin-create/', admin_create_view, name='admin_create'),

    # Bosh sahifa (Veb interfeysi / HTML)
    path('', index_view, name='home'),

    # Kompyuter (Agent) uchun API manzili
    path('api/command/', GetCommandAPIView.as_view(), name='api-command'),
]