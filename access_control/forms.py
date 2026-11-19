from django import forms
from accounts.forms import NeuFormMixin
from accounts.models import CustomUser
from .models import StaffIDCard, HOSPITAL_ZONES


class StaffIDCardIssueForm(NeuFormMixin, forms.ModelForm):
    user = forms.ModelChoiceField(
        queryset=CustomUser.objects.none(),
        label='Staff Member / Clinician',
    )
    allowed_zones = forms.MultipleChoiceField(
        choices=HOSPITAL_ZONES,
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label='Authorized Hospital Rooms & Zones',
    )

    class Meta:
        model = StaffIDCard
        fields = ['user', 'department_label', 'blood_group', 'expiry_date', 'allowed_zones']
        widgets = {
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = CustomUser.objects.filter(
            role__in=('ADMIN', 'DOCTOR', 'NURSE', 'STAFF'),
            is_active=True,
        ).filter(id_card__isnull=True)
        for name, field in self.fields.items():
            if name != 'allowed_zones':
                if isinstance(field.widget, forms.Select):
                    field.widget.attrs.setdefault('class', 'neu-select')
                else:
                    field.widget.attrs.setdefault('class', 'neu-input')


class StaffIDCardEditZonesForm(NeuFormMixin, forms.ModelForm):
    allowed_zones = forms.MultipleChoiceField(
        choices=HOSPITAL_ZONES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='Authorized Hospital Rooms & Zones',
    )

    class Meta:
        model = StaffIDCard
        fields = ['department_label', 'blood_group', 'status', 'expiry_date', 'allowed_zones']
        widgets = {
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != 'allowed_zones':
                if isinstance(field.widget, forms.Select):
                    field.widget.attrs.setdefault('class', 'neu-select')
                else:
                    field.widget.attrs.setdefault('class', 'neu-input')
