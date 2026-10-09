import asyncio
import argparse
import ctypes
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlsplit, urlunsplit


def configure_agent_logging():
    if sys.stdout is not None and sys.stderr is not None:
        return

    log_dir = os.path.join(
        os.environ.get('LOCALAPPDATA') or tempfile.gettempdir(),
        'MasofaAgent',
    )
    os.makedirs(log_dir, exist_ok=True)
    log_stream = open(
        os.path.join(log_dir, 'agent.log'),
        'a',
        encoding='utf-8',
        buffering=1,
    )
    sys.stdout = log_stream
    sys.stderr = log_stream


configure_agent_logging()

import pyautogui
import websockets

# ??????????????????????????? SOZLAMALAR ???????????????????????????
SCREEN_QUALITY = 75
FPS_DELAY = 0.016

HOSTS_FILE = r"C:\Windows\System32\drivers\etc\hosts"
HOSTS_MARKER_BEGIN = "# === MASOFA WEB FILTER BEGIN ==="
HOSTS_MARKER_END = "# === MASOFA WEB FILTER END ==="

WHITELIST_POLL_INTERVAL = 30
MAX_TRANSFER_BYTES = 4 * 1024 * 1024 * 1024
FILE_TRANSFER_START = '__FILE_TRANSFER_START__'
FILE_TRANSFER_END = '__FILE_TRANSFER_END__'


# ???????????????????? YORDAMCHI FUNKSIYALAR ????????????????????????
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


def normalize_domain_name(domain: str) -> str:
    if domain is None:
        return ''
    value = str(domain).strip().lower().replace('https://', '').replace('http://', '')
    value = value.split('/', 1)[0].split('?', 1)[0].split('#', 1)[0].strip('.')
    if value.startswith('www.'):
        value = value[4:]
    return value


def build_ws_url(agent_id: str, server_url=None, owner_id=None, username=None, password=None):
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


# ???????????????????? HOSTS FAYL BOSHQARUVI ???????????????????????
def read_hosts() -> str:
    try:
        with open(HOSTS_FILE, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception as e:
        print(f"[!] [FILTR] Hosts faylni o'qib bo'lmadi: {e}")
        return ''


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


def flush_dns_cache():
    try:
        subprocess.run("ipconfig /flushdns", shell=True, capture_output=True, timeout=5)
        print("[+] [FILTR] DNS kesh tozalandi.")
    except Exception as e:
        print(f"[-] [FILTR] DNS keshni tozalashda xatolik: {e}")


def remove_legacy_firewall_rules():
    if os.name != 'nt':
        return
    try:
        subprocess.run(
            [
                'powershell.exe',
                '-NoProfile',
                '-Command',
                "Get-NetFirewallRule -DisplayName 'MasofaBlock_*' -ErrorAction SilentlyContinue | Remove-NetFirewallRule",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[!] [FILTR] Eski Masofa firewall qoidalarini o'chirib bo'lmadi: {error}")


def blocked_url_patterns(allowed_domains: list, blocked_domains: list) -> list[str]:
    allowed = {normalize_domain_name(domain) for domain in (allowed_domains or [])}
    blocked = {
        normalize_domain_name(domain)
        for domain in (blocked_domains or [])
        if normalize_domain_name(domain)
    } - allowed
    return sorted(
        pattern
        for domain in blocked
        for pattern in (f'*://{domain}/*', f'*://*.{domain}/*')
    )


def _sync_browser_url_blocklist(policy_path: str, tracking_path: str, patterns: list[str]) -> None:
    import winreg

    access = winreg.KEY_READ | winreg.KEY_WRITE
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, policy_path, 0, access) as policy_key:
        _, value_count, _ = winreg.QueryInfoKey(policy_key)
        existing = {
            name: value
            for index in range(value_count)
            for name, value, _ in [winreg.EnumValue(policy_key, index)]
            if isinstance(value, str)
        }

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, tracking_path, 0, access) as tracking_key:
                previous_json, _ = winreg.QueryValueEx(tracking_key, 'ManagedEntries')
            previous_entries = json.loads(previous_json)
        except FileNotFoundError:
            previous_entries = []

        for name, value in previous_entries:
            if existing.get(name) == value:
                winreg.DeleteValue(policy_key, name)
                existing.pop(name)

        managed_entries = []
        next_name = 1
        for pattern in patterns:
            if pattern in existing.values():
                continue
            while str(next_name) in existing:
                next_name += 1
            name = str(next_name)
            winreg.SetValueEx(policy_key, name, 0, winreg.REG_SZ, pattern)
            existing[name] = pattern
            managed_entries.append([name, pattern])
            next_name += 1

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, tracking_path, 0, access) as tracking_key:
        winreg.SetValueEx(tracking_key, 'ManagedEntries', 0, winreg.REG_SZ, json.dumps(managed_entries))


