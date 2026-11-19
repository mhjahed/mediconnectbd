from django.urls import path
from . import views

app_name = 'payments'

urlpatterns = [
    path('submit/<int:appointment_id>/', views.submit_payment_view, name='submit'),
    path('my/', views.patient_payments_view, name='patient_list'),
    path('queue/', views.admin_payment_queue_view, name='admin_queue'),
    path('<int:pk>/verify/', views.verify_payment_action_view, name='verify_action'),
    path('<int:pk>/receipt/', views.payment_receipt_view, name='receipt'),
]
