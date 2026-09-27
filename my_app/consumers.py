import channels.layers
from channels.generic.websocket import AsyncWebsocketConsumer

class ScreenConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("screen_stream", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("screen_stream", self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # Agentdan kelgan rasm baytlarini barcha ulangan brauzerlarga tarqatish
        if bytes_data:
            await self.channel_layer.group_send(
                "screen_stream",
                {
                    "type": "send_screen",
                    "image": bytes_data
                }
            )

    async def send_screen(self, event):
        await self.send(bytes_data=event["image"])