def apply_browser_url_blocklist(allowed_domains: list, blocked_domains: list) -> bool:
    if os.name != 'nt':
        return False
    patterns = blocked_url_patterns(allowed_domains, blocked_domains)
    browser_policies = (
        (
            r'Software\Policies\Google\Chrome\URLBlocklist',
            r'Software\MasofaAgent\BrowserPolicies\Chrome',
            'Chrome',
        ),
        (
            r'Software\Policies\Microsoft\Edge\URLBlocklist',
            r'Software\MasofaAgent\BrowserPolicies\Edge',
            'Edge',
        ),
    )
    succeeded = True
    for policy_path, tracking_path, browser_name in browser_policies:
        try:
            _sync_browser_url_blocklist(policy_path, tracking_path, patterns)
        except (ImportError, OSError, TypeError, ValueError) as error:
            print(f"[!] [FILTR] {browser_name} URL bloklash siyosatini yangilab bo'lmadi: {error}")
            succeeded = False
    return succeeded


def apply_web_filter(allowed_domains: list, blocked_domains: list, computer_name: str, base_url: str) -> bool:
    """
    Ruxsat etilgan va taqiqlangan domenlar asosida hosts faylini yangilash.
    """
    normalized_allowed = [normalize_domain_name(domain) for domain in (allowed_domains or []) if normalize_domain_name(domain)]
    normalized_blocked = [normalize_domain_name(domain) for domain in (blocked_domains or []) if normalize_domain_name(domain)]
    browser_policy_applied = apply_browser_url_blocklist(normalized_allowed, normalized_blocked)
    if not is_admin():
        print("[!] [FILTR] Administrator huquqisiz hosts fayli o'zgartirilmaydi.")
        return browser_policy_applied

    remove_legacy_firewall_rules()
    current = read_hosts()
    clean = strip_masofa_block(current)

    block_lines = [HOSTS_MARKER_BEGIN]
    for domain in sorted(set(normalized_blocked)):
        if domain.lower().strip() not in {d.lower().strip() for d in normalized_allowed}:
            block_lines.append(f"127.0.0.1  {domain}")
            block_lines.append(f"127.0.0.1  www.{domain}")

    block_lines.append(HOSTS_MARKER_END)
    block_section = '\n'.join(block_lines) + '\n'
    new_content = clean.rstrip('\n') + '\n\n' + block_section

    if new_content != current:
        if write_hosts(new_content):
            print(f"[+] [FILTR] Hosts fayli yangilandi: {len(normalized_blocked)} ta domen taqiqlandi.")
            flush_dns_cache()
            hosts_applied = True
        else:
            print("[-] [FILTR] Hosts faylini yangilab bo'lmadi.")
            hosts_applied = False
    else:
        print(f"[=] [FILTR] Hosts fayli o'zgarmadi.")
        hosts_applied = True
    return browser_policy_applied and hosts_applied


