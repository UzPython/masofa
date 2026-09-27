import io
import time
import mss
from PIL import Image
import websocket
import json

# Django WebSocket server manzili (masalan: ws://server_ip:8000/ws/screen/)
WS_URL = "ws://127.0.0.1:8000/ws/screen/"

def stream_screen():
    print("Ekran uzatish agenti ishga tushdi...")
    
    while True:
        try:
            ws = websocket.create_connection(WS_URL)
            print("Serverga ulandi!")
            
            with mss.mss() as sct:
                monitor = sct.monitors[1] # Asosiy ekran
                
                while ws.connected:
                    # 1. Ekrandan rasm olish
                    img = sct.grab(monitor)
                    pil_img = Image.frombytes("RGB", img.size, img.rgb)
                    
                    # 2. Rasmni kichraytirish va sifatini tushurish (trafikni tejash uchun)
                    pil_img = pil_img.resize((1024, 768))
                    
                    buffer = io.BytesIO()
                    pil_img.save(buffer, format="JPEG", quality=60)
                    image_bytes = buffer.getvalue()
                    
                    # 3. Serverga yuborish
                    ws.send_binary(image_bytes)
                    
                    # Kadrlar chastotasi (FPS) - sekundiga ~5-10 kadr
                    time.sleep(0.15)
                    
        except Exception as e:
            print(f"Aloqa uzildi: {e}. Qayta ulanishga harakat qilinmoqda...")
            time.sleep(3)

if __name__ == "__main__":
    stream_screen()