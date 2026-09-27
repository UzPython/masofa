python manage.py migrate
daphne -b 0.0.0.0 -p $PORT lamp.asgi:application