import base64
import csv
import json
import os
import tempfile
import time
import uuid
from datetime import timedelta
from pathlib import Path
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import Command, BlockedApp, SiteWarning, AppWarning, Computer, SharedAccount, UserProfile
from .serializers import CommandSerializer
from site_rules.models import AgentCommandRoute, SiteRule

MAX_FILE_TRANSFER_BYTES = 4 * 1024 * 1024 * 1024
TRANSFER_MARKER = '__TRANSFER_FILE__:'
TRANSFER_DIR = Path(tempfile.gettempdir()) / 'masofa-transfers'


def normalize_domain(value):
    if value is None:
        return ''
    value = str(value).strip().lower()
    value = value.replace('https://', '').replace('http://', '')
    value = value.split('/', 1)[0].split('?', 1)[0].split('#', 1)[0]
    value = value.strip().strip('.')
    if value.startswith('www.'):
        value = value[4:]
    return value


def normalize_app_name(value):
    if value is None:
        return ''
    value = str(value).strip().lower()
    value = value.replace('\\', '/').rsplit('/', 1)[-1].strip()
    if value and not value.endswith('.exe'):
        value += '.exe'
    return value


def parse_policy_domains(value):
    domains = []
    seen = set()
    for raw_line in str(value or '').splitlines():
        for item in raw_line.replace(',', ' ').replace(';', ' ').split():
            domain = normalize_domain(item)
            if domain and domain not in seen:
                seen.add(domain)
                domains.append(domain)
    return domains


def apply_policy_domains(user, domains, is_blocked):
    added = 0
    for domain in domains:
        SiteRule.objects.filter(user=user, domain=domain).exclude(is_blocked=is_blocked).delete()
        rule, created = SiteRule.objects.get_or_create(
            user=user,
            domain=domain,
            is_blocked=is_blocked,
        )
        if created:
            added += 1
    return added


def is_blocked_domain(candidate, blocked_domain):
    candidate = normalize_domain(candidate)
    blocked_domain = normalize_domain(blocked_domain)
    if not candidate or not blocked_domain:
        return False
    return candidate == blocked_domain or candidate.endswith(f'.{blocked_domain}')


def shared_policy_user_ids(owner):
    return [owner.pk, *SharedAccount.objects.filter(
        owner=owner,
    ).values_list('recipient_id', flat=True)]


class TransferFileResponse(FileResponse):
    def __init__(self, *args, transfer_path, **kwargs):
        self.transfer_path = Path(transfer_path)
        super().__init__(*args, **kwargs)

    def close(self):
        try:
            super().close()
        finally:
            self.transfer_path.unlink(missing_ok=True)


def _transfer_path(transfer_id):
    parsed_id = uuid.UUID(hex=transfer_id)
    return TRANSFER_DIR / f'{parsed_id.hex}.bin'


def _cleanup_old_transfers():
    cutoff = time.time() - 24 * 60 * 60
    for transfer_path in TRANSFER_DIR.glob('*.bin'):
        try:
            if transfer_path.stat().st_mtime < cutoff:
                transfer_path.unlink()
        except OSError:
            pass


@csrf_exempt
def upload_command_transfer(request, command_id):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Faqat POST so\'rov qabul qilinadi.'}, status=405)

    command = get_object_or_404(Command, id=command_id)
    if command.is_executed or not command.command_text.startswith(('__DOWNLOAD_FILE__:', '__DOWNLOAD_ZIP__:')):
        return JsonResponse({'status': 'error', 'message': 'Fayl uzatish buyrug\'i topilmadi.'}, status=409)

    try:
        expected_size = int(request.META.get('CONTENT_LENGTH', '-1'))
    except (TypeError, ValueError):
        expected_size = -1
    if expected_size < 0:
        return JsonResponse({'status': 'error', 'message': 'Fayl hajmi ko\'rsatilmagan.'}, status=411)
    if expected_size > MAX_FILE_TRANSFER_BYTES:
        return JsonResponse({'status': 'error', 'message': 'Fayl 4 GiB limitidan katta.'}, status=413)

    filename = (request.GET.get('filename') or 'download').replace('\\', '/').rsplit('/', 1)[-1].strip()
    filename = filename.replace('\x00', '')[:255] or 'download'
    transfer_id = uuid.uuid4().hex
    TRANSFER_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_transfers()
    transfer_path = _transfer_path(transfer_id)
    received_size = 0

    try:
        with transfer_path.open('wb') as transfer_file:
            while True:
                chunk = request.read(1024 * 1024)
                if not chunk:
                    break
                received_size += len(chunk)
                if received_size > MAX_FILE_TRANSFER_BYTES or received_size > expected_size:
                    transfer_path.unlink(missing_ok=True)
                    return JsonResponse({'status': 'error', 'message': 'Fayl 4 GiB limitidan katta.'}, status=413)
                transfer_file.write(chunk)

        if received_size != expected_size:
            transfer_path.unlink(missing_ok=True)
            return JsonResponse({'status': 'error', 'message': 'Fayl to\'liq qabul qilinmadi.'}, status=400)
    except OSError as error:
        transfer_path.unlink(missing_ok=True)
        return JsonResponse({'status': 'error', 'message': str(error)}, status=500)

    command.output_result = TRANSFER_MARKER + json.dumps({
        'transfer_id': transfer_id,
        'filename': filename,
        'size': received_size,
    })
    command.is_executed = True
    command.save(update_fields=['output_result', 'is_executed'])
    return JsonResponse({'status': 'success'}, status=201)


