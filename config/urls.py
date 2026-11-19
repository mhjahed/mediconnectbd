from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.views.generic import RedirectView
from core.sitemaps import StaticViewSitemap
from core import views as core_views

sitemaps = {
    'static': StaticViewSitemap,
}

urlpatterns = [
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    path('', include('core.urls')),
    path('accounts/', include('accounts.urls')),
    path('dashboard/', include('dashboard.urls')),
    path('appointments/', include('appointments.urls')),
    path('payments/', include('payments.urls')),
    path('records/', include('medical_records.urls')),
    path('notifications/', include('notifications.urls')),
    path('access/', include('access_control.urls')),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', core_views.robots_txt, name='robots_txt'),
    path('llms.txt', core_views.llms_txt, name='llms_txt'),
    path('favicon.ico', RedirectView.as_view(url='/static/images/favicon.ico', permanent=True)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler403 = 'core.views.error_403'
handler404 = 'core.views.error_404'
handler500 = 'core.views.error_500'

admin.site.site_header = 'MediConnect Clinical Administration'
admin.site.site_title = 'MediConnect Admin'
admin.site.index_title = 'Hospital & Telemedicine Registry'