async def monitor_blocked_access(agent_id: str, server_url=None, owner_id=None):
    """Taqiqlangan saytlarga kirish urinishlarini faol kuzatish, brauzerni yopish va serverga xabar berish."""
    base_url = get_http_base_url(server_url)
    api_url = f"{base_url}/api/whitelist/"
    if owner_id:
        api_url += f"?{urlencode({'owner_id': owner_id})}"
    check_site_url = f"{base_url}/api/check-site/"

    while True:
        try:
            def check_active_window():
                if os.name != 'nt':
                    return ""
                try:
                    import ctypes
                    buf = ctypes.create_unicode_buffer(512)
                    hwnd = ctypes.windll.user32.GetForegroundWindow()
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
                    return buf.value.lower()
                except Exception:
                    return ""

            title = await asyncio.to_thread(check_active_window)
            if title:
                def get_blocked_data():
                    req = urllib.request.Request(api_url, headers={'User-Agent': 'MasofaAgent/3.0'})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        if resp.status == 200:
                            data = json.loads(resp.read().decode('utf-8'))
                            return data.get('blocked_domains', []), data.get('silent_domains', [])
                    return [], []

                blocked_domains, silent_domains = await asyncio.to_thread(get_blocked_data)
                silent_set = {normalize_domain_name(d) for d in silent_domains if normalize_domain_name(d)}
                monitored_domains = list(blocked_domains) + list(silent_domains)
                for domain in monitored_domains:
                    norm_domain = normalize_domain_name(domain)
                    base_name = norm_domain.split('.')[0] if '.' in norm_domain else norm_domain
                    if norm_domain and (norm_domain in title or (len(base_name) > 3 and base_name in title)):
                        is_silent = norm_domain in silent_set
                        def post_warning():
                            payload = {
                                "computer_name": agent_id,
                                "url": f"https://{domain} (Faol oyna: {title})",
                                "domain": domain
                            }
                            if owner_id:
                                payload["owner_id"] = owner_id
                            data = json.dumps(payload).encode('utf-8')
                            req = urllib.request.Request(
                                check_site_url,
                                data=data,
                                headers={'Content-Type': 'application/json', 'User-Agent': 'MasofaAgent/3.0'}
                            )
                            with urllib.request.urlopen(req, timeout=5):
                                pass
                        await asyncio.to_thread(post_warning)
                        
                        if is_silent:
                            print(f"[*] [FILTR] Jim kuzatilayotgan saytga kirildi (bloklanmadi): {domain}")
                        else:
                            print(f"[!] [FILTR] Taqiqlangan saytga urinish aniqlandi va yopildi: {domain}")
                            def kill_browsers():
                                try:
                                    subprocess.run("taskkill /f /im chrome.exe", shell=True, capture_output=True)
                                    subprocess.run("taskkill /f /im msedge.exe", shell=True, capture_output=True)
                                    subprocess.run("taskkill /f /im firefox.exe", shell=True, capture_output=True)
                                except Exception as error:
                                    print(f"[!] [FILTR] Taqiqlangan sayt nazoratida xatolik: {error}")
                                try:
                                    import ctypes
                                    ctypes.windll.user32.MessageBoxW(0, f"Diqqat! '{domain}' saytiga kirish taqiqlangan va brauzer yopildi!", "Masofa RMM - Xavfsizlik Ogohlantirishi", 0x30 | 0x40000)
                                except Exception:
                                    pass
                            await asyncio.to_thread(kill_browsers)
                        break
        except Exception:
            pass
        await asyncio.sleep(2)


async def monitor_blocked_apps(agent_id: str, server_url=None, owner_id=None):
    """Taqiqlangan dasturlarni aniqlash, ularni darhol yopish (taskkill) va serverga xabar berish."""
    base_url = get_http_base_url(server_url)
    api_url = f"{base_url}/api/whitelist/"
    if owner_id:
        api_url += f"?owner_id={owner_id}"

    print("[*] [DASTUR] Dastur bloklash vazifasi faollashdi.")

    while True:
        try:
            def get_blocked_apps():
                req = urllib.request.Request(api_url, headers={'User-Agent': 'MasofaAgent/3.0'})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode('utf-8')).get('blocked_apps', [])
                return []

            blocked_apps = await asyncio.to_thread(get_blocked_apps)
            if blocked_apps:
                def get_running_processes():
                    if os.name != 'nt':
                        return set()
                    try:
                        res = subprocess.run("tasklist /fo csv /nh", shell=True, capture_output=True, text=True, errors='ignore')
                        procs = set()
                        for line in res.stdout.splitlines():
                            parts = line.split('","')
                            if parts:
                                name = parts[0].strip('"').lower()
                                procs.add(name)
                        return procs
                    except Exception:
                        return set()

                running_procs = await asyncio.to_thread(get_running_processes)
                for app in blocked_apps:
                    norm_app = app.lower().strip()
                    if norm_app in running_procs:
                        def kill_and_warn():
                            try:
                                subprocess.run(f'taskkill /f /im "{norm_app}"', shell=True, capture_output=True)
                            except Exception:
                                pass
                            try:
                                import ctypes
                                ctypes.windll.user32.MessageBoxW(0, f"Diqqat! '{norm_app}' dasturini ishga tushirish taqiqlangan va yopildi!", "Masofa RMM - Xavfsizlik Ogohlantirishi", 0x30 | 0x40000)
                            except Exception:
                                pass
                            try:
                                payload = {
                                    "computer_name": agent_id,
                                    "app_name": norm_app,
                                    "owner_id": owner_id
                                }
                                data = json.dumps(payload).encode('utf-8')
                                req = urllib.request.Request(
                                    api_url,
                                    data=data,
                                    headers={'Content-Type': 'application/json', 'User-Agent': 'MasofaAgent/3.0'}
                                )
                                with urllib.request.urlopen(req, timeout=5):
                                    pass
                            except Exception:
                                pass
                        await asyncio.to_thread(kill_and_warn)
                        print(f"[!] [DASTUR] Taqiqlangan dastur ishga tushdi va yopildi: {norm_app}")
        except Exception:
            pass
        await asyncio.sleep(2)