@login_required(login_url='login')
def download_command_transfer(request, command_id):
    command = get_object_or_404(Command, id=command_id, user=request.user, is_executed=True)
    if not (command.output_result or '').startswith(TRANSFER_MARKER):
        return JsonResponse({'status': 'error', 'message': 'Yuklab olinadigan fayl topilmadi.'}, status=404)

    try:
        metadata = json.loads(command.output_result[len(TRANSFER_MARKER):])
        transfer_path = _transfer_path(metadata['transfer_id'])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({'status': 'error', 'message': 'Fayl uzatish ma\'lumoti noto\'g\'ri.'}, status=404)
    if not transfer_path.is_file():
        return JsonResponse({'status': 'error', 'message': 'Vaqtinchalik fayl topilmadi.'}, status=404)

    filename = str(metadata.get('filename') or 'download').replace('\\', '/').rsplit('/', 1)[-1]
    return TransferFileResponse(
        transfer_path.open('rb'),
        transfer_path=transfer_path,
        as_attachment=True,
        filename=filename,
    )

# ==========================================
# 1. VEB-INTERFEYS (HTML) QISMI
# ==========================================

def login_view(request):
    """Foydalanuvchi kirishi uchun sahifa."""
    if request.user.is_authenticated and request.user.is_staff and request.user.is_superuser:
        return redirect('home')

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        UserProfile.create_for_user(user)
        login(request, user)
        messages.success(request, f'Xush kelibsiz, {user.username}!')
        return redirect('home')

    return render(request, 'login.html', {'form': form})


def logout_view(request):
    """Chiqish va qayta login qilishga majbur qilish."""
    logout(request)
    messages.info(request, 'Siz tizimdan chiqdingiz. Iltimos, qayta kirish uchun login va parolni kiriting.')
    return redirect('login')


def register_view(request):
    """Yangi akkaunt yaratish sahifasi."""
    if request.user.is_authenticated:
        return redirect('home')

    form = UserCreationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        user_profile = UserProfile.create_for_user(user)
        messages.success(
            request,
            f'Akkaunt yaratildi, {user.username}! Sizning maxsus ID: {user_profile.account_id}'
        )
        return redirect('login')

    return render(request, 'register.html', {'form': form})


def admin_create_view(request):
    """Admin uchun superuser/ staff akkaunt yaratish sahifasi."""
    if request.user.is_authenticated:
        return redirect('home')

    if get_user_model().objects.filter(is_staff=True, is_superuser=True).exists():
        messages.info(request, 'Admin akkaunt allaqachon mavjud. Iltimos, login qiling.')
        return redirect('login')

    form = UserCreationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.is_staff = True
        user.is_superuser = True
        user.save()
        UserProfile.create_for_user(user)
        logout(request)
        messages.success(request, f'Admin akkaunt yaratildi, {user.username}! Endi tizimga kirishingiz kerak.')
        return redirect('login')

    return render(request, 'admin_create.html', {'form': form})


