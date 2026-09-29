# control_app/consumers.py
import channels.layers
from channels.generic.websocket import AsyncWebsocketConsumer


class ScreenConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.agent_id = self.scope["url_route"]["kwargs"].get("agent_id", "default")
        self.group_name = f"screen_stream_{self.agent_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if bytes_data:
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "send_screen",
                    "image": bytes_data,
                },
            )

    async def send_screen(self, event):
        await self.send(bytes_data=event["image"])