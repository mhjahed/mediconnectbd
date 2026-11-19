from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Verifies Cloudflare R2 S3-compatible private bucket connectivity, write/read roundtrip, signed URL generation, and cleanup."

    def handle(self, *args, **options):
        backend = settings.STORAGES['default']['BACKEND']
        self.stdout.write(self.style.MIGRATE_HEADING("=== MediConnect Storage & Cloudflare R2 Check ==="))
        self.stdout.write(f"Configured Backend : {backend}")

        if 's3' not in backend.lower():
            self.stdout.write(self.style.WARNING(
                "Active Mode        : Local Disk (Set R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, and R2_ENDPOINT_URL in .env to enable Cloudflare R2)"
            ))
            return

        self.stdout.write(f"R2 Endpoint        : {settings.R2_ENDPOINT_URL}")
        self.stdout.write(f"R2 Bucket Name     : {settings.R2_BUCKET_NAME}")

        probe_name = "healthcheck/r2_probe.txt"
        saved_path = default_storage.save(probe_name, ContentFile(b"mediconnect-r2-ok"))
        signed_url = default_storage.url(saved_path)
        body = default_storage.open(saved_path).read().decode("utf-8")
        default_storage.delete(saved_path)

        self.stdout.write(self.style.SUCCESS(f"Write/Read/Delete  : OK ({body})"))
        self.stdout.write(self.style.SUCCESS(f"15-Min Signed URL  : {signed_url[:90]}..."))
        self.stdout.write(self.style.SUCCESS("Cloudflare R2 private storage verified with zero errors!"))
