from django.contrib import admin
from .models import Notification, EmailDispatchLog, MailerSMTPConfig


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('id', 'recipient', 'notification_type', 'title', 'is_read', 'email_sent', 'created_at')
    list_filter = ('notification_type', 'is_read', 'email_sent')
    search_fields = ('title', 'message', 'recipient__username', 'recipient__email')


@admin.register(EmailDispatchLog)
class EmailDispatchLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'recipient_email', 'template_key', 'subject', 'reference_code', 'status', 'created_at')
    list_filter = ('template_key', 'status')
    search_fields = ('recipient_email', 'recipient_name', 'subject', 'reference_code')


@admin.register(MailerSMTPConfig)
class MailerSMTPConfigAdmin(admin.ModelAdmin):
    list_display = ('id', 'gmail_address', 'smtp_host', 'smtp_port', 'is_enabled', 'updated_at')