@login_required(login_url='login')
def index_view(request):
    stale_cutoff = timezone.now() - timedelta(seconds=45)
    Computer.objects.filter(
        Q(owner=request.user) | Q(shared_with=request.user),
        is_online=True,
        last_seen__lt=stale_cutoff,
    ).update(is_online=False, status_text='Offline')

    search_query = (request.GET.get('q') or '').strip()

    if request.method == "POST":
        form_type = request.POST.get("form_type")

        if form_type == "command":
            command_text = request.POST.get("command_text")
            if command_text:
                Command.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    command_text=command_text.strip()
                )
                messages.success(request, 'Buyruq kompyuterga yuborildi.')
        elif form_type == "bulk_policy":
            policy_action = (request.POST.get("policy_action") or "block").lower()
            preset = (request.POST.get("policy_preset") or "custom").lower()
            raw_domains = request.POST.get("bulk_domains") or ""

            preset_map = {
                'social': ['facebook.com', 'instagram.com', 'x.com', 'twitter.com', 'tiktok.com', 'youtube.com'],
                'work': ['google.com', 'office.com', 'docs.google.com', 'drive.google.com'],
                'gaming': ['steamcommunity.com', 'discord.com', 'twitch.tv', 'epicgames.com'],
            }

            if preset in preset_map and not raw_domains.strip():
                domains = preset_map[preset]
            else:
                domains = parse_policy_domains(raw_domains)

            if not domains:
                messages.warning(request, 'Bitta ham domen kiritilmadi. Iltimos, domen yoki urllarni kiriting.')
            else:
                is_blocked = policy_action == 'block'
                added = apply_policy_domains(request.user, domains, is_blocked)
                label = 'qora ro\'yxatga' if is_blocked else 'oq ro\'yxatga'
                messages.success(request, f'{len(domains)} ta domen {label} qo\'shildi ({added} tasi yangi).')
        elif form_type == "allowed_site":
            domain = normalize_domain(request.POST.get("domain"))
            if domain:
                SiteRule.objects.filter(user=request.user, domain=domain).exclude(is_blocked=False).delete()
                SiteRule.objects.get_or_create(
                    user=request.user,
                    domain=domain,
                    is_blocked=False,
                )
                messages.success(request, f'{domain} oq ro\'yxatga qo\'shildi.')
        elif form_type == "blocked_site":
            domain = normalize_domain(request.POST.get("domain"))
            if domain:
                SiteRule.objects.filter(user=request.user, domain=domain).exclude(is_blocked=True).delete()
                SiteRule.objects.get_or_create(
                    user=request.user,
                    domain=domain,
                    is_blocked=True,
                )
                messages.success(request, f'{domain} qora ro\'yxatga (taqiqlandi) qo\'shildi.')
        elif form_type == "blocked_app":
            app_name = normalize_app_name(request.POST.get("app_name"))
            if app_name:
                BlockedApp.objects.get_or_create(user=request.user, name=app_name)
                messages.success(request, f'{app_name} dasturi qora ro\'yxatga (taqiqlandi) qo\'shildi.')
        elif form_type == "computer":
            computer_name = (request.POST.get("computer_name") or "").strip()
            ip_address = (request.POST.get("computer_ip") or "").strip() or "127.0.0.1"
            username = (request.POST.get("computer_username") or "").strip() or "agent"
            password = (request.POST.get("computer_password") or "").strip() or "agent123"
            if computer_name:
                computer, created = Computer.objects.get_or_create(
                    owner=request.user,
                    name=computer_name,
                    defaults={
                        "ip_address": ip_address,
                        "username": username,
                        "password": password,
                        "is_online": False,
                        "status_text": "Offline",
                    },
                )
                if not created:
                    computer.ip_address = ip_address
                    computer.username = username
                    computer.password = password
                    computer.save(update_fields=['ip_address', 'username', 'password'])
                messages.success(request, f'{computer_name} kompyuteri saqlandi.')
        elif form_type == "share_access":
            share_id = (request.POST.get("share_id") or "").strip()
            share_name = (request.POST.get("share_name") or "").strip()
            if not share_name:
                messages.warning(request, 'Ulangan foydalanuvchi uchun nom kiriting.')
            elif len(share_name) > 100:
                messages.warning(request, 'Nom 100 ta belgidan oshmasligi kerak.')
            elif not share_id:
                messages.warning(request, 'ID kiritilmadi.')
            elif len(share_id) != 20 or not share_id.isdigit():
                messages.warning(request, 'ID 20 ta raqamdan iborat bo\'lishi kerak.')
            else:
                profile = UserProfile.objects.filter(account_id=share_id).first()
                if not profile:
                    messages.warning(request, 'Bunday ID ega foydalanuvchi topilmadi.')
                elif profile.user == request.user:
                    messages.info(request, 'Siz o\'zingizning ID\'ingizni ulay olmaysiz.')
                else:
                    SharedAccount.objects.update_or_create(
                        owner=profile.user,
                        recipient=request.user,
                        defaults={'display_name': share_name},
                    )
                    for computer in Computer.objects.filter(owner=profile.user):
                        computer.shared_with.add(request.user)
                    messages.success(request, f'Kirish ruxsatlari berildi: {share_name}')

        return redirect('home')

    commands = Command.objects.filter(user=request.user).order_by('-created_at')
    allowed_sites = SiteRule.objects.filter(
        user=request.user,
        is_blocked=False,
    ).order_by('-created_at')
    blocked_sites = SiteRule.objects.filter(
        user=request.user,
        is_blocked=True,
    ).order_by('-created_at')
    blocked_apps = BlockedApp.objects.filter(user=request.user).order_by('-created_at')
    warnings = SiteWarning.objects.filter(
        Q(user=request.user) | Q(user__incoming_shares__recipient=request.user)
    ).distinct().order_by('-timestamp')
    app_warnings = AppWarning.objects.filter(
        Q(user=request.user) | Q(user__incoming_shares__recipient=request.user)
    ).distinct().order_by('-timestamp')
    computers = Computer.objects.filter(
        Q(owner=request.user) | Q(shared_with=request.user)
    ).distinct().order_by('name')

    if search_query:
        allowed_sites = allowed_sites.filter(domain__icontains=search_query)
        blocked_sites = blocked_sites.filter(domain__icontains=search_query)
        blocked_apps = blocked_apps.filter(name__icontains=search_query)
        warnings = warnings.filter(
            Q(domain__icontains=search_query) |
            Q(computer_name__icontains=search_query) |
            Q(url__icontains=search_query)
        )
        app_warnings = app_warnings.filter(
            Q(app_name__icontains=search_query) |
            Q(computer_name__icontains=search_query)
        )
        commands = commands.filter(
            Q(command_text__icontains=search_query) |
            Q(computer_name__icontains=search_query)
        )

    if not computers.exists():
        import socket
        hostname = socket.gethostname() or "Asosiy Kompyuter"
        Computer.objects.get_or_create(
            owner=request.user,
            name=hostname,
            defaults={
                "ip_address": "127.0.0.1",
                "username": "agent",
                "password": "agent123",
                "is_online": False,
                "status_text": "Offline",
            }
        )
        computers = Computer.objects.filter(
            Q(owner=request.user) | Q(shared_with=request.user)
        ).distinct().order_by('name')

    shared_accounts = SharedAccount.objects.filter(recipient=request.user).select_related('owner').prefetch_related(
        Prefetch(
            'owner__owned_computers',
            queryset=Computer.objects.filter(is_online=True).order_by('name', 'pk'),
            to_attr='online_owned_computers',
        )
    ).order_by('-created_at')
    shared_account_count = shared_accounts.count()
    selected_computer = computers.first()
    own_computers = Computer.objects.filter(owner=request.user).order_by('name')
    user_profile = UserProfile.objects.filter(user=request.user).first()
    if not user_profile:
        user_profile = UserProfile.create_for_user(request.user)

    total_computers = computers.count()
    online_computers = sum(1 for c in computers if c.is_online)
    total_commands = commands.count()
    total_warnings = warnings.count() + app_warnings.count()
    total_allowed_sites = allowed_sites.count()
    total_blocked_sites = blocked_sites.count()
    total_blocked_apps = blocked_apps.count()
    total_app_warnings = app_warnings.count()

    context = {
        'commands': commands,
        'allowed_sites': allowed_sites,
        'blocked_sites': blocked_sites,
        'blocked_apps': blocked_apps,
        'warnings': warnings,
        'app_warnings': app_warnings,
        'computers': computers,
        'shared_accounts': shared_accounts,
        'shared_account_count': shared_account_count,
        'selected_computer': selected_computer,
        'own_computers': own_computers,
        'user_profile': user_profile,
        'account_id': getattr(user_profile, 'account_id', None),
        'search_query': search_query,
        'total_computers': total_computers,
        'online_computers': online_computers,
        'total_commands': total_commands,
        'total_warnings': total_warnings,
        'total_allowed_sites': total_allowed_sites,
        'total_blocked_sites': total_blocked_sites,
        'total_blocked_apps': total_blocked_apps,
        'total_app_warnings': total_app_warnings,
    }
    return render(request, 'index.html', context)


