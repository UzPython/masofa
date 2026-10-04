import asyncio
import base64
import json
import os
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from agent import apply_web_filter, blocked_url_patterns, build_ws_url, execute_system_command, poll_whitelist, send_screen
from my_app.consumers import normalize_agent_id
from my_app.routing import websocket_urlpatterns
from .models import BlockedApp, BlockedSite, Command, Computer, SharedAccount, UserProfile
from .views import execute_system_command_local
from site_rules.models import AgentCommandRoute, SiteRule


class AgentIdValidationTests(TestCase):
    def test_normalize_agent_id_replaces_invalid_path_chars(self):
        self.assertEqual(normalize_agent_id(r'.\agent_stream.py'), 'agent_stream.py')
        self.assertEqual(normalize_agent_id('PC-05'), 'PC-05')
        self.assertEqual(normalize_agent_id('  '), 'default')


class AgentStreamConfigTests(TestCase):
    def test_build_ws_url_uses_local_default_server(self):
        self.assertEqual(
            build_ws_url('PC-05'),
            'ws://127.0.0.1:8000/ws/screen/PC-05/?role=agent',
        )

    def test_build_ws_url_uses_explicit_server_url(self):
        self.assertEqual(
            build_ws_url('PC-05', 'https://panel.example.com'),
            'wss://panel.example.com/ws/screen/PC-05/?role=agent',
        )

    def test_build_ws_url_uses_environment_server_url(self):
        previous = os.environ.get('SCREEN_SERVER_URL')
        os.environ['SCREEN_SERVER_URL'] = 'https://remote.example.com/'
        try:
            self.assertEqual(
                build_ws_url('PC-05'),
                'wss://remote.example.com/ws/screen/PC-05/?role=agent',
            )
        finally:
            if previous is None:
                os.environ.pop('SCREEN_SERVER_URL', None)
            else:
                os.environ['SCREEN_SERVER_URL'] = previous

    def test_build_ws_url_includes_credentials_for_remote_agent(self):
        self.assertEqual(
            build_ws_url('PC-05', username='agentuser', password='secret123'),
            'ws://127.0.0.1:8000/ws/screen/PC-05/?role=agent&username=agentuser&password=secret123',
        )

    def test_build_ws_url_uses_account_id_for_shared_computer(self):
        self.assertEqual(
            build_ws_url('PC-07', owner_id=19863799005458003971),
            'ws://127.0.0.1:8000/ws/screen/19863799005458003971__PC-07/?role=agent',
        )


class AgentScreenCaptureTests(TestCase):
    def test_screen_capture_does_not_block_other_agent_tasks(self):
        heartbeat_count = [0]
        heartbeats_during_capture = []

        class FakeScreenshot:
            def save(self, output, format, quality):
                output.write(b'jpeg-frame')

        def slow_screenshot():
            start_count = heartbeat_count[0]
            time.sleep(0.05)
            heartbeats_during_capture.append(heartbeat_count[0] - start_count)
            return FakeScreenshot()

        class FakeWebSocket:
            def __init__(self):
                self.frames = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_value, traceback):
                return False

            async def send(self, frame):
                self.frames.append(frame)

        async def run_agent_tasks():
            async def heartbeat():
                while True:
                    heartbeat_count[0] += 1
                    await asyncio.sleep(0.001)

            heartbeat_task = asyncio.create_task(heartbeat())
            screen_task = asyncio.create_task(send_screen('PC-05'))
            await asyncio.sleep(0.12)
            screen_task.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(screen_task, heartbeat_task, return_exceptions=True)

        with (
            patch('agent.pyautogui.screenshot', side_effect=slow_screenshot),
            patch('agent.websockets.connect', return_value=FakeWebSocket()),
            patch('agent.build_ws_url', return_value='ws://127.0.0.1/ws/screen/PC-05/'),
        ):
            asyncio.run(run_agent_tasks())

        self.assertTrue(heartbeats_during_capture)
        self.assertTrue(all(count > 0 for count in heartbeats_during_capture))


class AgentSetupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='agent-setup', password='StrongPass123')
        self.client.force_login(self.user)

    def test_setup_installer_registers_hidden_agent_for_windows_startup(self):
        response = self.client.get(
            reverse('download_agent_bat'),
            HTTP_X_FORWARDED_PROTO='https',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('masofa_212.bat', response['Content-Disposition'])
        bat_content = response.content.decode()
        self.assertIn('set "PS_ENCODED_FILE=%TEMP%', bat_content)
        self.assertIn('[scriptblock]::Create($script)', bat_content)
        self.assertIn('if not "%INSTALL_ERROR%"=="0" (', bat_content)
        setup_lines = [
            line for line in bat_content.splitlines()
            if line.startswith('> "%PS_ENCODED_FILE%" echo ')
            or line.startswith('>> "%PS_ENCODED_FILE%" echo ')
        ]
        encoded_script = ''.join(line.split(' echo ', 1)[1] for line in setup_lines)
        setup_script = base64.b64decode(encoded_script).decode('utf-16le')
        powershell_line = next(line for line in bat_content.splitlines() if 'powershell.exe ' in line)
        self.assertLess(len(powershell_line), 8191)
        self.assertGreater(len(setup_lines), 1)
        self.assertIn("Invoke-WebRequest -UseBasicParsing -Uri ($serverUrl + '/agent/script/')", setup_script)
        self.assertIn('\n        $installDir =', setup_script)
        self.assertIn('Start-Process -FilePath', setup_script)
        self.assertIn("-Verb RunAs -Wait -PassThru", setup_script)
        self.assertIn("New-ScheduledTaskPrincipal -UserId $userIdentity -LogonType Interactive -RunLevel Highest", setup_script)
        self.assertIn("Register-ScheduledTask -TaskName 'Masofa Agent'", setup_script)
        self.assertIn("GetFolderPath('Startup')", setup_script)
        self.assertIn("$agentLauncherPath = Join-Path $installDir 'masofa_212.bat'", setup_script)
        self.assertIn("'set \"AGENT_PYTHON=%AGENT_DIR%.venv-py314\\Scripts\\pythonw.exe\"'", setup_script)
        self.assertIn('New-ScheduledTaskAction -Execute $env:ComSpec -Argument', setup_script)
        self.assertIn("('/d /c \"\"' + $agentLauncherPath + '\"\"')", setup_script)
        self.assertIn(
            '--owner-id "{}" --server-url "https://testserver:80"'.format(self.user.profile.account_id),
            setup_script,
        )
        self.assertIn('New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero)', setup_script)
        self.assertNotIn('New-ScheduledTaskAction -Execute $pythonw', setup_script)
        self.assertIn("Join-Path $env:LOCALAPPDATA 'MasofaAgent'", setup_script)
        self.assertIn('& $launcher @launcherArgs -m venv $venvDir', setup_script)
        self.assertNotIn('--clear $venvDir', setup_script)
        self.assertIn("$version = '3.14'", setup_script)
        self.assertIn("$candidateVersion -eq '3.14'", setup_script)
        self.assertIn("Join-Path $installDir '.venv-py314'", setup_script)
        self.assertNotIn("'3.10'", setup_script)
        self.assertIn('Get-CimInstance Win32_Process', setup_script)
        self.assertIn('pyautogui.screenshot().size', setup_script)
        self.assertIn('& $agentPython -m pip install --upgrade pip websockets pyautogui pillow', setup_script)
        self.assertIn("$serverUrl = 'https://testserver:80'", setup_script)
        self.assertNotIn('‘', setup_script)
        self.assertNotIn('’', setup_script)

    def test_agent_script_download_endpoint_serves_agent_source(self):
        response = self.client.get(reverse('download_agent_script'))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'async def send_screen', b''.join(response.streaming_content))

    def test_agent_redirects_pythonw_output_to_a_log_file(self):
        import agent

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {'LOCALAPPDATA': temp_dir}):
                with patch('agent.sys.stdout', None), patch('agent.sys.stderr', None):
                    agent.configure_agent_logging()
                    print('agent log test')
                    agent.sys.stdout.close()

            log_path = Path(temp_dir) / 'MasofaAgent' / 'agent.log'
            self.assertEqual(log_path.read_text(encoding='utf-8'), 'agent log test\n')


