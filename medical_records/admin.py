from django.contrib import admin
from .models import MedicalRecord, Prescription, LabTest

admin.site.register(MedicalRecord)
admin.site.register(Prescription)
admin.site.register(LabTest)
