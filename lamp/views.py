import csv
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db.models import Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import Command, AllowedSite, BlockedSite, SiteWarning, Computer, SharedAccount, UserProfile
from .serializers import CommandSerializer

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
        elif form_type == "allowed_site":
            domain = request.POST.get("domain")
            if domain:
                domain = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
                AllowedSite.objects.get_or_create(domain=domain)
                messages.success(request, f'{domain} oq ro\'yxatga qo\'shildi.')
        elif form_type == "blocked_site":
            domain = request.POST.get("domain")
            if domain:
                domain = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
                BlockedSite.objects.get_or_create(domain=domain)
                messages.success(request, f'{domain} qora ro\'yxatga (taqiqlandi) qo\'shildi.')
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
                        "is_online": True,
                        "status_text": "Online",
                    },
                )
                if not created:
                    computer.ip_address = ip_address
                    computer.username = username
                    computer.password = password
                    computer.is_online = True
                    computer.status_text = "Online"
                    computer.save(update_fields=['ip_address', 'username', 'password', 'is_online', 'status_text'])
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
    allowed_sites = AllowedSite.objects.all().order_by('-created_at')
    blocked_sites = BlockedSite.objects.all().order_by('-created_at')
    warnings = SiteWarning.objects.all().order_by('-timestamp')
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
    user_profile = UserProfile.objects.filter(user=request.user).first()

    total_computers = computers.count()
    online_computers = sum(1 for c in computers if c.is_online)
    total_commands = commands.count()
    total_warnings = warnings.count()
    total_allowed_sites = allowed_sites.count()
    total_blocked_sites = blocked_sites.count()

    context = {
        'commands': commands,
        'allowed_sites': allowed_sites,
        'blocked_sites': blocked_sites,
        'warnings': warnings,
        'computers': computers,
        'shared_accounts': shared_accounts,
        'shared_account_count': shared_account_count,
        'selected_computer': selected_computer,
        'user_profile': user_profile,
        'account_id': getattr(user_profile, 'account_id', None),
        'total_computers': total_computers,
        'online_computers': online_computers,
        'total_commands': total_commands,
        'total_warnings': total_warnings,
        'total_allowed_sites': total_allowed_sites,
        'total_blocked_sites': total_blocked_sites,
    }
    return render(request, 'index.html', context)


@login_required(login_url='login')
def delete_blocked_site(request, site_id):
    site = get_object_or_404(BlockedSite, id=site_id)
    domain = site.domain
    site.delete()
    messages.success(request, f"'{domain}' taqiqlar ro'yxatidan olib tashlandi.")
    return redirect('home')


@login_required(login_url='login')
def delete_allowed_site(request, site_id):
    site = get_object_or_404(AllowedSite, id=site_id)
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

    bat_content = f"""@echo off
chcp 65001 >nul
title Masofa Agent v3.0 - Masofaviy Boshqaruv va Veb Filtr

:: ── UAC: Administrator huquqini so'rash ──────────────────────────
>nul 2>&1 "%SYSTEMROOT%\\system32\\cacls.exe" "%SYSTEMROOT%\\system32\\config\\system"
if '%errorlevel%' NEQ '0' (
    echo Administrator huquqi talab etiladi. Iltimos ruxsat bering...
    powershell -Command "Start-Process -FilePath '%~dpnx0' -Verb RunAs"
    exit /b
)
:: ─────────────────────────────────────────────────────────────────

echo ========================================================
echo       MASOFAVIY BOSHQARUV TIZIMI - AGENT v3.0
echo ========================================================
echo Foydalanuvchi: {request.user.username}
echo Akkaunt ID   : {account_id}
echo Server       : {server_url}
echo Administrator: HA (Veb filtr faol)
echo ========================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [XATOLIK] Kompyuteringizda Python o'rnatilmagan!
    echo Iltimos, https://www.python.org saytidan Python 3.10+ o'rnating.
    pause
    exit /b 1
)

echo Kerakli kutubxonalar tekshirilmoqda...
pip install websockets pyautogui pillow requests >nul 2>&1

echo Agent ishga tushirilmoqda (%COMPUTERNAME%)...
echo Veb filtr (hosts fayl bloklash) faol!
echo.
python agent.py "%COMPUTERNAME%" --owner-id {account_id} --server-url {server_url}

pause
"""
    response = HttpResponse(bat_content, content_type='application/x-bat')
    response['Content-Disposition'] = 'attachment; filename="Masofa_Agent_Runner.bat"'
    return response


@login_required(login_url='login')
def ajax_send_command(request):
    """AJAX orqali terminal buyrug'ini qabul qilish"""
    if request.method == "POST":
        command_text = (request.POST.get("command_text") or "").strip()
        if not command_text:
            return JsonResponse({"status": "error", "message": "Buyruq matni bo'sh bo'lishi mumkin emas."}, status=400)
        
        cmd = Command.objects.create(
            user=request.user,
            command_text=command_text
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
    return JsonResponse({
        "command_id": cmd.id,
        "is_executed": cmd.is_executed,
        "output_result": cmd.output_result or "",
        "created_at": cmd.created_at.strftime("%Y-%m-%d %H:%M:%S")
    })


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
                queryset = queryset.filter(user=profile.user)
            elif str(owner_id).isdigit():
                queryset = queryset.filter(user_id=owner_id)
        
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
        domain = request.GET.get("domain")
        if not domain:
            return Response({"allowed": False, "error": "Domain not provided"}, status=400)
        
        # Birinchi navbatda taqiqlanganmi yoki yo'qligini tekshiramiz
        if BlockedSite.objects.filter(domain__iexact=domain).exists():
            return Response({"allowed": False, "blocked": True})
            
        is_allowed = AllowedSite.objects.filter(domain__iexact=domain).exists()
        return Response({"allowed": is_allowed, "blocked": False})

    def post(self, request):
        computer_name = request.data.get("computer_name", "Noma'lum kompyuter")
        url = request.data.get("url")
        domain = request.data.get("domain")

        if url and domain:
            SiteWarning.objects.create(
                computer_name=computer_name,
                url=url,
                domain=domain
            )
            return Response({"status": "warning recorded"}, status=201)
        
        return Response({"status": "invalid data"}, status=400)


class WhitelistAPIView(APIView):
    """Agent uchun: ruxsat etilgan va taqiqlangan domenlar ro'yxatini qaytaradi."""
    permission_classes = [AllowAny]

    def get(self, request):
        allowed = list(AllowedSite.objects.values_list('domain', flat=True))
        blocked = list(BlockedSite.objects.values_list('domain', flat=True))
        return Response({
            "allowed_domains": allowed,
            "blocked_domains": blocked,
            "allowed_count": len(allowed),
            "blocked_count": len(blocked)
        })


class AjaxBlockSiteView(APIView):
    """AJAX orqali saytni taqiqlash uchun API."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        domain = request.data.get("domain")
        if not domain:
            return Response({"status": "error", "message": "Domen kiritilmadi."}, status=400)
        
        domain = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
        BlockedSite.objects.get_or_create(domain=domain)
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