class AgentFileTransferTests(TestCase):
    def test_local_directory_listing_errors_use_the_expected_json_end_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / 'missing-masofa-folder'
            result = execute_system_command_local(f'__LIST_DIR__:{missing_path}')

        self.assertTrue(result.startswith('__JSON_START__'))
        self.assertTrue(result.endswith('__JSON_END__'))
        payload = json.loads(result[len('__JSON_START__'):-len('__JSON_END__')])
        self.assertEqual(payload['status'], 'error')

    def test_file_download_returns_stream_descriptor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / 'sample.bin'
            file_path.write_bytes(b'file-content')

            result = execute_system_command(f'__DOWNLOAD_FILE__:{file_path}')

        self.assertTrue(result.startswith('__FILE_TRANSFER_START__'))
        self.assertIn('sample.bin', result)
        self.assertTrue(result.endswith('__FILE_TRANSFER_END__'))

    def test_file_over_transfer_limit_returns_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / 'large.bin'
            file_path.write_bytes(b'abcd')
            with patch('agent.MAX_TRANSFER_BYTES', 3):
                result = execute_system_command(f'__DOWNLOAD_FILE__:{file_path}')

        self.assertIn('"status": "error"', result)
        self.assertIn('4 GiB', result)


class AgentWebFilterTests(TestCase):
    def test_blocked_domains_include_all_browser_subdomains_and_respect_allow_rules(self):
        patterns = blocked_url_patterns(
            ['allowed.example'],
            ['python.org', 'allowed.example'],
        )

        self.assertEqual(
            patterns,
            ['*://*.python.org/*', '*://python.org/*'],
        )

    def test_admin_filter_writes_only_blocked_hosts_entries(self):
        with (
            patch('agent.is_admin', return_value=True),
            patch('agent.apply_browser_url_blocklist'),
            patch('agent.read_hosts', return_value='127.0.0.1 localhost\n'),
            patch('agent.write_hosts') as write_hosts,
            patch('agent.flush_dns_cache'),
            patch('agent.remove_legacy_firewall_rules') as remove_firewall_rules,
        ):
            apply_web_filter([], ['python.org'], 'PC-Filter', 'http://localhost:8000')

        written_hosts = write_hosts.call_args.args[0]
        self.assertIn('127.0.0.1  python.org', written_hosts)
        self.assertIn('127.0.0.1  www.python.org', written_hosts)
        remove_firewall_rules.assert_called_once_with()

    def test_filter_reports_missing_admin_rights_without_changing_hosts(self):
        with (
            patch('agent.is_admin', return_value=False),
            patch('agent.apply_browser_url_blocklist'),
            patch('agent.read_hosts') as read_hosts,
            patch('agent.write_hosts') as write_hosts,
        ):
            apply_web_filter([], ['python.org'], 'PC-Filter', 'http://localhost:8000')

        read_hosts.assert_not_called()
        write_hosts.assert_not_called()

    async def test_whitelist_retries_until_browser_and_hosts_policies_apply(self):
        class StopPolling(Exception):
            pass

        response = patch('agent.urllib.request.urlopen')
        apply_filter = patch('agent.apply_web_filter', side_effect=[False, True])
        sleep_count = 0

        async def stop_after_retry(_interval):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count == 2:
                raise StopPolling

        response_mock = response.start()
        apply_mock = apply_filter.start()
        try:
            http_response = response_mock.return_value.__enter__.return_value
            http_response.status = 200
            http_response.read.return_value = json.dumps({
                'allowed_domains': [],
                'blocked_domains': ['instagram.com'],
            }).encode()
            with patch('agent.asyncio.sleep', side_effect=stop_after_retry):
                with self.assertRaises(StopPolling):
                    await poll_whitelist('PC-Filter', 'http://localhost:8000', '12345678901234567890')
        finally:
            response.stop()
            apply_filter.stop()

        self.assertEqual(apply_mock.call_count, 2)


