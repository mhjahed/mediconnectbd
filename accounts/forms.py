import secrets
from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.utils import timezone
from .models import CustomUser, PatientProfile, DoctorProfile, StaffProfile, AccessKey


class NeuFormMixin:
    """Applies clean accessible attributes and neumorphic input classes."""
    def _style_fields(self):
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'neu-checkbox')
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault('class', 'neu-select')
            elif isinstance(widget, forms.Textarea):
                widget.attrs.setdefault('class', 'neu-textarea')
            else:
                widget.attrs.setdefault('class', 'neu-input')


class CustomLoginForm(NeuFormMixin, AuthenticationForm):
    username = forms.CharField(
        label='Username or Email',
        widget=forms.TextInput(attrs={'placeholder': 'Enter username or email', 'autocomplete': 'username'}),
    )
    password = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={'placeholder': 'Enter password', 'autocomplete': 'current-password'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class PatientRegistrationForm(NeuFormMixin, UserCreationForm):
    email = forms.EmailField(required=True, label='Email Address')
    first_name = forms.CharField(max_length=60, required=True, label='First Name')
    last_name = forms.CharField(max_length=60, required=True, label='Last Name')
    phone = forms.CharField(max_length=20, required=True, label='Mobile Number')
    blood_group = forms.ChoiceField(
        choices=[('', 'Optional — Select Blood Group')] + PatientProfile.BLOOD_GROUPS,
        required=False,
        label='Blood Group (Optional)',
    )
    accept_terms = forms.BooleanField(
        required=True,
        label='I agree to the Terms of Use, Telemedicine Disclaimer, and Privacy Policy.',
    )

    class Meta:
        model = CustomUser
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = 'PATIENT'
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.terms_accepted_at = timezone.now()
        if commit:
            user.save()
            profile, _ = PatientProfile.objects.get_or_create(user=user)
            profile.blood_group = self.cleaned_data.get('blood_group', '')
            profile.save()
        return user


class DoctorRegistrationForm(NeuFormMixin, UserCreationForm):
    access_key = forms.CharField(
        max_length=32,
        required=True,
        label='Clinician Access Key',
        widget=forms.TextInput(attrs={'placeholder': 'MEDI-XXXX-XXXX-XXXX'}),
    )
    email = forms.EmailField(required=True, label='Official Email')
    first_name = forms.CharField(max_length=60, required=True, label='First Name')
    last_name = forms.CharField(max_length=60, required=True, label='Last Name')
    phone = forms.CharField(max_length=20, required=True, label='Mobile Number')
    specialty = forms.ChoiceField(choices=DoctorProfile.SPECIALTIES, required=True)
    license_number = forms.CharField(max_length=50, required=True, label='BMDC Registration Number')
    qualification = forms.CharField(max_length=200, required=True, label='Qualifications (e.g. MBBS, FCPS)')
    experience_years = forms.IntegerField(min_value=0, max_value=60, initial=5, label='Years of Experience')
    consultation_fee = forms.DecimalField(max_digits=8, decimal_places=2, initial=800.00, label='Consultation Fee (BDT)')
    bio = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False, label='Clinical Summary')
    accept_terms = forms.BooleanField(
        required=True,
        label='I confirm my BMDC license is current and agree to the Clinical Governance & Privacy Policy.',
    )

    class Meta:
        model = CustomUser
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean_access_key(self):
        raw = self.cleaned_data['access_key'].strip().upper()
        try:
            key_obj = AccessKey.objects.get(key=raw, target_role='DOCTOR')
        except AccessKey.DoesNotExist:
            raise forms.ValidationError('Invalid clinician key. Request a MEDI-XXXX key from Hospital Administration.')
        if not key_obj.is_valid:
            raise forms.ValidationError(f'This key is {key_obj.get_status_display().lower()} or expired.')
        self._access_key_obj = key_obj
        return raw

    def clean_license_number(self):
        lic = self.cleaned_data['license_number'].strip().upper()
        if DoctorProfile.objects.filter(license_number__iexact=lic).exists():
            raise forms.ValidationError('This BMDC license number is already registered.')
        return lic

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = 'DOCTOR'
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.terms_accepted_at = timezone.now()
        if commit:
            user.save()
            key_obj = self._access_key_obj
            specialty = key_obj.specialty_hint or self.cleaned_data['specialty']
            DoctorProfile.objects.create(
                user=user,
                specialty=specialty,
                license_number=self.cleaned_data['license_number'],
                qualification=self.cleaned_data['qualification'],
                experience_years=self.cleaned_data['experience_years'],
                consultation_fee=self.cleaned_data['consultation_fee'],
                bio=self.cleaned_data.get('bio', ''),
                department=dict(DoctorProfile.SPECIALTIES).get(specialty, ''),
            )
            key_obj.mark_used(user)
        return user


