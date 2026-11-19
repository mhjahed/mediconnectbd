from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.home_view, name='home'),
    path('legal/privacy/', views.privacy_view, name='privacy'),
    path('legal/terms/', views.terms_view, name='terms'),
    path('legal/refunds/', views.refund_policy_view, name='refund_policy'),
    path('legal/cookies/', views.cookie_policy_view, name='cookie_policy'),
    path('legal/accessibility/', views.accessibility_view, name='accessibility'),
    path('pulse/', views.telemetry_pulse_view, name='telemetry_pulse'),
]
