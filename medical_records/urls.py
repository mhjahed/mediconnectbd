from django.urls import path
from . import views

app_name = 'medical_records'

urlpatterns = [
    path('', views.record_list_view, name='list'),
    path('author/<int:appointment_id>/', views.create_or_edit_record_view, name='author'),
    path('<int:pk>/', views.record_detail_view, name='detail'),
    path('<int:pk>/print/', views.print_prescription_view, name='print_rx'),
]
