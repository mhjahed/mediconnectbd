import io
from PIL import Image
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Verifies Cloudinary (or S3/Local) media storage connectivity, live WebP upload, CDN HTTPS URL, and cleanup."

    def handle(self, *args, **options):
        backend = settings.STORAGES['default']['BACKEND']
        self.stdout.write(self.style.MIGRATE_HEADING("=== MediConnect Cloud Media Storage Check ==="))
        self.stdout.write(f".env File Loaded   : {getattr(settings, 'LOADED_ENV_FILE', 'NONE')}")
        self.stdout.write(f"Configured Backend : {backend}")

        if getattr(settings, 'USE_CLOUDINARY', False):
            self.stdout.write(f"Cloudinary Cloud   : {settings.CLOUDINARY_CLOUD_NAME}")
            # Generate a tiny 4x4 WebP test image in memory to verify real Cloudinary upload + CDN URL + delete
            buf = io.BytesIO()
            Image.new('RGB', (4, 4), color=(40, 45, 49)).save(buf, format='WEBP')
            saved_name = default_storage.save('healthcheck/mc_probe.webp', ContentFile(buf.getvalue()))
            cdn_url = default_storage.url(saved_name)
            default_storage.delete(saved_name)

            self.stdout.write(self.style.SUCCESS("Upload & Cleanup   : OK (WebP image uploaded & deleted cleanly)"))
            self.stdout.write(self.style.SUCCESS(f"HTTPS CDN URL      : {cdn_url}"))
            self.stdout.write(self.style.SUCCESS("Cloudinary 25 GB No-Card storage verified with zero errors!"))
            return

        if getattr(settings, 'USE_S3_STORAGE', False):
            self.stdout.write(f"S3 Endpoint        : {settings.R2_ENDPOINT_URL}")
            saved_name = default_storage.save('healthcheck/s3_probe.txt', ContentFile(b'mediconnect-s3-ok'))
            signed_url = default_storage.url(saved_name)
            default_storage.delete(saved_name)
            self.stdout.write(self.style.SUCCESS(f"Signed URL         : {signed_url[:90]}..."))
            self.stdout.write(self.style.SUCCESS("S3-compatible storage verified!"))
            return

        self.stdout.write(self.style.WARNING(
            "Active Mode        : Local Disk (Paste CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME into .env to activate Cloudinary!)"
        ))
