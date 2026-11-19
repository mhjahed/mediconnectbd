from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, PatientProfile, DoctorProfile, StaffProfile, AccessKey, DailyAttendance


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'role', 'is_active')
    list_filter = ('role', 'is_active')
    fieldsets = UserAdmin.fieldsets + (
        ('MediConnect Role & Contact', {'fields': ('role', 'phone', 'gender', 'date_of_birth', 'address', 'profile_picture')}),
    )


admin.site.register(PatientProfile)
admin.site.register(DoctorProfile)
admin.site.register(StaffProfile)
admin.site.register(AccessKey)
admin.site.register(DailyAttendance)