@login_required(login_url='login')
def export_policy_csv(request):
    search_query = (request.GET.get('q') or '').strip()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="masofa_policy_{request.user.username}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Type', 'Value', 'Created At'])

    allowed_sites = SiteRule.objects.filter(user=request.user, is_blocked=False)
    blocked_sites = SiteRule.objects.filter(user=request.user, is_blocked=True)
    blocked_apps = BlockedApp.objects.filter(user=request.user)

    if search_query:
        allowed_sites = allowed_sites.filter(domain__icontains=search_query)
        blocked_sites = blocked_sites.filter(domain__icontains=search_query)
        blocked_apps = blocked_apps.filter(name__icontains=search_query)

    for site in allowed_sites.order_by('domain'):
        writer.writerow(['Allowed domain', site.domain, site.created_at.isoformat()])
    for site in blocked_sites.order_by('domain'):
        writer.writerow(['Blocked domain', site.domain, site.created_at.isoformat()])
    for app in blocked_apps.order_by('name'):
        writer.writerow(['Blocked app', app.name, app.created_at.isoformat()])

    return response


@login_required(login_url='login')
def delete_blocked_site(request, site_id):
    site = get_object_or_404(SiteRule, id=site_id, user=request.user, is_blocked=True)
    domain = site.domain
    site.delete()
    messages.success(request, f"'{domain}' taqiqlar ro'yxatidan olib tashlandi.")
    return redirect('home')


@login_required(login_url='login')
def delete_blocked_app(request, app_id):
    app = get_object_or_404(BlockedApp, id=app_id, user=request.user)
    name = app.name
    app.delete()
    messages.success(request, f"'{name}' dasturi taqiqlar ro'yxatidan olib tashlandi.")
    return redirect('home')


@login_required(login_url='login')
def clear_app_warnings(request):
    count = AppWarning.objects.count()
    AppWarning.objects.all().delete()
    messages.success(request, f"{count} ta dastur bloklash ogohlantirishlari tozalandi.")
    return redirect('home')


@login_required(login_url='login')
def delete_allowed_site(request, site_id):
    site = get_object_or_404(SiteRule, id=site_id, user=request.user, is_blocked=False)
    domain = site.domain
    site.delete()
    messages.success(request, f"'{domain}' oq ro'yxatdan olib tashlandi.")
    return redirect('home')


@login_required(login_url='login')
def delete_shared_account(request, share_id):
    share = get_object_or_404(SharedAccount, id=share_id, recipient=request.user)
    for comp in Computer.objects.filter(owner=share.owner):
        comp.shared_with.remove(request.user)
    display_name = share.display_name
    share.delete()
    messages.success(request, f"'{display_name}' bilan aloqa uzildi.")
    return redirect('home')


@login_required(login_url='login')
def delete_computer(request, computer_id):
    computer = get_object_or_404(Computer, id=computer_id)
    if computer.owner == request.user or computer.owner is None or request.user.is_superuser:
        comp_name = computer.name
        computer.delete()
        messages.success(request, f"'{comp_name}' kompyuteri ro'yxatdan o'chirildi.")
    else:
        messages.error(request, "Siz faqat o'zingizga tegishli kompyuterni o'chira olasiz.")
    return redirect('home')


@login_required(login_url='login')
def clear_commands(request):
    count = Command.objects.filter(user=request.user).count()
    Command.objects.filter(user=request.user).delete()
    messages.success(request, f"{count} ta buyruq tarixi tozalandi.")
    return redirect('home')


