from django import forms
from django.forms import inlineformset_factory
from accounts.forms import NeuFormMixin
from .models import MedicalRecord, Prescription, LabTest


class MedicalRecordForm(NeuFormMixin, forms.ModelForm):
    class Meta:
        model = MedicalRecord
        fields = [
            'blood_pressure', 'pulse_rate', 'temperature', 'spo2', 'weight_kg',
            'chief_complaint', 'examination_findings', 'diagnosis',
            'clinical_advice', 'follow_up_date',
        ]
        widgets = {
            'chief_complaint': forms.Textarea(attrs={'rows': 3}),
            'examination_findings': forms.Textarea(attrs={'rows': 2}),
            'clinical_advice': forms.Textarea(attrs={'rows': 3}),
            'follow_up_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class PrescriptionItemForm(NeuFormMixin, forms.ModelForm):
    class Meta:
        model = Prescription
        fields = ['medication_name', 'dosage', 'frequency', 'meal_timing', 'duration', 'instructions']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class LabTestItemForm(NeuFormMixin, forms.ModelForm):
    class Meta:
        model = LabTest
        fields = ['test_name', 'clinical_notes', 'status']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


PrescriptionFormSet = inlineformset_factory(
    MedicalRecord,
    Prescription,
    form=PrescriptionItemForm,
    extra=2,
    can_delete=True,
)

LabTestFormSet = inlineformset_factory(
    MedicalRecord,
    LabTest,
    form=LabTestItemForm,
    extra=1,
    can_delete=True,
)