# ???????????????????? ASOSIY ASYNC VAZIFALAR ??????????????????????
async def send_screen(agent_id: str, server_url=None, owner_id=None):
    """Ekran tasvirini real vaqtda serverga uzatish (avtomatik qayta ulanish bilan)."""
    websocket_url = build_ws_url(agent_id, server_url, owner_id=owner_id)
    print(f"[*] [EKRAN] WebSocket serverga ulanmoqda: {websocket_url}")

    while True:
        try:
            async with websockets.connect(websocket_url, max_size=10_000_000) as websocket:
                print(f"[+] [EKRAN] Ulandi! Jonli efir uzatilmoqda ({agent_id})...")
                while True:
                    frame = await asyncio.to_thread(_capture_screen_frame)
                    await websocket.send(frame)
                    await asyncio.sleep(FPS_DELAY)
        except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
            print(f"[-] [EKRAN] Ulanish uzildi: {e}. 3 soniyadan so'ng qayta ulanish...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[!] [EKRAN] Kutilmagan xatolik: {e}")
            await asyncio.sleep(3)


def _capture_screen_frame() -> bytes:
    screenshot = pyautogui.screenshot()
    img_byte_arr = io.BytesIO()
    screenshot.save(img_byte_arr, format='JPEG', quality=SCREEN_QUALITY)
    return img_byte_arr.getvalue()


def upload_transfer_file(base_url: str, command_id: int, file_path: str, filename: str) -> None:
    parts = urlsplit(base_url)
    connection_type = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
    connection = connection_type(parts.hostname, parts.port, timeout=120)
    file_size = os.path.getsize(file_path)
    if file_size > MAX_TRANSFER_BYTES:
        raise ValueError('Fayl hajmi 4 GiB limitidan oshdi.')

    transfer_url = f"/api/command/{command_id}/transfer/?{urlencode({'filename': filename})}"
    try:
        connection.putrequest('POST', transfer_url)
        connection.putheader('Content-Type', 'application/octet-stream')
        connection.putheader('Content-Length', str(file_size))
        connection.putheader('User-Agent', 'MasofaAgent/3.0')
        connection.endheaders()
        sent_size = 0
        with open(file_path, 'rb') as transfer_file:
            while chunk := transfer_file.read(1024 * 1024):
                sent_size += len(chunk)
                if sent_size > MAX_TRANSFER_BYTES:
                    raise ValueError('Fayl hajmi 4 GiB limitidan oshdi.')
                connection.send(chunk)
        if sent_size != file_size:
            raise OSError('Fayl uzatish vaqtida hajmi o\'zgardi.')

        response = connection.getresponse()
        response_body = response.read().decode('utf-8', errors='replace')
        if response.status != 201:
            raise RuntimeError(f'Fayl serverga uzatilmadi ({response.status}): {response_body}')
    finally:
        connection.close()