@login_required(login_url='login')
def clear_warnings(request):
    count = SiteWarning.objects.count()
    SiteWarning.objects.all().delete()
    messages.success(request, f"{count} ta xavfsizlik ogohlantirishlari tozalandi.")
    return redirect('home')


@login_required(login_url='login')
def download_agent_bat(request):
    profile = getattr(request.user, 'profile', None)
    account_id = profile.account_id if profile else "12345678901234567890"
    host = request.get_host()
    scheme = "https" if request.is_secure() else "http"
    server_url = f"{scheme}://{host}"
    safe_server_url = server_url.replace("'", "''")

    powershell_setup = """
    $ErrorActionPreference = 'Stop'
    if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $setupScriptPath = Join-Path $env:TEMP ('MasofaAgentSetup_' + [guid]::NewGuid().ToString() + '.ps1')
        [IO.File]::WriteAllText($setupScriptPath, $script, [Text.Encoding]::Unicode)
        try {
            $elevatedProcess = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $setupScriptPath + '"')
            exit $elevatedProcess.ExitCode
        } finally {
            Remove-Item -LiteralPath $setupScriptPath -Force -ErrorAction SilentlyContinue
        }
    }
    try {
        $serverUrl = '__SERVER_URL__'
        $installDir = Join-Path $env:LOCALAPPDATA 'MasofaAgent'
        New-Item -ItemType Directory -Force -Path $installDir | Out-Null

        $launcher = $null
        $launcherArgs = @()
        $pythonVersion = $null
        $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($pyLauncher) {
            $launcher = $pyLauncher.Source
            $version = '3.14'
            & $launcher "-$version" -c 'import sys; print(sys.version_info[:2])' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $launcherArgs = @("-$version")
                $pythonVersion = $version
            }
        } else {
            foreach ($name in @('python.exe', 'python3.exe')) {
                $candidate = Get-Command $name -ErrorAction SilentlyContinue
                if ($candidate -and $candidate.Source -notlike '*WindowsApps*') {
                    $candidateVersion = & $candidate.Source -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
                    if ($LASTEXITCODE -eq 0 -and $candidateVersion -eq '3.14') {
                        $launcher = $candidate.Source
                        $pythonVersion = $candidateVersion.Trim()
                        break
                    }
                }
            }
        }
        if (-not $launcher -or -not $pythonVersion) {
            throw 'Python 3.14 topilmadi. Python 3.14 o''rnating.'
        }
        $pythonProbe = & $launcher @launcherArgs -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $pythonProbe) {
            throw 'Topilgan Python ishga tushmadi. Python''ni qayta o''rnating.'
        }

        $agentPath = Join-Path $installDir 'agent.py'
        $oldAgents = @(Get-CimInstance Win32_Process | Where-Object {
            $_.Name -in @('python.exe', 'pythonw.exe') -and
            $_.CommandLine -and
            $_.CommandLine.IndexOf($agentPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
        })
        foreach ($oldAgent in $oldAgents) {
            Stop-Process -Id $oldAgent.ProcessId -Force -ErrorAction Stop
        }

        $venvDir = Join-Path $installDir '.venv-py314'
        & $launcher @launcherArgs -m venv $venvDir
        if ($LASTEXITCODE -ne 0) {
            throw 'Python virtual muhitini yaratib bo''lmadi.'
        }
        $agentPython = Join-Path $venvDir 'Scripts\\python.exe'

        & $agentPython -m pip install --upgrade pip websockets pyautogui pillow
        if ($LASTEXITCODE -ne 0) {
            throw 'Kutubxonalarni o''rnatib bo''lmadi.'
        }

        & $agentPython -c 'import pyautogui; print(pyautogui.screenshot().size)'
        if ($LASTEXITCODE -ne 0) {
            throw 'Ekran tasvirini olish sinovi bajarilmadi. Xatoni tekshiring.'
        }

        Invoke-WebRequest -UseBasicParsing -Uri ($serverUrl + '/agent/script/') -OutFile $agentPath
        if (-not (Test-Path $agentPath) -or (Get-Item $agentPath).Length -eq 0) {
            throw 'Agent fayli serverdan yuklanmadi.'
        }

        $userIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $legacyShortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'Masofa Agent.lnk'
        if (Test-Path $legacyShortcut) {
            Remove-Item -LiteralPath $legacyShortcut -Force
        }
        $agentLauncherPath = Join-Path $installDir 'masofa_212.bat'
        $launcherLines = @(
            '@echo off',
            'setlocal',
            'set "AGENT_DIR=%~dp0"',
            'set "AGENT_PYTHON=%AGENT_DIR%.venv-py314\\Scripts\\pythonw.exe"',
            'if not exist "%AGENT_PYTHON%" exit /b 1',
            '"%AGENT_PYTHON%" "%AGENT_DIR%agent.py" "%COMPUTERNAME%" --owner-id "__ACCOUNT_ID__" --server-url "__SERVER_URL__"',
            'exit /b %ERRORLEVEL%'
        )
        $agentLauncher = $launcherLines -join "`r`n"
        [IO.File]::WriteAllText($agentLauncherPath, $agentLauncher, [Text.Encoding]::ASCII)
        $action = New-ScheduledTaskAction -Execute $env:ComSpec -Argument ('/d /c ""' + $agentLauncherPath + '""') -WorkingDirectory $installDir
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $userIdentity
        $principal = New-ScheduledTaskPrincipal -UserId $userIdentity -LogonType Interactive -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName 'Masofa Agent' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
        Start-ScheduledTask -TaskName 'Masofa Agent'
        Write-Host 'Masofa Agent o''rnatildi va ishga tushirildi.' -ForegroundColor Green
    } catch {
        Write-Host ('O''rnatishda xato: ' + $_.Exception.Message) -ForegroundColor Red
        exit 1
    }
""".replace('__SERVER_URL__', safe_server_url).replace('__ACCOUNT_ID__', str(account_id))
    encoded_setup = base64.b64encode(powershell_setup.encode('utf-16le')).decode('ascii')
    encoded_setup_lines = '\n'.join(
        f'{" >" if index == 0 else " >>"} "%PS_ENCODED_FILE%" echo {encoded_setup[offset:offset + 160]}'.lstrip()
        for index, offset in enumerate(range(0, len(encoded_setup), 160))
    )
    bat_content = f"""@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Masofa Agent O'rnatish
>nul 2>&1 net session
if %errorlevel% neq 0 (
    echo Administrator huquqi talab qilinmoqda... UAC oynasida "Yes" ni bosing...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)
set "PS_ENCODED_FILE=%TEMP%\\MasofaAgentSetup_%RANDOM%_%RANDOM%.b64"
{encoded_setup_lines}
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$encoded = [IO.File]::ReadAllText($env:PS_ENCODED_FILE); $script = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($encoded)); & ([scriptblock]::Create($script))"
set "INSTALL_ERROR=%ERRORLEVEL%"
del /q "%PS_ENCODED_FILE%" >nul 2>&1
if not "%INSTALL_ERROR%"=="0" (
    echo.
    echo O'rnatishda xato yuz berdi. Yuqoridagi xabarni tekshiring.
    pause
    exit /b 1
)
echo.
echo Agent o'rnatildi. Agent logi: %LOCALAPPDATA%\\MasofaAgent\\agent.log
pause
"""
    response = HttpResponse(bat_content, content_type='application/x-bat')
    response['Content-Disposition'] = 'attachment; filename="masofa_212.bat"'
    return response