class StaffRegistrationForm(NeuFormMixin, UserCreationForm):
    access_key = forms.CharField(
        max_length=32,
        required=True,
        label='Staff Onboarding Key',
        widget=forms.TextInput(attrs={'placeholder': 'STAFF-XXXX-XXXX-XXXX'}),
    )
    email = forms.EmailField(required=True, label='Official Email')
    first_name = forms.CharField(max_length=60, required=True)
    last_name = forms.CharField(max_length=60, required=True)
    phone = forms.CharField(max_length=20, required=True)
    department = forms.ChoiceField(choices=StaffProfile.DEPARTMENTS, required=True)
    position_title = forms.CharField(max_length=100, required=True, label='Position / Designation')
    shift = forms.ChoiceField(choices=StaffProfile.SHIFTS, required=True)
    accept_terms = forms.BooleanField(
        required=True,
        label='I agree to the Hospital Staff Access Policy and Privacy Governance.',
    )

    class Meta:
        model = CustomUser
        fields = ['username', 'first_name', 'last_name', 'email', 'phone', 'password1', 'password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean_access_key(self):
        raw = self.cleaned_data['access_key'].strip().upper()
        try:
            key_obj = AccessKey.objects.get(key=raw, target_role__in=('NURSE', 'STAFF'))
        except AccessKey.DoesNotExist:
            raise forms.ValidationError('Invalid staff key. Request a STAFF-XXXX key from Hospital Administration.')
        if not key_obj.is_valid:
            raise forms.ValidationError(f'This key is {key_obj.get_status_display().lower()} or expired.')
        self._access_key_obj = key_obj
        return raw

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        key_obj = self._access_key_obj
        user.role = key_obj.target_role
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.terms_accepted_at = timezone.now()
        if commit:
            user.save()
            dept = key_obj.department_hint or self.cleaned_data['department']
            profile, _ = StaffProfile.objects.get_or_create(
                user=user,
                defaults={'employee_code': f"EMP-{user.id:04d}-{secrets.token_hex(2).upper()}"},
            )
            profile.department = dept
            profile.position_title = self.cleaned_data['position_title']
            profile.shift = self.cleaned_data['shift']
            profile.save()
            key_obj.mark_used(user)
        return user


class UserProfileUpdateForm(NeuFormMixin, forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ['first_name', 'last_name', 'email', 'phone', 'gender', 'date_of_birth', 'address', 'profile_picture']
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
            'address': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class PatientProfileUpdateForm(NeuFormMixin, forms.ModelForm):
    class Meta:
        model = PatientProfile
        fields = ['blood_group', 'allergies', 'medical_conditions', 'emergency_contact_name', 'emergency_contact_phone', 'insurance_number']
        widgets = {
            'allergies': forms.Textarea(attrs={'rows': 2}),
            'medical_conditions': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class DoctorProfileUpdateForm(NeuFormMixin, forms.ModelForm):
    class Meta:
        model = DoctorProfile
        fields = [
            'specialty', 'qualification', 'experience_years', 'consultation_fee',
            'available_days', 'consultation_start_time', 'consultation_end_time',
            'max_patients_per_day', 'is_available', 'bio',
        ]
        widgets = {
            'consultation_start_time': forms.TimeInput(attrs={'type': 'time'}),
            'consultation_end_time': forms.TimeInput(attrs={'type': 'time'}),
            'bio': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class AccessKeyGenerateForm(NeuFormMixin, forms.ModelForm):
    expires_in_days = forms.IntegerField(
        min_value=1, max_value=365, initial=14, required=False,
        label='Expires In (Days)',
    )

    class Meta:
        model = AccessKey
        fields = ['target_role', 'doctor_name_hint', 'specialty_hint', 'department_hint', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
