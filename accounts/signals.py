import secrets
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import CustomUser, PatientProfile, StaffProfile


@receiver(post_save, sender=CustomUser)
def ensure_role_profile(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.role == 'PATIENT':
        PatientProfile.objects.get_or_create(user=instance)
    elif instance.role in ('NURSE', 'STAFF'):
        StaffProfile.objects.get_or_create(
            user=instance,
            defaults={
                'employee_code': f"EMP-{instance.id:04d}-{secrets.token_hex(2).upper()}",
                'position_title': 'Registered Nurse' if instance.role == 'NURSE' else 'Operations Staff',
            },
        )
