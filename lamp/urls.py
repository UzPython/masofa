from django.urls import path
from .views import (
    index_view,
    login_view,
    logout_view,
    register_view,
    admin_create_view,
    delete_allowed_site,
    delete_blocked_site,
    delete_shared_account,
    delete_computer,
    clear_commands,
    clear_warnings,
    download_agent_bat,
    download_agent_script,
    export_warnings_csv,
    export_commands_csv,
    ajax_send_command,
    ajax_command_status,
    upload_command_transfer,
    download_command_transfer,
    GetCommandAPIView,
    CheckSiteAPIView,
    WhitelistAPIView,
)

urlpatterns = [
    # Avtorizatsiya va akkaunt boshqaruvi
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('register/', register_view, name='register'),
    path('admin-create/', admin_create_view, name='admin_create'),

    # Bosh sahifa (Veb interfeysi / HTML)
    path('', index_view, name='home'),

    # Boshqaruv va o'chirish marshrutlari
    path('share/delete/<int:share_id>/', delete_shared_account, name='delete_share'),
    path('computer/delete/<int:computer_id>/', delete_computer, name='delete_computer'),
    path('allowed-site/delete/<int:site_id>/', delete_allowed_site, name='delete_allowed_site'),
    path('blocked-site/delete/<int:site_id>/', delete_blocked_site, name='delete_blocked_site'),
    path('commands/clear/', clear_commands, name='clear_commands'),
    path('warnings/clear/', clear_warnings, name='clear_warnings'),
    path('warnings/export-csv/', export_warnings_csv, name='export_warnings_csv'),
    path('commands/export-csv/', export_commands_csv, name='export_commands_csv'),
    path('agent/download-bat/', download_agent_bat, name='download_agent_bat'),
    path('agent/script/', download_agent_script, name='download_agent_script'),

    # AJAX orqali tezkor terminal buyruqlari
    path('api/ajax-send-command/', ajax_send_command, name='ajax_send_command'),
    path('api/ajax-command-status/<int:command_id>/', ajax_command_status, name='ajax_command_status'),
    path('api/command/<int:command_id>/transfer/', upload_command_transfer, name='upload_command_transfer'),
    path('api/command/<int:command_id>/transfer/download/', download_command_transfer, name='download_command_transfer'),

    # Kompyuter (Agent) uchun API manzillari
    path('api/command/', GetCommandAPIView.as_view(), name='api-command'),
    path('api/check-site/', CheckSiteAPIView.as_view(), name='api-check-site'),
    path('api/whitelist/', WhitelistAPIView.as_view(), name='api-whitelist'),
]