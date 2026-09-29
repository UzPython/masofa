from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db.models import Prefetch, Q
from django.shortcuts import render, redirect, get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import Command, AllowedSite, SiteWarning, Computer, SharedAccount, UserProfile
from .serializers import CommandSerializer

MAX_SHARED_ACCOUNTS = 3


def ensure_demo_computers():
    default_names = [
        "PC-01",
        "PC-02",
        "PC-03",
        "PC-04",
        "PC-05",
    ]
    for index, name in enumerate(default_names, start=1):
        Computer.objects.get_or_create(
            name=name,
            defaults={
                "ip_address": f"192.168.1.{index + 10}",
                "username": f"agent{index}",
                "password": f"agentpass{index}",
                "is_online": index % 2 == 1,
                "status_text": "Online" if index % 2 == 1 else "Offline",
            },
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

    if request.method == "POST":
        form_type = request.POST.get("form_type")

        if form_type == "command":
            command_text = request.POST.get("command_text")
            if command_text:
                Command.objects.create(
                    user=request.user if request.user.is_authenticated else None,
                    command_text=command_text
                )
        elif form_type == "allowed_site":
            domain = request.POST.get("domain")
            if domain:
                domain = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
                AllowedSite.objects.get_or_create(domain=domain)
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
                elif (
                    not SharedAccount.objects.filter(owner=profile.user, recipient=request.user).exists()
                    and SharedAccount.objects.filter(recipient=request.user).count() >= MAX_SHARED_ACCOUNTS
                ):
                    messages.warning(request, f'Ko\'pi bilan {MAX_SHARED_ACCOUNTS} ta kompyuter/akkaunt ulash mumkin.')
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

    ensure_demo_computers()

    commands = Command.objects.filter(user=request.user).order_by('-created_at')
    allowed_sites = AllowedSite.objects.all().order_by('-created_at')
    warnings = SiteWarning.objects.all().order_by('-timestamp')
    computers = Computer.objects.filter(Q(owner=request.user) | Q(shared_with=request.user)).distinct().order_by('name')
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

    context = {
        'commands': commands,
        'allowed_sites': allowed_sites,
        'warnings': warnings,
        'computers': computers,
        'shared_accounts': shared_accounts,
        'shared_account_count': shared_account_count,
        'max_shared_accounts': MAX_SHARED_ACCOUNTS,
        'can_add_shared_account': shared_account_count < MAX_SHARED_ACCOUNTS,
        'selected_computer': selected_computer,
        'user_profile': user_profile,
        'account_id': getattr(user_profile, 'account_id', None),
    }
    return render(request, 'index.html', context)


def delete_allowed_site(request, site_id):
    site = get_object_or_404(AllowedSite, id=site_id)
    site.delete()
    return redirect('home')


# ==========================================
# 2. KOMPYUTER (AGENT) UCHUN API QISMI
# ==========================================

class GetCommandAPIView(APIView):

    def get(self, request):
        # Hali bajarilmagan eng birinchi buyruqni topamiz
        command = Command.objects.filter(is_executed=False).first()
        if command:
            serializer = CommandSerializer(command)
            return Response(serializer.data)
        return Response({"id": None, "command_text": None})

    def post(self, request):
        # Masofadagi kompyuter bajarilgan buyruq natijasini shu yerga yuboradi
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
    

    permission_classes = [AllowAny] # Agar xavfsizlik uchun Token/IsAuthenticated kerak bo'lsa, o'zgartirishingiz mumkin

    def get(self, request):
        domain = request.GET.get("domain")
        if not domain:
            return Response({"allowed": False, "error": "Domain not provided"}, status=400)
        
        # Oq ro'yxatda borligini tekshiramiz
        is_allowed = AllowedSite.objects.filter(domain__iexact=domain).exists()
        return Response({"allowed": is_allowed})

    def post(self, request):
        # Ruxsatsiz saytga kirishga urinilganda agent ushbu endpointga ma'lumot yuboradi
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