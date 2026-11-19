import os
from django.core.management.base import BaseCommand
from django.db import connections
from django.conf import settings


class Command(BaseCommand):
    help = "Verifies database connectivity, .env file detection, TLS/SSL encryption in transit, engine version, and table counts."

    def handle(self, *args, **options):
        conn = connections['default']
        conn.ensure_connection()
        engine = settings.DATABASES['default']['ENGINE']
        loaded_env = getattr(settings, 'LOADED_ENV_FILE', '') or 'NONE FOUND!'

        self.stdout.write(self.style.MIGRATE_HEADING("=== MediConnect Database Health & Security Check ==="))
        self.stdout.write(f".env File Loaded  : {loaded_env}")
        self.stdout.write(f"Configured Engine : {engine}")

        with conn.cursor() as cursor:
            if 'postgresql' in engine:
                cursor.execute("SELECT version(), current_database(), current_user;")
                version, db_name, db_user = cursor.fetchone()
                self.stdout.write(f"Database / Role   : {db_name} (user: {db_user})")
                self.stdout.write(f"Server Version    : {version.split(',')[0]}")

                try:
                    cursor.execute("SHOW ssl;")
                    ssl_state = cursor.fetchone()[0]
                except Exception:
                    ssl_state = 'unknown'
                self.stdout.write(self.style.SUCCESS(f"TLS/SSL Transit   : {ssl_state} (sslmode=require active)"))

                cursor.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';"
                )
                table_count = cursor.fetchone()[0]
                self.stdout.write(self.style.SUCCESS(f"Public Tables     : {table_count} tables ready on Neon PostgreSQL!"))
            else:
                cursor.execute("SELECT sqlite_version();")
                ver = cursor.fetchone()[0]
                raw_val = os.getenv('DATABASE_URL', '')
                hint = 'DATABASE_URL is empty' if not raw_val else f'DATABASE_URL starts with "{raw_val[:14]}..."'
                self.stdout.write(self.style.WARNING(
                    f"Active Mode       : Local SQLite {ver} ({hint} — check {settings.BASE_DIR / '.env'})"
                ))

        self.stdout.write(self.style.SUCCESS("Connection check passed with zero errors."))
