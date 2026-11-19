from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    path('', views.notification_list_view, name='list'),
    path('live/', views.live_notifications_api, name='live_api'),
    path('<int:pk>/read/', views.mark_read_view, name='mark_read'),
    path('read-all/', views.mark_all_read_view, name='mark_all_read'),
    path('broadcast/', views.broadcast_notice_view, name='broadcast'),

    # Gmail SMTP Mailer Console (Overview, Compose, Recipients, History, In-Portal SMTP Settings, Live HTML Preview)
    path('mailer/', views.mailer_overview_view, name='mailer_overview'),
    path('mailer/compose/', views.mailer_compose_view, name='mailer_compose'),
    path('mailer/recipients/', views.mailer_recipients_view, name='mailer_recipients'),
    path('mailer/history/', views.mailer_history_view, name='mailer_history'),
    path('mailer/settings/', views.mailer_smtp_settings_view, name='mailer_settings'),
    path('mailer/preview/<str:template_key>/', views.mailer_preview_view, name='mailer_preview'),
]
