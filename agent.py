import asyncio
import argparse
import websockets
import pyautogui
import io
import os
from urllib.parse import urlencode, urlsplit, urlunsplit, quote

def build_ws_url(agent_id, server_url=None, username=None, password=None, owner_id=None):
    server_url = server_url or os.environ.get('SCREEN_SERVER_URL') or 'http://127.0.0.1:8000'
    parts = urlsplit(server_url if '://' in server_url else f'http://{server_url}')
    scheme = {'http': 'ws', 'https': 'wss', 'ws': 'ws', 'wss': 'wss'}.get(parts.scheme)
    if not scheme:
        raise ValueError('Server URL http(s) yoki ws(s) bo\'lishi kerak.')

    if owner_id is not None:
        agent_id = f'{owner_id}__{agent_id}'

    query = {'role': 'agent'}
    if username:
        query['username'] = username
    if password:
        query['password'] = password
    path = f'/ws/screen/{quote(str(agent_id), safe="")}/'
    return urlunsplit((scheme, parts.netloc, path, urlencode(query), ''))

async def send_screen(agent_id, server_url=None, owner_id=None):
    websocket_url = build_ws_url(agent_id, server_url, owner_id=owner_id)
    print(f"Agent {agent_id} uchun WebSocket serverga ulanmoqda...")
    try:
        async with websockets.connect(websocket_url) as websocket:
            print("Ulanish muvaffaqiyatli! Jonli ekran uzatilmoqda...")
            while True:
                # 1. Kompyuter ekranidan skrinshot olish
                screenshot = pyautogui.screenshot()
                
                # 2. Rasmni bytes (JPEG) formatiga o'tkazish
                img_byte_arr = io.BytesIO()
                screenshot.save(img_byte_arr, format='JPEG', quality=60) # Sifatini 60% qilib tezlikni oshiramiz
                img_bytes = img_byte_arr.getvalue()
                
                # 3. WebSocket orqali serverga yuborish
                await websocket.send(img_bytes)
                
                # Kadrlar oralig'i (taxminan sekundiga 10 ta kadr)
                await asyncio.sleep(0.1)
    except Exception as e:
        print("Ulanishda xatolik yuz berdi:", e)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Kompyuter ekranini boshqaruv paneliga uzatish.')
    parser.add_argument('agent_id', help='Paneldagi kompyuter nomi, masalan: 123')
    parser.add_argument('--owner-id', type=int, help='Eganing 20 xonali akkaunt IDsi (eski user ID ham qo\'llab-quvvatlanadi).')
    parser.add_argument('--server-url', help='Panel server manzili; SCREEN_SERVER_URL muhit o\'zgaruvchisi ham ishlaydi.')
    arguments = parser.parse_args()
    asyncio.run(send_screen(arguments.agent_id, arguments.server_url, arguments.owner_id))