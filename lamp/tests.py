import os

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from agent import build_ws_url
from my_app.consumers import normalize_agent_id
from my_app.routing import websocket_urlpatterns
from .models import Computer, SharedAccount, UserProfile


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
        self.assertContains(response, 'data-stream-id="user-' + owner.profile.account_id + '"')
        self.assertContains(response, 'Ishxonadagi kompyuterlar')
        self.assertContains(response, 'id="copyAccountId"')

    def test_own_computer_uses_plain_name_for_live_stream_key(self):
        owner = get_user_model().objects.create_user(username='owner', password='StrongPass123')
        Computer.objects.create(owner=owner, name='PC-Own', is_online=True)
        self.client.force_login(owner)

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-computer-id="PC-Own"')

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
