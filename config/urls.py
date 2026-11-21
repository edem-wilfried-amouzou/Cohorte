from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("aide/", TemplateView.as_view(template_name="help.html"), name="help"),
    path("confidentialite/", TemplateView.as_view(template_name="privacy.html"), name="privacy"),
    path("conditions/", TemplateView.as_view(template_name="terms.html"), name="terms"),
    path("hors-ligne/", TemplateView.as_view(template_name="offline.html"), name="offline"),
    path("sante/", views.health, name="health"),
    path("console/", include("config.admin_urls")),
    path("sw.js", views.service_worker, name="service_worker"),
    path("compte/", include("apps.accounts.urls")),
    path("", include("apps.groupes.urls")),
    path(settings.ADMIN_URL, admin.site.urls),
]