def execute_system_command(command_text: str) -> str:
    """Tizim buyrug'ini xavfsiz bajarish va natijani qaytarish."""
    if command_text.startswith("__DOWNLOAD_ZIP__:"):
        folder_path = command_text[len("__DOWNLOAD_ZIP__:"):].strip()
        if (folder_path.startswith('"') and folder_path.endswith('"')) or (folder_path.startswith("'") and folder_path.endswith("'")):
            folder_path = folder_path[1:-1].strip()
        try:
            zip_filename = os.path.basename(folder_path) or 'backup'
            zip_path = os.path.join(os.environ.get('TEMP', os.path.dirname(__file__) or '.'), f'{zip_filename}.zip')
            import zipfile
            with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(folder_path):
                    for file_name in files:
                        full_path = os.path.join(root, file_name)
                        try:
                            zipf.write(full_path, os.path.relpath(full_path, folder_path))
                        except Exception:
                            pass
            if os.path.getsize(zip_path) > MAX_TRANSFER_BYTES:
                os.remove(zip_path)
                raise ValueError('ZIP fayl hajmi 4 GiB limitidan oshdi.')
            return FILE_TRANSFER_START + json.dumps({'path': zip_path, 'filename': zip_filename + '.zip'}, ensure_ascii=False) + FILE_TRANSFER_END
        except Exception as e:
            return '__FILE_JSON_START__' + json.dumps({'status': 'error', 'message': str(e)}, ensure_ascii=False) + '__FILE_JSON_END__'

    if command_text.startswith("__DOWNLOAD_FILE__:"):
        path = command_text[len("__DOWNLOAD_FILE__:"):].strip()
        if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
            path = path[1:-1].strip()
        try:
            if os.path.getsize(path) > MAX_TRANSFER_BYTES:
                raise ValueError('Fayl hajmi 4 GiB limitidan oshdi.')
            filename = os.path.basename(path)
            return FILE_TRANSFER_START + json.dumps({'path': path, 'filename': filename}, ensure_ascii=False) + FILE_TRANSFER_END
        except Exception as e:
            return '__FILE_JSON_START__' + json.dumps({'status': 'error', 'message': str(e)}, ensure_ascii=False) + '__FILE_JSON_END__'

    if command_text.startswith("__LIST_DIR__:"):
        path = command_text[len("__LIST_DIR__:"):].strip()
        if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
            path = path[1:-1].strip()
        if len(path) == 2 and path[1] == ':':
            path += '\\'
        try:
            import time
            items = []
            for entry in os.scandir(path):
                try:
                    stat = entry.stat()
                    items.append({
                        'name': entry.name,
                        'is_dir': entry.is_dir(),
                        'size': 0 if entry.is_dir() else stat.st_size,
                        'mtime': time.strftime('%Y-%m-%d %H:%M', time.localtime(stat.st_mtime))
                    })
                except Exception:
                    items.append({'name': entry.name, 'is_dir': entry.is_dir(), 'size': 0, 'mtime': ''})
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
        output = result.stdout or ''
        if result.stderr:
            output += ('\n' if output else '') + '[XATO]:\n' + result.stderr
        if not output.strip():
            output = '[OK] Buyruq muvaffaqiyatli bajarildi (hech qanday chiqish yo\'q).'
        return output
    except subprocess.TimeoutExpired:
        return '[XATO]: Buyruqni bajarish vaqti tugadi (25 soniya limiti).'
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

            if data and data.get('id') and data.get('command_text'):
                cmd_id = data['id']
                cmd_text = data['command_text']
                print(f"[+] [BUYRUQ] Yangi buyruq (ID: {cmd_id}): '{cmd_text}'")

                output = await asyncio.to_thread(execute_system_command, cmd_text)
                transfer_succeeded = False
                transfer_path = None
                if output.startswith(FILE_TRANSFER_START):
                    try:
                        transfer_json = output[len(FILE_TRANSFER_START):output.index(FILE_TRANSFER_END)]
                        transfer = json.loads(transfer_json)
                        transfer_path = transfer['path']
                        await asyncio.to_thread(upload_transfer_file, base_url, cmd_id, transfer_path, transfer['filename'])
                        transfer_succeeded = True
                        print(f"[+] [BUYRUQ] ID: {cmd_id} fayli oqimli uzatildi.")
                    except Exception as e:
                        output = '__FILE_JSON_START__' + json.dumps({'status': 'error', 'message': str(e)}, ensure_ascii=False) + '__FILE_JSON_END__'
                    finally:
                        if transfer_path:
                            try:
                                os.remove(transfer_path)
                            except OSError:
                                pass

                if not transfer_succeeded:
                    def post_result():
                        post_data = json.dumps({'command_id': cmd_id, 'output': output}).encode('utf-8')
                        post_req = urllib.request.Request(
                            f"{base_url}/api/command/",
                            data=post_data,
                            headers={'Content-Type': 'application/json', 'User-Agent': 'MasofaAgent/3.0'}
                        )
                        with urllib.request.urlopen(post_req, timeout=5) as res:
                            return res.status == 200

                    await asyncio.to_thread(post_result)
                    print(f"[+] [BUYRUQ] ID: {cmd_id} natijasi yuborildi.")
        except urllib.error.URLError as error:
            print(f"[!] [BUYRUQ] Serverga ulanish xatosi: {error}")
        except Exception as error:
            print(f"[!] [BUYRUQ] Buyruqlarni tekshirishda xatolik: {error}")

        await asyncio.sleep(1.5)