def download_agent_script(request):
    agent_path = Path(__file__).resolve().parent.parent / 'agent.py'
    return FileResponse(agent_path.open('rb'), content_type='text/x-python', as_attachment=True, filename='agent.py')


def execute_system_command_local(command_text: str) -> str:
    import os
    import json
    import base64
    import zipfile
    import tempfile
    import subprocess
    import time

    if command_text.startswith("__DOWNLOAD_ZIP__:"):
        folder_path = command_text[len("__DOWNLOAD_ZIP__:"):].strip()
        if (folder_path.startswith('"') and folder_path.endswith('"')) or (folder_path.startswith("'") and folder_path.endswith("'")):
            folder_path = folder_path[1:-1].strip()
        try:
            base_name = os.path.basename(folder_path.rstrip('\\/')) or 'folder'
            zip_filename = f"{base_name}_masofa.zip"
            zip_path = os.path.join(tempfile.gettempdir(), zip_filename)
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(folder_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        try:
                            arcname = os.path.relpath(file_path, folder_path)
                            zipf.write(file_path, arcname)
                        except Exception:
                            pass
            
            with open(zip_path, 'rb') as f:
                content_bytes = f.read()
            b64_data = base64.b64encode(content_bytes).decode('utf-8')
            try:
                os.remove(zip_path)
            except:
                pass
            return '__FILE_JSON_START__' + json.dumps({'status': 'ok', 'filename': zip_filename, 'data': b64_data}, ensure_ascii=False) + '__FILE_JSON_END__'
        except Exception as e:
            return '__FILE_JSON_START__' + json.dumps({'status': 'error', 'message': str(e)}, ensure_ascii=False) + '__FILE_JSON_END__'

    if command_text.startswith("__DOWNLOAD_FILE__:"):
        path = command_text[len("__DOWNLOAD_FILE__:"):].strip()
        if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
            path = path[1:-1].strip()
        try:
            with open(path, 'rb') as f:
                content_bytes = f.read()
            b64_data = base64.b64encode(content_bytes).decode('utf-8')
            filename = os.path.basename(path)
            return '__FILE_JSON_START__' + json.dumps({'status': 'ok', 'filename': filename, 'data': b64_data}, ensure_ascii=False) + '__FILE_JSON_END__'
        except Exception as e:
            return '__FILE_JSON_START__' + json.dumps({'status': 'error', 'message': str(e)}, ensure_ascii=False) + '__FILE_JSON_END__'

    if command_text.startswith("__LIST_DIR__:"):
        path = command_text[len("__LIST_DIR__:"):].strip()
        if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
            path = path[1:-1].strip()
        if len(path) == 2 and path[1] == ':':
            path += '\\'
        try:
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
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        res = subprocess.run(
            command_text,
            shell=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=15,
            startupinfo=startupinfo
        )
        output = res.stdout + res.stderr
        if not output.strip():
            output = "[Bajarildi] Buyruq muvaffaqiyatli bajarildi (natija yo'q)."
        return output
    except Exception as e:
        return f"[Xato] Buyruqni bajarishda xatolik: {e}"


@login_required(login_url='login')
def ajax_send_command(request):
    """AJAX orqali terminal yoki fayl menejeri buyrug'ini agent navbatiga qo'shish"""
    if request.method == "POST":
        command_text = (request.POST.get("command_text") or "").strip()
        computer_name = (request.POST.get("computer_name") or "").strip()
        if not command_text:
            return JsonResponse({"status": "error", "message": "Buyruq matni bo'sh bo'lishi mumkin emas."}, status=400)

        target_owner_id = (request.POST.get("computer_owner_id") or "").strip()
        target_owner = request.user
        if target_owner_id:
            if len(target_owner_id) != 20 or not target_owner_id.isdigit() or not computer_name:
                return JsonResponse({"status": "error", "message": "Kompyuter akkaunt IDsi yoki nomi noto'g'ri."}, status=400)
            target_profile = UserProfile.objects.filter(account_id=target_owner_id).first()
            if not target_profile:
                return JsonResponse({"status": "error", "message": "Kompyuter egasi topilmadi."}, status=404)
            target_owner = target_profile.user
            if not Computer.objects.filter(owner=target_owner, name=computer_name).exists():
                return JsonResponse({"status": "error", "message": "Tanlangan kompyuter egasiga tegishli emas."}, status=404)
            if target_owner != request.user and not SharedAccount.objects.filter(
                owner=target_owner,
                recipient=request.user,
            ).exists():
                return JsonResponse({"status": "error", "message": "Bu kompyuterni boshqarish uchun ruxsat yo'q."}, status=403)

        with transaction.atomic():
            cmd = Command.objects.create(
                user=request.user,
                command_text=command_text,
                computer_name=computer_name if computer_name else None,
            )
            if target_owner != request.user:
                AgentCommandRoute.objects.create(
                    command_id=cmd.pk,
                    target_owner=target_owner,
                    computer_name=computer_name,
                )
        return JsonResponse({
            "status": "success",
            "command_id": cmd.id,
            "command_text": cmd.command_text,
            "created_at": cmd.created_at.strftime("%Y-%m-%d %H:%M:%S")
        })
    return JsonResponse({"status": "error", "message": "Faqat POST so'rov qabul qilinadi."}, status=405)


@login_required(login_url='login')
def ajax_command_status(request, command_id):
    """AJAX orqali buyruq bajarilish natijasini tekshirish"""
    cmd = get_object_or_404(Command, id=command_id, user=request.user)
    result = {
        "command_id": cmd.id,
        "is_executed": cmd.is_executed,
        "output_result": cmd.output_result or "",
        "created_at": cmd.created_at.strftime("%Y-%m-%d %H:%M:%S")
    }
    if cmd.is_executed and (cmd.output_result or '').startswith(TRANSFER_MARKER):
        try:
            metadata = json.loads(cmd.output_result[len(TRANSFER_MARKER):])
            result['transfer_url'] = reverse('download_command_transfer', args=[cmd.id])
            result['transfer_filename'] = metadata.get('filename', 'download')
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return JsonResponse(result)


# ==========================================
# 2. KOMPYUTER (AGENT) UCHUN API QISMI
# ==========================================

class GetCommandAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        owner_id = request.GET.get("owner_id")
        agent_id = request.GET.get("agent_id")
        queryset = Command.objects.filter(is_executed=False)
        
        if owner_id:
            profile = UserProfile.objects.filter(account_id=str(owner_id)).first()
            if profile:
                own_command_ids = Command.objects.filter(
                    user=profile.user,
                ).exclude(
                    pk__in=AgentCommandRoute.objects.values('command_id'),
                ).values('pk')
                routed_command_ids = AgentCommandRoute.objects.filter(
                    target_owner=profile.user,
                )
                if agent_id:
                    routed_command_ids = routed_command_ids.filter(computer_name__iexact=agent_id)
                queryset = queryset.filter(
                    Q(pk__in=own_command_ids) |
                    Q(pk__in=routed_command_ids.values('command_id'))
                )
            elif str(owner_id).isdigit():
                queryset = queryset.filter(user_id=owner_id)
            else:
                queryset = queryset.none()

        if agent_id:
            queryset = queryset.filter(
                Q(computer_name__isnull=True) |
                Q(computer_name='') |
                Q(computer_name__iexact=agent_id)
            )
        
        command = queryset.order_by('created_at').first()
        if command:
            serializer = CommandSerializer(command)
            return Response(serializer.data)
        return Response({"id": None, "command_text": None})

    def post(self, request):
        command_id = request.data.get("command_id")
        output = request.data.get("output")
        
        try:
            command = Command.objects.get(id=command_id)
            command.output_result = output
            command.is_executed = True
            command.save()
            return Response({"status": "success"})
        except Command.DoesNotExist:
            return Response({"status": "not found"}, status=404)


class CheckSiteAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        domain = normalize_domain(request.GET.get("domain"))
        if not domain:
            return Response({"allowed": False, "blocked": False, "error": "Domain not provided"}, status=400)

        owner_id = request.GET.get("owner_id")
        profile = UserProfile.objects.filter(account_id=str(owner_id)).first() if owner_id else None
        rules = SiteRule.objects.filter(user__in=shared_policy_user_ids(profile.user)) if profile else SiteRule.objects.none()
        blocked_sites = list(rules.filter(is_blocked=True).values_list('domain', flat=True))
        if any(is_blocked_domain(domain, blocked_site) for blocked_site in blocked_sites):
            return Response({"allowed": False, "blocked": True, "message": f"{domain} taqiqlangan sayt."})

        is_allowed = rules.filter(is_blocked=False, domain__iexact=domain).exists()
        return Response({"allowed": is_allowed, "blocked": False, "message": "Saytga ruxsat berilgan." if is_allowed else "Sayt qoidaga muvofiq."})

    def post(self, request):
        computer_name = request.data.get("computer_name", "Noma'lum kompyuter")
        owner_id = request.data.get("owner_id")
        url = request.data.get("url")
        domain = normalize_domain(request.data.get("domain") or url)

        if not domain:
            return Response({"status": "invalid data"}, status=400)

        target_user = None
        display_name = computer_name
        if owner_id:
            profile = UserProfile.objects.filter(account_id=str(owner_id)).first()
            if profile:
                target_user = profile.user
                display_name = profile.user.username

        if url:
            SiteWarning.objects.create(
                user=target_user,
                computer_name=display_name,
                url=url,
                domain=domain,
            )
            return Response({"status": "warning recorded"}, status=201)

        return Response({"status": "invalid data"}, status=400)


class WhitelistAPIView(APIView):
    """Agent uchun: ruxsat etilgan va taqiqlangan domenlar hamda dasturlar ro'yxatini qaytaradi."""
    permission_classes = [AllowAny]

    def get(self, request):
        owner_id = request.GET.get("owner_id")
        profile = UserProfile.objects.filter(account_id=str(owner_id)).first() if owner_id else None
        rules = SiteRule.objects.filter(user__in=shared_policy_user_ids(profile.user)) if profile else SiteRule.objects.none()
        allowed = list(rules.filter(is_blocked=False).values_list('domain', flat=True))
        blocked = list(rules.filter(is_blocked=True).values_list('domain', flat=True))
        
        if owner_id:
            blocked_apps_qs = BlockedApp.objects.none()
            profile = UserProfile.objects.filter(account_id=str(owner_id)).first()
            if profile:
                blocked_apps_qs = BlockedApp.objects.filter(
                    user__in=shared_policy_user_ids(profile.user),
                )
            elif str(owner_id).isdigit():
                blocked_apps_qs = BlockedApp.objects.filter(user_id=owner_id)
        else:
            blocked_apps_qs = BlockedApp.objects.all()
            
        blocked_apps = list(blocked_apps_qs.values_list('name', flat=True))
        return Response({
            "allowed_domains": allowed,
            "blocked_domains": blocked,
            "blocked_apps": blocked_apps,
            "allowed_count": len(allowed),
            "blocked_count": len(blocked),
            "blocked_apps_count": len(blocked_apps)
        })

    def post(self, request):
        computer_name = request.data.get("computer_name", "Noma'lum kompyuter")
        owner_id = request.data.get("owner_id")
        app_name = request.data.get("app_name")

        if not app_name:
            return Response({"status": "invalid data"}, status=400)

        target_user = None
        display_name = computer_name
        if owner_id:
            profile = UserProfile.objects.filter(account_id=str(owner_id)).first()
            if profile:
                target_user = profile.user
                display_name = profile.user.username

        AppWarning.objects.create(
            user=target_user,
            computer_name=display_name,
            app_name=app_name,
        )
        return Response({"status": "app warning recorded"}, status=201)


class AjaxBlockSiteView(APIView):
    """AJAX orqali saytni taqiqlash uchun API."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        domain = normalize_domain(request.data.get("domain"))
        if not domain:
            return Response({"status": "error", "message": "Domen kiritilmadi."}, status=400)

        SiteRule.objects.get_or_create(user=request.user, domain=domain, is_blocked=True)
        return Response({"status": "success", "message": f"{domain} taqiqlandi."})


@login_required(login_url='login')
def export_warnings_csv(request):
    """Barcha sayt ogohlantirishlarini CSV formatida yuklab olish"""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="site_warnings.csv"'
    response.write('\ufeff'.encode('utf8'))  # BOM for Excel UTF-8 support
    writer = csv.writer(response)
    writer.writerow(['ID', 'Kompyuter', 'Domen', 'Havola', 'Vaqt'])
    for w in SiteWarning.objects.all().order_by('-timestamp'):
        writer.writerow([w.id, w.computer_name, w.domain, w.url, w.timestamp.strftime('%Y-%m-%d %H:%M:%S')])
    return response


@login_required(login_url='login')
def export_commands_csv(request):
    """Buyruqlar tarixini CSV formatida yuklab olish"""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="commands_history.csv"'
    response.write('\ufeff'.encode('utf8'))
    writer = csv.writer(response)
    writer.writerow(['ID', 'Foydalanuvchi', 'Buyruq', 'Bajarildi', 'Natija', 'Vaqt'])
    for c in Command.objects.filter(user=request.user).order_by('-created_at'):
        writer.writerow([
            c.id,
            c.user.username if c.user else 'Noma\'lum',
            c.command_text,
            'Ha' if c.is_executed else 'Yo\'q',
            c.output_result or '',
            c.created_at.strftime('%Y-%m-%d %H:%M:%S')
        ])
    return response
