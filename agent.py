import asyncio
import argparse
import websockets
import pyautogui
import io
import os
import sys
import ctypes
import json
import subprocess
from urllib.parse import urlencode, urlsplit, urlunsplit, quote
import urllib.request
import urllib.error

# ─────────────────────────── SOZLAMALAR ───────────────────────────
SCREEN_QUALITY = 65
FPS_DELAY = 0.08          # ~12 FPS

# Hosts fayl joylashuvi (Windows)
HOSTS_FILE = r"C:\Windows\System32\drivers\etc\hosts"
HOSTS_MARKER_BEGIN = "# === MASOFA WEB FILTER BEGIN ==="
HOSTS_MARKER_END   = "# === MASOFA WEB FILTER END ==="

# Whitelist yangilanish oralig'i (soniya)
WHITELIST_POLL_INTERVAL = 30


# ──────────────────── YORDAMCHI FUNKSIYALAR ────────────────────────

def is_admin() -> bool:
    """Agentning administrator huquqida ishlayotganini tekshirish."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_http_base_url(server_url: str) -> str:
    server_url = server_url or os.environ.get('SCREEN_SERVER_URL') or 'http://127.0.0.1:8000'
    if '://' not in server_url:
        server_url = f'http://{server_url}'
    parts = urlsplit(server_url)
    scheme = 'https' if parts.scheme in ('https', 'wss') else 'http'
    return f"{scheme}://{parts.netloc}"


def build_ws_url(agent_id: str, server_url=None, owner_id=None, username=None, password=None) -> str:
    server_url = server_url or os.environ.get('SCREEN_SERVER_URL') or 'http://127.0.0.1:8000'
    if '://' not in server_url:
        server_url = f'http://{server_url}'
    parts = urlsplit(server_url)
    scheme = {'http': 'ws', 'https': 'wss', 'ws': 'ws', 'wss': 'wss'}.get(parts.scheme)
    if not scheme:
        raise ValueError("Server URL http(s) yoki ws(s) bo'lishi kerak.")

    if owner_id:
        agent_id = f'{owner_id}__{agent_id}'

    query = {'role': 'agent'}
    if username:
        query['username'] = username
    if password:
        query['password'] = password
    path = f'/ws/screen/{quote(str(agent_id), safe="")}/'
    return urlunsplit((scheme, parts.netloc, path, urlencode(query), ''))


# ──────────────────── HOSTS FAYL BOSHQARUVI ───────────────────────

def read_hosts() -> str:
    try:
        with open(HOSTS_FILE, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception as e:
        print(f"[!] [FILTR] Hosts faylni o'qib bo'lmadi: {e}")
        return ""


def write_hosts(content: str) -> bool:
    try:
        with open(HOSTS_FILE, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except PermissionError:
        print("[!] [FILTR] Hosts faylga yozish uchun Administrator huquqi kerak!")
        return False
    except Exception as e:
        print(f"[!] [FILTR] Hosts faylga yozishda xatolik: {e}")
        return False


def strip_masofa_block(hosts_content: str) -> str:
    """Hosts fayldan oldingi Masofa bloklarini olib tashlash."""
    lines = hosts_content.splitlines(keepends=True)
    result = []
    inside_block = False
    for line in lines:
        if HOSTS_MARKER_BEGIN in line:
            inside_block = True
            continue
        if HOSTS_MARKER_END in line:
            inside_block = False
            continue
        if not inside_block:
            result.append(line)
    return ''.join(result)


def apply_web_filter(allowed_domains: list, blocked_domains: list, computer_name: str, base_url: str):
    """
    Ruxsat etilgan va taqiqlangan domenlar asosida
    hosts faylini yangilash.
    """
    if not is_admin():
        print("[!] [FILTR] Administrator huquqisiz hosts faylini o'zgartirish mumkin emas.")
        return

    current = read_hosts()
    clean = strip_masofa_block(current)

    # Taqiqlanganlar ro'yxatini shakllantiramiz
    block_lines = [HOSTS_MARKER_BEGIN]
    
    # 1. Serverdan kelgan qora ro'yxatni qo'shamiz
    for domain in blocked_domains:
        # Agar bu domen oq ro'yxatda bo'lmasa, uni bloklaymiz
        if domain.lower().strip() not in [d.lower().strip() for d in allowed_domains]:
            block_lines.append(f"127.0.0.1  {domain}")
            block_lines.append(f"127.0.0.1  www.{domain}")

    block_lines.append(HOSTS_MARKER_END)
    block_section = '\n'.join(block_lines) + '\n'

    new_content = clean.rstrip('\n') + '\n\n' + block_section

    if new_content != current:
        if write_hosts(new_content):
            print(f"[+] [FILTR] Hosts fayli yangilandi: {len(blocked_domains)} ta domen taqiqlandi.")
        else:
            print("[-] [FILTR] Hosts faylini yangilab bo'lmadi.")
    else:
        print(f"[=] [FILTR] Hosts fayli o'zgarmadi.")


# ──────────────────── ASOSIY ASYNC VAZIFALAR ──────────────────────

async def send_screen(agent_id: str, server_url=None, owner_id=None):
    """Ekran tasvirini real vaqtda serverga uzatish (avtomatik qayta ulanish bilan)."""
    websocket_url = build_ws_url(agent_id, server_url, owner_id=owner_id)
    print(f"[*] [EKRAN] WebSocket serverga ulanmoqda: {websocket_url}")

    while True:
        try:
            async with websockets.connect(websocket_url, max_size=10_000_000) as websocket:
                print(f"[+] [EKRAN] Ulandi! Jonli efir uzatilmoqda ({agent_id})...")
                while True:
                    screenshot = pyautogui.screenshot()
                    img_byte_arr = io.BytesIO()
                    screenshot.save(img_byte_arr, format='JPEG', quality=SCREEN_QUALITY)
                    await websocket.send(img_byte_arr.getvalue())
                    await asyncio.sleep(FPS_DELAY)
        except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
            print(f"[-] [EKRAN] Ulanish uzildi: {e}. 3 soniyadan so'ng qayta ulanish...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[!] [EKRAN] Kutilmagan xatolik: {e}")
            await asyncio.sleep(3)


def execute_system_command(command_text: str) -> str:
    """Tizim buyrug'ini xavfsiz bajarish va natijani qaytarish."""
    if command_text.startswith("__LIST_DIR__:"):
        path = command_text[len("__LIST_DIR__:"):].strip()
        if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
            path = path[1:-1].strip()
        if len(path) == 2 and path[1] == ':':
            path += '\\'
        try:
            import time
            items = []
            for e in os.scandir(path):
                try:
                    st = e.stat()
                    items.append({
                        'name': e.name,
                        'is_dir': e.is_dir(),
                        'size': 0 if e.is_dir() else st.st_size,
                        'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))
                    })
                except:
                    items.append({
                        'name': e.name,
                        'is_dir': e.is_dir(),
                        'size': 0,
                        'mtime': ''
                    })
            return '__JSON_START__' + json.dumps({'status': 'ok', 'path': path, 'items': items}, ensure_ascii=False) + '__JSON_END__'
        except Exception as e:
            return '__JSON_START__' + json.dumps({'status': 'error', 'message': str(e)}, ensure_ascii=False) + '__JSON_END__'

    try:
        encoding = 'cp866' if os.name == 'nt' else 'utf-8'
        result = subprocess.run(
            command_text,
            shell=True,
            capture_output=True,
            text=True,
            timeout=25,
            encoding=encoding,
            errors='replace'
        )
        output = result.stdout or ""
        if result.stderr:
            output += ("\n" if output else "") + "[XATO]:\n" + result.stderr
        if not output.strip():
            output = "[OK] Buyruq muvaffaqiyatli bajarildi (hech qanday chiqish yo'q)."
        return output
    except subprocess.TimeoutExpired:
        return "[XATO]: Buyruqni bajarish vaqti tugadi (25 soniya limiti)."
    except Exception as e:
        return f"[XATO]: Buyruqni bajarib bo'lmadi: {str(e)}"


