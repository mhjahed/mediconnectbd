from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/patient/', views.register_patient_view, name='register_patient'),
    path('register/doctor/', views.register_doctor_view, name='register_doctor'),
    path('register/staff/', views.register_staff_view, name='register_staff'),
    path('attendance/', views.attendance_checkin_view, name='attendance_checkin'),
    path('profile/', views.profile_view, name='profile'),
    path('keys/', views.manage_keys_view, name='manage_keys'),
    path('keys/<int:pk>/revoke/', views.revoke_key_view, name='revoke_key'),
    path('users/', views.manage_users_view, name='manage_users'),
    path('users/<int:pk>/toggle/', views.toggle_user_active_view, name='toggle_user'),

    # Password Reset Flow
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='accounts/password_reset.html',
            email_template_name='accounts/password_reset_email.html',
            success_url='/accounts/password-reset/done/',
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(template_name='accounts/password_reset_done.html'),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='accounts/password_reset_confirm.html',
            success_url='/accounts/reset/done/',
        ),
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(template_name='accounts/password_reset_complete.html'),
        name='password_reset_complete',
    ),
]