class AgentWebsocketConnectionTests(TransactionTestCase):
    async def test_agent_streams_to_viewer_using_owner_account_id(self):
        owner = await get_user_model().objects.acreate(username='account-owner', password='StrongPass123')
        profile = await UserProfile.objects.aget(user=owner)
        machine = await Computer.objects.acreate(owner=owner, name='PC-Account', is_online=False)
        stream_path = f'/ws/screen/{profile.account_id}__PC-Account/'
        viewer = WebsocketCommunicator(URLRouter(websocket_urlpatterns), stream_path + '?role=viewer')
        agent = WebsocketCommunicator(URLRouter(websocket_urlpatterns), stream_path + '?role=agent')

        viewer_connected, _ = await viewer.connect()
        agent_connected, _ = await agent.connect()

        self.assertTrue(viewer_connected)
        self.assertTrue(agent_connected)
        self.assertTrue(await Computer.objects.filter(pk=machine.pk, is_online=True).aexists())
        await agent.send_to(bytes_data=b'account-screen-frame')
        self.assertEqual(await viewer.receive_from(), b'account-screen-frame')
        await agent.disconnect()
        await viewer.disconnect()

    async def test_agent_frames_refresh_computer_last_seen(self):
        owner = await get_user_model().objects.acreate(username='heartbeat-owner', password='StrongPass123')
        profile = await UserProfile.objects.aget(user=owner)
        machine = await Computer.objects.acreate(owner=owner, name='PC-Heartbeat', is_online=False)
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns),
            f'/ws/screen/{profile.account_id}__PC-Heartbeat/?role=agent',
        )

        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        old_last_seen = timezone.now() - timedelta(minutes=1)
        await Computer.objects.filter(pk=machine.pk).aupdate(last_seen=old_last_seen)
        with patch('my_app.consumers.AGENT_HEARTBEAT_INTERVAL', 0):
            await communicator.send_to(bytes_data=b'heartbeat-frame')

        machine = await Computer.objects.aget(pk=machine.pk)
        self.assertTrue(machine.is_online)
        self.assertGreater(machine.last_seen, old_last_seen)
        await communicator.disconnect()

    async def test_agent_connects_when_computer_names_are_duplicated(self):
        await Computer.objects.acreate(name='123')
        await Computer.objects.acreate(name='123')
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns),
            '/ws/screen/123/?role=agent',
        )

        connected, _ = await communicator.connect()

        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_agent_connects_to_the_correct_owner_when_names_are_duplicated(self):
        owner = await get_user_model().objects.acreate(username='owner', password='StrongPass123')
        recipient = await get_user_model().objects.acreate(username='recipient', password='StrongPass123')
        owner_machine = await Computer.objects.acreate(owner=owner, name='123', is_online=False)
        await Computer.objects.acreate(owner=recipient, name='123', is_online=False)

        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns),
            f'/ws/screen/{owner.id}__123/?role=agent',
        )

        connected, _ = await communicator.connect()

        self.assertTrue(connected)
        updated_machine = await Computer.objects.aget(pk=owner_machine.pk)
        self.assertTrue(updated_machine.is_online)
        self.assertFalse(await Computer.objects.filter(owner=recipient, name='123', is_online=True).aexists())
        await communicator.disconnect()

    async def test_browser_publisher_streams_frames_to_viewer(self):
        stream_key = 'user-12345678901234567890'
        viewer = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns),
            f'/ws/screen/{stream_key}/?role=viewer',
        )
        publisher = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns),
            f'/ws/screen/{stream_key}/?role=publisher',
        )

        viewer_connected, _ = await viewer.connect()
        publisher_connected, _ = await publisher.connect()

        self.assertTrue(viewer_connected)
        self.assertTrue(publisher_connected)
        await publisher.send_to(bytes_data=b'jpeg-frame')
        self.assertEqual(await viewer.receive_from(), b'jpeg-frame')
        await publisher.disconnect()
        await viewer.disconnect()


class ComputerOnlineStateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='computer-status', password='StrongPass123')
        self.client.force_login(self.user)

    def test_dashboard_placeholder_computer_starts_offline_until_agent_connects(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        computer = Computer.objects.get(owner=self.user)
        self.assertFalse(computer.is_online)
        self.assertEqual(computer.status_text, 'Offline')

    def test_dashboard_marks_inactive_agent_offline(self):
        computer = Computer.objects.create(owner=self.user, name='PC-Stale', is_online=True)
        Computer.objects.filter(pk=computer.pk).update(
            last_seen=timezone.now() - timedelta(minutes=1),
        )

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        computer.refresh_from_db()
        self.assertFalse(computer.is_online)
        self.assertEqual(computer.status_text, 'Offline')


class UserAccessIsolationTests(TestCase):
    def test_new_user_gets_20_digit_account_id(self):
        user = get_user_model().objects.create_user(username='userA', password='StrongPass123')
        profile = user.profile
        self.assertEqual(len(profile.account_id), 20)
        self.assertTrue(profile.account_id.isdigit())

    def test_user_only_sees_their_own_and_shared_computers(self):
        user_a = get_user_model().objects.create_user(username='userA', password='StrongPass123')
        user_b = get_user_model().objects.create_user(username='userB', password='StrongPass123')

        Computer.objects.create(owner=user_a, name='PC-A', username='agentA', password='passA')
        Computer.objects.create(owner=user_b, name='PC-B', username='agentB', password='passB')

        visible = Computer.objects.filter(Q(owner=user_a) | Q(shared_with=user_a)).distinct()
        self.assertTrue(visible.filter(name='PC-A').exists())
        self.assertFalse(visible.filter(name='PC-B').exists())

    def test_shared_computer_command_is_routed_to_its_owner_agent(self):
        owner = get_user_model().objects.create_user(username='route-owner', password='StrongPass123')
        recipient = get_user_model().objects.create_user(username='route-recipient', password='StrongPass123')
        Computer.objects.create(owner=owner, name='PC-Shared')
        Computer.objects.create(owner=recipient, name='PC-Shared')
        SharedAccount.objects.create(owner=owner, recipient=recipient, display_name='Shared PC')
        self.client.force_login(recipient)

        response = self.client.post(
            reverse('ajax_send_command'),
            {
                'command_text': '__LIST_DIR__:C:\\',
                'computer_name': 'PC-Shared',
                'computer_owner_id': owner.profile.account_id,
            },
        )

        self.assertEqual(response.status_code, 200)
        command = Command.objects.get(pk=response.json()['command_id'])
        route = AgentCommandRoute.objects.get(command_id=command.pk)
        self.assertEqual(command.user, recipient)
        self.assertEqual(route.target_owner, owner)
        self.assertEqual(route.computer_name, 'PC-Shared')

        recipient_agent_response = self.client.get(
            reverse('api-command'),
            {'owner_id': recipient.profile.account_id, 'agent_id': 'PC-Shared'},
        )
        agent_response = self.client.get(
            reverse('api-command'),
            {'owner_id': owner.profile.account_id, 'agent_id': 'PC-Shared'},
        )
        self.assertIsNone(recipient_agent_response.json()['id'])
        self.assertEqual(agent_response.json()['id'], command.pk)

        dashboard_response = self.client.get(reverse('home'))
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(
            dashboard_response,
            f'data-computer-name="PC-Shared" data-owner-id="{owner.profile.account_id}"',
        )
        self.assertContains(
            dashboard_response,
            f'data-computer-name="PC-Shared" data-owner-id="{recipient.profile.account_id}"',
        )

    def test_shared_computer_command_rejects_unshared_owner_id(self):
        owner = get_user_model().objects.create_user(username='route-private-owner', password='StrongPass123')
        recipient = get_user_model().objects.create_user(username='route-private-recipient', password='StrongPass123')
        Computer.objects.create(owner=owner, name='PC-Private')
        self.client.force_login(recipient)

        response = self.client.post(
            reverse('ajax_send_command'),
            {
                'command_text': '__LIST_DIR__:C:\\',
                'computer_name': 'PC-Private',
                'computer_owner_id': owner.profile.account_id,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Command.objects.exists())

    def test_sharing_account_id_adds_owner_computer_to_recipient_dashboard(self):
        owner = get_user_model().objects.create_user(username='owner', password='StrongPass123')
        recipient = get_user_model().objects.create_user(username='recipient', password='StrongPass123')
        computer = Computer.objects.create(owner=owner, name='PC-Shared')
        self.client.force_login(recipient)

        response = self.client.post(
            reverse('home'),
            {
                'form_type': 'share_access',
                'share_id': owner.profile.account_id,
                'share_name': 'Ishxonadagi kompyuterlar',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(computer.shared_with.filter(pk=recipient.pk).exists())
        self.assertEqual(
            SharedAccount.objects.get(owner=owner, recipient=recipient).display_name,
            'Ishxonadagi kompyuterlar',
        )
        self.assertContains(response, 'data-computer="PC-Shared"')
        self.assertContains(response, 'data-computer="PC-Shared" data-online="true"')
        self.assertContains(response, 'data-computer-id="' + owner.profile.account_id + '__PC-Shared"')
        self.assertContains(response, f'data-stream-id="{owner.profile.account_id}__PC-Shared"')
        self.assertContains(response, 'Ishxonadagi kompyuterlar')
        self.assertContains(response, 'id="copyAccountId"')

    def test_own_computer_uses_plain_name_for_live_stream_key(self):
        owner = get_user_model().objects.create_user(username='owner', password='StrongPass123')
        Computer.objects.create(owner=owner, name='PC-Own', is_online=True)
        self.client.force_login(owner)

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-computer-id="PC-Own"')
        self.assertContains(response, f'data-stream-id="{owner.profile.account_id}__PC-Own"')
        self.assertContains(response, f'id="watchOwnAgentButton" data-stream-id="{owner.profile.account_id}__PC-Own"')
        self.assertContains(response, 'id="expandScreenButton"')

    def test_sharing_rejects_ids_that_are_not_20_digits(self):
        owner = get_user_model().objects.create_user(username='owner', password='StrongPass123')
        recipient = get_user_model().objects.create_user(username='recipient', password='StrongPass123')
        computer = Computer.objects.create(owner=owner, name='PC-Shared')
        self.client.force_login(recipient)

        response = self.client.post(
            reverse('home'),
            {'form_type': 'share_access', 'share_id': '123', 'share_name': 'Noto\'g\'ri ID'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(computer.shared_with.filter(pk=recipient.pk).exists())
        self.assertContains(response, 'ID 20 ta raqamdan iborat')

    def test_sharing_allows_more_than_three_accounts(self):
        recipient = get_user_model().objects.create_user(username='recipient', password='StrongPass123')
        owners = [
            get_user_model().objects.create_user(username=f'owner-{index}', password='StrongPass123')
            for index in range(5)
        ]
        self.client.force_login(recipient)

        for owner in owners:
            self.client.post(
                reverse('home'),
                {
                    'form_type': 'share_access',
                    'share_id': owner.profile.account_id,
                    'share_name': owner.username,
                },
            )

        response = self.client.get(reverse('home'))

        self.assertEqual(SharedAccount.objects.filter(recipient=recipient).count(), 5)
        self.assertContains(response, '5 ta')


class FileTransferTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='transfer-user', password='StrongPass123')
        self.client.force_login(self.user)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=2)
    def test_streamed_file_upload_and_download(self):
        command = Command.objects.create(
            user=self.user,
            command_text='__DOWNLOAD_FILE__:C:\\sample.bin',
            computer_name='PC-Transfer',
        )

        with tempfile.TemporaryDirectory() as transfer_dir:
            with patch('lamp.views.TRANSFER_DIR', Path(transfer_dir)), patch('lamp.views.MAX_FILE_TRANSFER_BYTES', 3):
                upload = self.client.post(
                    f"{reverse('upload_command_transfer', args=[command.pk])}?filename=sample.bin",
                    data=b'abc',
                    content_type='application/octet-stream',
                )

                self.assertEqual(upload.status_code, 201)
                status = self.client.get(reverse('ajax_command_status', args=[command.pk])).json()
                self.assertTrue(status['is_executed'])
                self.assertEqual(status['transfer_filename'], 'sample.bin')

                download = self.client.get(status['transfer_url'])
                self.assertEqual(download.status_code, 200)
                self.assertEqual(b''.join(download.streaming_content), b'abc')
                download.close()
                self.assertEqual(list(Path(transfer_dir).iterdir()), [])

    def test_streamed_file_over_limit_is_rejected(self):
        command = Command.objects.create(
            user=self.user,
            command_text='__DOWNLOAD_FILE__:C:\\large.bin',
            computer_name='PC-Transfer',
        )

        with tempfile.TemporaryDirectory() as transfer_dir:
            with patch('lamp.views.TRANSFER_DIR', Path(transfer_dir)), patch('lamp.views.MAX_FILE_TRANSFER_BYTES', 3):
                response = self.client.post(
                    reverse('upload_command_transfer', args=[command.pk]),
                    data=b'abcd',
                    content_type='application/octet-stream',
                )

        self.assertEqual(response.status_code, 413)
        command.refresh_from_db()
        self.assertFalse(command.is_executed)


class AuthFlowTests(TestCase):
    def test_home_page_requires_login(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_can_register_and_login_user(self):
        response = self.client.post(
            reverse('register'),
            {'username': 'newuser', 'password1': 'StrongPass123', 'password2': 'StrongPass123'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(get_user_model().objects.filter(username='newuser').exists())
        self.assertRedirects(response, reverse('login'))

        login_response = self.client.post(
            reverse('login'),
            {'username': 'newuser', 'password': 'StrongPass123'},
            follow=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertTrue(login_response.wsgi_request.user.is_authenticated)

    def test_admin_account_creation_creates_staff_user(self):
        response = self.client.post(
            reverse('admin_create'),
            {'username': 'adminuser', 'password1': 'StrongPass123', 'password2': 'StrongPass123'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        user = get_user_model().objects.get(username='adminuser')
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_logged_in_user_can_create_computer(self):
        user = get_user_model().objects.create_user(username='regularuser', password='StrongPass123')
        self.client.force_login(user)

        response = self.client.post(
            reverse('home'),
            {
                'form_type': 'computer',
                'computer_name': 'PC-07',
                'computer_ip': '192.168.1.77',
                'computer_username': 'agentuser',
                'computer_password': 'agentpass123',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Computer.objects.filter(name='PC-07').exists())


class BlockedAppIsolationTests(TestCase):
    def test_user_only_sees_their_own_blocked_apps(self):
        user_a = get_user_model().objects.create_user(username='userA', password='StrongPass123')
        user_b = get_user_model().objects.create_user(username='userB', password='StrongPass123')

        BlockedApp.objects.create(user=user_a, name='app_a.exe')
        BlockedApp.objects.create(user=user_b, name='app_b.exe')

        self.client.force_login(user_a)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'app_a.exe')
        self.assertNotContains(response, 'app_b.exe')

    def test_whitelist_api_filters_blocked_apps_by_owner_id(self):
        user_a = get_user_model().objects.create_user(username='userA', password='StrongPass123')
        user_b = get_user_model().objects.create_user(username='userB', password='StrongPass123')

        BlockedApp.objects.create(user=user_a, name='app_a.exe')
        BlockedApp.objects.create(user=user_b, name='app_b.exe')

        response_a = self.client.get(reverse('api-whitelist') + f'?owner_id={user_a.profile.account_id}')
        self.assertEqual(response_a.status_code, 200)
        data_a = response_a.json()
        self.assertIn('app_a.exe', data_a['blocked_apps'])
        self.assertNotIn('app_b.exe', data_a['blocked_apps'])

    def test_shared_recipient_blocked_app_applies_to_owner_agent(self):
        owner = get_user_model().objects.create_user(username='app-policy-owner', password='StrongPass123')
        recipient = get_user_model().objects.create_user(username='app-policy-recipient', password='StrongPass123')
        SharedAccount.objects.create(owner=owner, recipient=recipient, display_name='Shared PC')
        BlockedApp.objects.create(user=recipient, name='blocked-for-shared.exe')

        response = self.client.get(
            reverse('api-whitelist'),
            {'owner_id': owner.profile.account_id},
        )

        self.assertIn('blocked-for-shared.exe', response.json()['blocked_apps'])


class SiteRuleIsolationTests(TestCase):
    def setUp(self):
        self.user_a = get_user_model().objects.create_user(username='site-owner-a', password='StrongPass123')
        self.user_b = get_user_model().objects.create_user(username='site-owner-b', password='StrongPass123')

    def test_added_site_is_visible_only_to_adding_account(self):
        self.client.force_login(self.user_a)
        response = self.client.post(
            reverse('home'),
            {'form_type': 'blocked_site', 'domain': 'private-example.com'},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'private-example.com')
        rule = SiteRule.objects.get(domain='private-example.com', is_blocked=True)
        self.assertEqual(rule.user, self.user_a)

        self.client.force_login(self.user_b)
        response = self.client.get(reverse('home'))
        self.assertNotContains(response, 'private-example.com')

    def test_agent_whitelist_returns_only_owner_site_rules(self):
        SiteRule.objects.create(user=self.user_a, domain='allowed-a.example', is_blocked=False)
        SiteRule.objects.create(user=self.user_a, domain='blocked-a.example', is_blocked=True)
        SiteRule.objects.create(user=self.user_b, domain='blocked-b.example', is_blocked=True)

        response = self.client.get(
            reverse('api-whitelist'),
            {'owner_id': self.user_a.profile.account_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['allowed_domains'], ['allowed-a.example'])
        self.assertEqual(response.json()['blocked_domains'], ['blocked-a.example'])

    def test_shared_recipient_site_rules_apply_to_owner_agent(self):
        SharedAccount.objects.create(
            owner=self.user_a,
            recipient=self.user_b,
            display_name='Shared PC',
        )
        SiteRule.objects.create(user=self.user_b, domain='shared-blocked.example', is_blocked=True)

        whitelist_response = self.client.get(
            reverse('api-whitelist'),
            {'owner_id': self.user_a.profile.account_id},
        )
        check_response = self.client.get(
            reverse('api-check-site'),
            {'owner_id': self.user_a.profile.account_id, 'domain': 'www.shared-blocked.example'},
        )

        self.assertIn('shared-blocked.example', whitelist_response.json()['blocked_domains'])
        self.assertTrue(check_response.json()['blocked'])

    def test_unidentified_agent_gets_no_accounts_site_rules(self):
        SiteRule.objects.create(user=self.user_a, domain='blocked-a.example', is_blocked=True)

        response = self.client.get(reverse('api-whitelist'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['allowed_domains'], [])
        self.assertEqual(response.json()['blocked_domains'], [])

    def test_legacy_global_site_rules_are_hidden_from_all_accounts(self):
        BlockedSite.objects.create(domain='legacy-global.example')
        self.client.force_login(self.user_a)

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'legacy-global.example')

    def test_site_check_uses_only_requested_owner_rules(self):
        SiteRule.objects.create(user=self.user_a, domain='blocked-a.example', is_blocked=True)

        response = self.client.get(
            reverse('api-check-site'),
            {'owner_id': self.user_a.profile.account_id, 'domain': 'www.blocked-a.example'},
        )
        other_account_response = self.client.get(
            reverse('api-check-site'),
            {'owner_id': self.user_b.profile.account_id, 'domain': 'www.blocked-a.example'},
        )

        self.assertTrue(response.json()['blocked'])
        self.assertFalse(other_account_response.json()['blocked'])

    def test_user_cannot_delete_another_accounts_site_rule(self):
        rule = SiteRule.objects.create(
            user=self.user_a,
            domain='private-example.com',
            is_blocked=True,
        )
        self.client.force_login(self.user_b)

        response = self.client.post(reverse('delete_blocked_site', args=[rule.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(SiteRule.objects.filter(pk=rule.pk).exists())
