from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.middleware import get_client_ip
from .decorators import admin_required, staff_member_required
from .forms import (
    CustomLoginForm,
    PatientRegistrationForm,
    DoctorRegistrationForm,
    StaffRegistrationForm,
    UserProfileUpdateForm,
    PatientProfileUpdateForm,
    DoctorProfileUpdateForm,
    AccessKeyGenerateForm,
)
from .models import CustomUser, PatientProfile, AccessKey, DailyAttendance


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:home')

    if request.method == 'POST':
        data = request.POST.copy()
        identifier = data.get('username', '').strip()
        if '@' in identifier:
            matched = CustomUser.objects.filter(email__iexact=identifier).first()
            if matched:
                data['username'] = matched.username

        form = CustomLoginForm(request, data=data)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect('dashboard:home')
    else:
        form = CustomLoginForm(request)

    return render(request, 'accounts/login.html', {'form': form})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, 'You have signed out of MediConnect.')
    return redirect('core:home')


def register_patient_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:home')

    if request.method == 'POST':
        form = PatientRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Your patient account is ready. You can now search specialists and schedule consultations.')
            return redirect('dashboard:home')
    else:
        form = PatientRegistrationForm()

    return render(request, 'accounts/register_patient.html', {'form': form})


def register_doctor_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:home')

    if request.method == 'POST':
        form = DoctorRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Clinician onboarding complete. Confirm your shift attendance to open your practice docket.')
            return redirect('accounts:attendance_checkin')
    else:
        initial = {}
        key_param = request.GET.get('key', '').strip()
        if key_param:
            initial['access_key'] = key_param
        form = DoctorRegistrationForm(initial=initial)

    return render(request, 'accounts/register_doctor.html', {'form': form})


def register_staff_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard:home')

    if request.method == 'POST':
        form = StaffRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Hospital staff registration complete. Confirm your shift attendance to proceed.')
            return redirect('accounts:attendance_checkin')
    else:
        initial = {}
        key_param = request.GET.get('key', '').strip()
        if key_param:
            initial['access_key'] = key_param
        form = StaffRegistrationForm(initial=initial)

    return render(request, 'accounts/register_staff.html', {'form': form})


@staff_member_required
def attendance_checkin_view(request):
    today = timezone.localdate()
    today_record = DailyAttendance.objects.filter(user=request.user, date=today).first()

    if request.method == 'POST':
        action = request.POST.get('action', 'checkin')
        note = request.POST.get('shift_note', '').strip()[:160]

        if action == 'checkin':
            if not today_record:
                DailyAttendance.objects.create(
                    user=request.user,
                    date=today,
                    check_in_time=timezone.now(),
                    status='PRESENT',
                    shift_note=note,
                    ip_address=get_client_ip(request),
                )
                messages.success(request, f'Shift attendance logged for {today.strftime("%d %b %Y")}.')
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect('dashboard:home')

        elif action == 'checkout' and today_record and today_record.status == 'PRESENT':
            today_record.check_out_time = timezone.now()
            today_record.status = 'COMPLETED'
            today_record.save(update_fields=['check_out_time', 'status'])
            messages.info(request, 'Shift check-out recorded.')
            return redirect('accounts:attendance_checkin')

    recent_logs = DailyAttendance.objects.filter(user=request.user).order_by('-date')[:14]
    streak = DailyAttendance.calculate_streak(request.user)

    return render(request, 'accounts/attendance.html', {
        'today_record': today_record,
        'recent_logs': recent_logs,
        'streak': streak,
        'next_url': request.GET.get('next', ''),
    })


@login_required
def profile_view(request):
    user = request.user
    patient_form = None
    doctor_form = None

    if request.method == 'POST':
        user_form = UserProfileUpdateForm(request.POST, request.FILES, instance=user)
        valid = user_form.is_valid()

        if user.is_patient:
            p_prof, _ = PatientProfile.objects.get_or_create(user=user)
            patient_form = PatientProfileUpdateForm(request.POST, instance=p_prof)
            valid = valid and patient_form.is_valid()
        elif user.is_doctor and hasattr(user, 'doctor_profile'):
            doctor_form = DoctorProfileUpdateForm(request.POST, instance=user.doctor_profile)
            valid = valid and doctor_form.is_valid()

        if valid:
            user_form.save()
            if patient_form:
                patient_form.save()
            if doctor_form:
                doctor_form.save()
            messages.success(request, 'Account and clinical profile details saved.')
            return redirect('accounts:profile')
    else:
        user_form = UserProfileUpdateForm(instance=user)
        if user.is_patient:
            p_prof, _ = PatientProfile.objects.get_or_create(user=user)
            patient_form = PatientProfileUpdateForm(instance=p_prof)
        elif user.is_doctor and hasattr(user, 'doctor_profile'):
            doctor_form = DoctorProfileUpdateForm(instance=user.doctor_profile)

    return render(request, 'accounts/profile.html', {
        'user_form': user_form,
        'patient_form': patient_form,
        'doctor_form': doctor_form,
    })


@admin_required
def manage_keys_view(request):
    if request.method == 'POST':
        form = AccessKeyGenerateForm(request.POST)
        if form.is_valid():
            key_obj = form.save(commit=False)
            key_obj.created_by = request.user
            days = form.cleaned_data.get('expires_in_days')
            if days:
                key_obj.expires_at = timezone.now() + timezone.timedelta(days=days)
            key_obj.save()
            messages.success(request, f'Generated single-use onboarding key: {key_obj.key}')
            return redirect('accounts:manage_keys')
    else:
        form = AccessKeyGenerateForm()

    role_filter = request.GET.get('role', '')
    status_filter = request.GET.get('status', '')
    qs = AccessKey.objects.select_related('created_by', 'assigned_to').all()
    if role_filter:
        qs = qs.filter(target_role=role_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/manage_keys.html', {
        'form': form,
        'page_obj': page_obj,
        'role_filter': role_filter,
        'status_filter': status_filter,
    })


@admin_required
@require_POST
def revoke_key_view(request, pk):
    key_obj = get_object_or_404(AccessKey, pk=pk)
    key_obj.revoke()
    messages.warning(request, f'Access key {key_obj.key} has been revoked.')
    return redirect('accounts:manage_keys')


@admin_required
def manage_users_view(request):
    role_filter = request.GET.get('role', '')
    q = request.GET.get('q', '').strip()

    qs = CustomUser.objects.select_related('doctor_profile', 'staff_profile').all()
    if role_filter:
        qs = qs.filter(role=role_filter)
    if q:
        qs = qs.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'accounts/manage_users.html', {
        'page_obj': page_obj,
        'role_filter': role_filter,
        'q': q,
    })


@admin_required
@require_POST
def toggle_user_active_view(request, pk):
    target = get_object_or_404(CustomUser, pk=pk)
    if target.pk == request.user.pk:
        messages.error(request, 'You cannot deactivate your own administrator session.')
        return redirect('accounts:manage_users')
    target.is_active = not target.is_active
    target.save(update_fields=['is_active'])
    state = 'activated' if target.is_active else 'deactivated'
    messages.info(request, f'Account {target.username} has been {state}.')
    return redirect('accounts:manage_users')
