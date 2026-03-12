web: python manage.py migrate && (python manage.py createsuperuser --noinput || true) && gunicorn Trendza_store.wsgi --workers 2 --bind 0.0.0.0:$PORT