async def poll_commands(agent_id: str, server_url=None, owner_id=None):
    """Serverdan navbatdagi buyruqlarni tekshirib borish va natijani qaytarish."""
    base_url = get_http_base_url(server_url)
    api_url = f"{base_url}/api/command/"
    if owner_id:
        api_url += f"?owner_id={owner_id}&agent_id={agent_id}"

    print(f"[*] [TERMINAL] Buyruqlar tekshiruvchisi faollashdi: {api_url}")

    while True:
        try:
            def get_command():
                req = urllib.request.Request(api_url, headers={'User-Agent': 'MasofaAgent/3.0'})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode('utf-8'))
                return None

            data = await asyncio.to_thread(get_command)

            if data and data.get("id") and data.get("command_text"):
                cmd_id = data["id"]
                cmd_text = data["command_text"]
                print(f"[+] [BUYRUQ] Yangi buyruq (ID: {cmd_id}): '{cmd_text}'")

                output = await asyncio.to_thread(execute_system_command, cmd_text)

                def post_result():
                    post_data = json.dumps({"command_id": cmd_id, "output": output}).encode('utf-8')
                    post_req = urllib.request.Request(
                        f"{base_url}/api/command/",
                        data=post_data,
                        headers={'Content-Type': 'application/json', 'User-Agent': 'MasofaAgent/3.0'}
                    )
                    with urllib.request.urlopen(post_req, timeout=5) as res:
                        return res.status == 200

                await asyncio.to_thread(post_result)
                print(f"[+] [BUYRUQ] ID: {cmd_id} natijasi yuborildi.")

        except urllib.error.URLError:
            pass
        except Exception:
            pass

        await asyncio.sleep(1.5)


