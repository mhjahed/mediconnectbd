from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    priority = 0.8
    changefreq = 'weekly'

    def items(self):
        return [
            'core:home',
            'appointments:search',
            'accounts:login',
            'accounts:register_patient',
            'core:privacy',
            'core:terms',
            'core:refund_policy',
            'core:cookie_policy',
            'core:accessibility',
        ]

    def location(self, item):
        return reverse(item)
