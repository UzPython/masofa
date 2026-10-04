import re
import time
import unicodedata
from collections import defaultdict
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from lamp.models import Computer, UserProfile


VIEWERS_BY_AGENT = defaultdict(set)
AGENT_HEARTBEAT_INTERVAL = 15


def normalize_agent_id(agent_id):
    value = str(agent_id or '').strip()
    if not value:
        return 'default'

    value = value.replace('\\', '/').split('/')[-1]
    value = value.lstrip('.')
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^A-Za-z0-9_.-]+', '_', value)
    value = value.strip('._-') or 'default'
    return value[:99]


def extract_agent_identity(agent_id):
    value = normalize_agent_id(agent_id)
    if '__' not in value:
        return None, value

    owner_id, _, computer_name = value.rpartition('__')
    if owner_id.isdigit() and computer_name:
        return int(owner_id), computer_name
    return None, value


def resolve_owner_id(owner_key):
    if owner_key is None:
        return None

    account_owner_id = UserProfile.objects.filter(account_id=str(owner_key)).values_list('user_id', flat=True).first()
    if account_owner_id is not None:
        return account_owner_id
    if len(str(owner_key)) == 20:
        return None
    return owner_key


class ScreenConsumer(AsyncWebsocketConsumer):
    @database_sync_to_async
    def _register_agent(self):
        owner_key, computer_name = extract_agent_identity(self.agent_id)
        owner_id = resolve_owner_id(owner_key)
        if owner_key is not None and owner_id is None:
            return False
        queryset = Computer.objects.filter(name=computer_name)
        if owner_id is not None:
            queryset = queryset.filter(owner_id=owner_id)

        computer = queryset.order_by('pk').first()
        if computer is None:
            computer = Computer.objects.create(
                name=computer_name,
                owner_id=owner_id,
                ip_address='127.0.0.1',
                username=self.username or 'agent',
                password=self.password or 'agent123',
                is_online=True,
                status_text='Online',
            )
        if owner_id is not None:
            computer.owner_id = owner_id
        if self.username:
            computer.username = self.username
        if self.password:
            computer.password = self.password
        computer.is_online = True
        computer.status_text = 'Online'
        computer.save()
        return True

    @database_sync_to_async
    def _mark_agent_offline(self):
        owner_key, computer_name = extract_agent_identity(self.agent_id)
        owner_id = resolve_owner_id(owner_key)
        if owner_key is not None and owner_id is None:
            return
        queryset = Computer.objects.filter(name=computer_name)
        if owner_id is not None:
            queryset = queryset.filter(owner_id=owner_id)
        queryset.update(
            is_online=False,
            status_text='Offline',
        )

    @database_sync_to_async
    def _touch_agent(self):
        owner_key, computer_name = extract_agent_identity(self.agent_id)
        owner_id = resolve_owner_id(owner_key)
        if owner_key is not None and owner_id is None:
            return
        queryset = Computer.objects.filter(name=computer_name)
        if owner_id is not None:
            queryset = queryset.filter(owner_id=owner_id)
        queryset.update(
            is_online=True,
            status_text='Online',
            last_seen=timezone.now(),
        )

    async def connect(self):
        self.agent_id = normalize_agent_id(self.scope["url_route"]["kwargs"].get("agent_id", "default"))
        query_params = parse_qs(self.scope.get("query_string", b'').decode())
        self.role = query_params.get('role', ['viewer'])[0].lower()
        self.username = query_params.get('username', [''])[0]
        self.password = query_params.get('password', [''])[0]

        if self.role == "agent":
            if not await self._register_agent():
                await self.close(code=4404)
                return
            self.last_seen_touch = time.monotonic()
            await self.accept()
            return

        if self.role == "viewer":
            VIEWERS_BY_AGENT[self.agent_id].add(self.channel_name)
            await self.accept()
            return

        if self.role == "publisher":
            await self.accept()
            return

        await self.close(code=4400)

    async def disconnect(self, close_code):
        if self.role == "agent":
            await self._mark_agent_offline()
            return

        if self.role == "viewer":
            VIEWERS_BY_AGENT.get(self.agent_id, set()).discard(self.channel_name)
            if not VIEWERS_BY_AGENT.get(self.agent_id):
                VIEWERS_BY_AGENT.pop(self.agent_id, None)

    async def receive(self, text_data=None, bytes_data=None):
        if self.role not in {"agent", "publisher"} or not bytes_data:
            return

        if self.role == "agent":
            now = time.monotonic()
            if now - self.last_seen_touch >= AGENT_HEARTBEAT_INTERVAL:
                await self._touch_agent()
                self.last_seen_touch = now

        viewers = list(VIEWERS_BY_AGENT.get(self.agent_id, set()))
        for channel_name in viewers:
            await self.channel_layer.send(
                channel_name,
                {"type": "screen.message", "image": bytes_data},
            )

    async def screen_message(self, event):
        await self.send(bytes_data=event["image"])