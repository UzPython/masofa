import re
import unicodedata
from collections import defaultdict
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from lamp.models import Computer


VIEWERS_BY_AGENT = defaultdict(set)


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


class ScreenConsumer(AsyncWebsocketConsumer):
    @database_sync_to_async
    def _register_agent(self):
        computer = Computer.objects.filter(name=self.agent_id).order_by('pk').first()
        if computer is None:
            computer = Computer.objects.create(
                name=self.agent_id,
                ip_address='127.0.0.1',
                username=self.username or 'agent',
                password=self.password or 'agent123',
                is_online=True,
                status_text='Online',
            )
        if self.username:
            computer.username = self.username
        if self.password:
            computer.password = self.password
        computer.is_online = True
        computer.status_text = 'Online'
        computer.save()

    @database_sync_to_async
    def _mark_agent_offline(self):
        Computer.objects.filter(name=self.agent_id).update(
            is_online=False,
            status_text='Offline',
        )

    async def connect(self):
        self.agent_id = normalize_agent_id(self.scope["url_route"]["kwargs"].get("agent_id", "default"))
        query_params = parse_qs(self.scope.get("query_string", b'').decode())
        self.role = query_params.get('role', ['viewer'])[0].lower()
        self.username = query_params.get('username', [''])[0]
        self.password = query_params.get('password', [''])[0]

        if self.role == "agent":
            await self._register_agent()
            await self.accept()
            return

        if self.role == "viewer":
            VIEWERS_BY_AGENT[self.agent_id].add(self.channel_name)
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
        if self.role != "agent" or not bytes_data:
            return

        viewers = list(VIEWERS_BY_AGENT.get(self.agent_id, set()))
        for channel_name in viewers:
            await self.channel_layer.send(
                channel_name,
                {"type": "screen.message", "image": bytes_data},
            )

    async def screen_message(self, event):
        await self.send(bytes_data=event["image"])