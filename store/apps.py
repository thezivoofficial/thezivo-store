from django.apps import AppConfig


class StoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'store'

    def ready(self):
        import store.security  # noqa: F401 — registers login alert signal

        import sys
        # Only start scheduler in the main process (not during migrations, tests, etc.)
        if "migrate" not in sys.argv and "makemigrations" not in sys.argv and "test" not in sys.argv:
            from store.scheduler import start
            start()
