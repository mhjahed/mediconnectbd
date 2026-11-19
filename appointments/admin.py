from django.contrib import admin
from .models import Availability, Appointment, DoctorReview

admin.site.register(Availability)
admin.site.register(Appointment)
admin.site.register(DoctorReview)
