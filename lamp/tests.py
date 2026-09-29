import os

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse

from agent import build_ws_url
from my_app.consumers import normalize_agent_id
from .models import Computer


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
            {'form_type': 'share_access', 'share_id': owner.profile.account_id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(computer.shared_with.filter(pk=recipient.pk).exists())
        self.assertContains(response, 'data-computer="PC-Shared"')
        self.assertContains(response, 'id="copyAccountId"')

    def test_sharing_rejects_ids_that_are_not_20_digits(self):
        owner = get_user_model().objects.create_user(username='owner', password='StrongPass123')
        recipient = get_user_model().objects.create_user(username='recipient', password='StrongPass123')
        computer = Computer.objects.create(owner=owner, name='PC-Shared')
        self.client.force_login(recipient)

        response = self.client.post(
            reverse('home'),
            {'form_type': 'share_access', 'share_id': '123'},
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
