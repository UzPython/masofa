import os
from django.core.asgi import get_asgi_application

# Qat'iy belgilaymiz
os.environ['DJANGO_SETTINGS_MODULE'] = 'my_app.settings'

application = get_asgi_application()