async def poll_whitelist(agent_id: str, server_url=None):
    """
    Serverdan ruxsat etilgan va taqiqlangan domenlar ro'yxatini davriy ravishda yuklab,
    hosts faylini yangilash orqali bloklashni amalga oshirish.
    """
    base_url = get_http_base_url(server_url)
    api_url = f"{base_url}/api/whitelist/"

    admin_status = "✔ Administrator" if is_admin() else "✘ Administrator emas (hosts fayl o'zgartirilmaydi)"
    print(f"[*] [FILTR] Veb filtr vazifasi faollashdi. Holat: {admin_status}")
    print(f"[*] [FILTR] Whitelist manzili: {api_url}")

    last_allowed: list = []
    last_blocked: list = []

    while True:
        try:
            def fetch_whitelist():
                req = urllib.request.Request(api_url, headers={'User-Agent': 'MasofaAgent/3.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode('utf-8'))
                return None

            data = await asyncio.to_thread(fetch_whitelist)

            if data and isinstance(data.get("allowed_domains"), list):
                new_allowed = sorted(data["allowed_domains"])
                # Serverdan taqiqlanganlar ro'yxatini ham so'raymiz (agar API qo'shilgan bo'lsa)
                # Hozircha biz view.py da faqat allowed_domains qaytarganmiz, 
                # lekin bizga blocked_domains ham kerak.
                # Agar serverda blocked_domains bo'lmasa, bo'sh list olamiz.
                new_blocked = sorted(data.get("blocked_domains", []))

                if new_allowed != last_allowed or new_blocked != last_blocked:
                    print(f"[+] [FILTR] Filtrlarni yangilash: {len(new_allowed)} ruxsat, {len(new_blocked)} taqiq.")
                    await asyncio.to_thread(apply_web_filter, new_allowed, new_blocked, agent_id, base_url)
                    last_allowed = new_allowed
                    last_blocked = new_blocked

        except urllib.error.URLError:
            pass
        except Exception as e:
            print(f"[!] [FILTR] Xatolik: {e}")

        await asyncio.sleep(WHITELIST_POLL_INTERVAL)


async def main(agent_id: str, server_url=None, owner_id=None):
    print("=" * 60)
    print(f"   MASOFA AGENT v3.0 — ISHGA TUSHDI")
    print(f"   Kompyuter nomi : {agent_id}")
    print(f"   Akkaunt ID     : {owner_id or 'Umumiy'}")
    print(f"   Server         : {get_http_base_url(server_url)}")
    print(f"   Admin huquqi   : {'HA ✔' if is_admin() else 'YOQ (Veb filtr ishlamaydi!)'}")
    print("=" * 60)

    if not is_admin():
        print()
        print("  [DIQQAT] Administrator huquqisiz veb filtr ishlamaydi.")
        print("  Veb filtr uchun agentni administrator sifatida ishga tushiring.")
        print()

    # Uchta vazifani bir vaqtda (parallel) ishga tushirish
    await asyncio.gather(
        send_screen(agent_id, server_url, owner_id),
        poll_commands(agent_id, server_url, owner_id),
        poll_whitelist(agent_id, server_url),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Masofa Agent — ekran uzatish, terminal va veb filtr.'
    )
    parser.add_argument('agent_id', nargs='?', default=os.environ.get('COMPUTERNAME') or 'PC-01', help='Paneldagi kompyuter nomi, masalan: PC-01')
    parser.add_argument('--owner-id', type=str, help='Eganing 20 xonali akkaunt IDsi')
    parser.add_argument('--server-url', help='Server manzili (masalan: http://127.0.0.1:8000)')
    arguments = parser.parse_args()

    try:
        asyncio.run(main(arguments.agent_id, arguments.server_url, arguments.owner_id))
    except KeyboardInterrupt:
        print("\n[!] Agent foydalanuvchi tomonidan to'xtatildi.")
        sys.exit(0)