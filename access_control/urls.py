from django.urls import path
from . import views

app_name = 'access_control'

urlpatterns = [
    path('scanner/', views.scanner_terminal_view, name='scanner'),
    path('scan/', views.verify_scan_api, name='verify_scan_api'),
    path('patient-entry/', views.patient_entry_desk_view, name='patient_desk'),
    path('patient-entry/verify/', views.verify_patient_qr_api, name='verify_patient_qr'),
    path('my-card/', views.my_id_card_view, name='my_card'),
    path('cards/<int:pk>/print/', views.print_badge_view, name='print_badge'),
    path('cards/', views.manage_cards_view, name='manage_cards'),
    path('cards/<int:pk>/edit/', views.edit_card_view, name='edit_card'),
    path('logs/', views.audit_logs_view, name='audit_logs'),
]
