from django.urls import path
from . import views

app_name = 'appointments'

urlpatterns = [
    path('search/', views.doctor_search_view, name='search'),
    path('specialist/<int:doctor_id>/', views.specialist_detail_view, name='specialist_detail'),
    path('api/slots/<int:doctor_id>/', views.slots_api_view, name='slots_api'),
    path('book/<int:doctor_id>/', views.book_appointment_view, name='book'),
    path('my/', views.patient_appointments_view, name='patient_list'),
    path('doctor/', views.doctor_appointments_view, name='doctor_list'),
    path('<int:pk>/', views.appointment_detail_view, name='detail'),
    path('<int:pk>/meet-link/', views.set_meet_link_view, name='set_meet_link'),
    path('<int:pk>/status/', views.update_appointment_status_view, name='update_status'),
    path('<int:pk>/cancel/', views.cancel_appointment_view, name='cancel'),
    path('<int:pk>/review/', views.submit_review_view, name='submit_review'),
    path('availability/', views.manage_availability_view, name='availability'),
    path('availability/<int:pk>/delete/', views.delete_availability_view, name='delete_availability'),
]