async def poll_whitelist(agent_id: str, server_url=None, owner_id=None):
    """
    Serverdan ruxsat etilgan va taqiqlangan domenlar ro'yxatini davriy ravishda yuklab,
    hosts faylini yangilash orqali bloklashni amalga oshirish.
    """
    base_url = get_http_base_url(server_url)
    api_url = f"{base_url}/api/whitelist/"
    if owner_id:
        api_url += f"?{urlencode({'owner_id': owner_id})}"

    admin_status = 'Administrator' if is_admin() else 'Administrator emas (hosts fayli bloklanmaydi)'
    print(f"[*] [FILTR] Veb filtr vazifasi faollashdi. Holat: {admin_status}")
    print(f"[*] [FILTR] Whitelist manzili: {api_url}")

    last_allowed = None
    last_blocked = None

    while True:
        try:
            def fetch_whitelist():
                req = urllib.request.Request(api_url, headers={'User-Agent': 'MasofaAgent/3.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        return json.loads(resp.read().decode('utf-8'))
                return None

            data = await asyncio.to_thread(fetch_whitelist)

            if data and isinstance(data.get('allowed_domains'), list):
                new_allowed = sorted({normalize_domain_name(domain) for domain in data['allowed_domains'] if normalize_domain_name(domain)})
                new_blocked = sorted({normalize_domain_name(domain) for domain in data.get('blocked_domains', []) if normalize_domain_name(domain)})

                if new_allowed != last_allowed or new_blocked != last_blocked:
                    print(f"[+] [FILTR] Filtrlarni yangilash: {len(new_allowed)} ruxsat, {len(new_blocked)} taqiq.")
                    applied = await asyncio.to_thread(apply_web_filter, new_allowed, new_blocked, agent_id, base_url)
                    if applied:
                        last_allowed = new_allowed
                        last_blocked = new_blocked
                    else:
                        print("[!] [FILTR] Bloklash to'liq o'rnatilmadi; keyingi tekshiruvda qayta uriniladi.")
        except urllib.error.URLError as error:
            print(f"[!] [FILTR] Serverdan taqiqlarni olib bo'lmadi: {error}")
        except Exception as e:
            print(f"[!] [FILTR] Xatolik: {e}")

        await asyncio.sleep(WHITELIST_POLL_INTERVAL)


async def main(agent_id: str, server_url=None, owner_id=None):
    print('=' * 60)
    print(f'   MASOFA AGENT v3.0 ? ISHGA TUSHDI')
    print(f'   Kompyuter nomi : {agent_id}')
    print(f'   Akkaunt ID     : {owner_id or "Umumiy"}')
    print(f'   Server         : {get_http_base_url(server_url)}')
    print(f'   Admin huquqi   : {"HA ?" if is_admin() else "YOQ (Veb filtr ishlamaydi!)"}')
    print('=' * 60)

    if not is_admin():
        print()
        print('  [DIQQAT] Administrator huquqisiz hosts orqali bloklash ishlamaydi.')
        print("  Chrome va Edge siyosatlari ishlashi mumkin; to'liq bloklash uchun agentni qayta o'rnating.")
        print()

    await asyncio.gather(
        send_screen(agent_id, server_url, owner_id),
        poll_commands(agent_id, server_url, owner_id),
        poll_whitelist(agent_id, server_url, owner_id),
        monitor_blocked_access(agent_id, server_url, owner_id),
        monitor_blocked_apps(agent_id, server_url, owner_id),
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Masofa Agent ? ekran uzatish, terminal va veb filtr.')
    parser.add_argument('agent_id', nargs='?', default=os.environ.get('COMPUTERNAME') or 'PC-01', help='Paneldagi kompyuter nomi, masalan: PC-01')
    parser.add_argument('--owner-id', type=str, help='Eganing 20 xonali akkaunt IDsi')
    parser.add_argument('--server-url', help='Server manzili (masalan: http://127.0.0.1:8000)')
    arguments = parser.parse_args()

    try:
        asyncio.run(main(arguments.agent_id, arguments.server_url, arguments.owner_id))
    except KeyboardInterrupt:
        print('\n[!] Agent foydalanuvchi tomonidan to\'xtatildi.')
        sys.exit(0)
