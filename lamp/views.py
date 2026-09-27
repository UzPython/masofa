from django.shortcuts import render, redirect, get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from .models import Command, AllowedSite, SiteWarning
from .serializers import CommandSerializer

# ==========================================
# 1. VEB-INTERFEYS (HTML) QISMI
# ==========================================

def index_view(request):
    """
    Asosiy boshqaruv paneli:
    - Yangi buyruq yuborish (POST)
    - Oq ro'yxatga yangi sayt qo'shish (POST)
    - Buyruqlar tarixi, ruxsat etilgan saytlar va ogohlantirishlarni ekranga chiqarish.
    """
    if request.method == "POST":
        # Qaysi forma yuborilganligini aniqlaymiz ('form_type' orqali)
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
                # Domen nomini tozalab saqlash (masalan: https:// dan tozalash)
                domain = domain.strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
                AllowedSite.objects.get_or_create(domain=domain)
                
        return redirect('home')

    # Ma'lumotlarni bazadan olib kelamiz
    commands = Command.objects.all().order_by('-created_at')
    allowed_sites = AllowedSite.objects.all().order_by('-created_at')
    warnings = SiteWarning.objects.all().order_by('-timestamp')

    context = {
        'commands': commands,
        'allowed_sites': allowed_sites,
        'warnings': warnings,
    }
    return render(request, 'index.html', context)


def delete_allowed_site(request, site_id):
    """Oq ro'yxatdan saytni o'chirish uchun yordamchi view"""
    site = get_object_or_404(AllowedSite, id=site_id)
    site.delete()
    return redirect('home')


# ==========================================
# 2. KOMPYUTER (AGENT) UCHUN API QISMI
# ==========================================

class GetCommandAPIView(APIView):
    """
    Bu API agent skripti bilan bog'lanadi:
    - GET: Bajarilmagan navbatdagi buyruqni qaytaradi.
    - POST: Agentdan kelgan buyruq natijasini bazaga yozadi.
    """
    permission_classes = [IsAuthenticated]

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
    """
    Agent foydalanuvchi kirmoqchi bo'lgan sayt oq ro'yxatda bor-yo'qligini 
    shu API orqali tekshiradi yoki ruxsatsiz kirish urinishini yuboradi.
    """
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