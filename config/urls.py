from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

from . import views

urlpatterns = [
    path("", TemplateView.as_view(template_name="landing.html"), name="landing"),
    path("confidentialite/", TemplateView.as_view(template_name="privacy.html"), name="privacy"),
    path("hors-ligne/", TemplateView.as_view(template_name="offline.html"), name="offline"),
    path("sante/", views.health, name="health"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("compte/", include("apps.accounts.urls")),
    path("", include("apps.groupes.urls")),
    path(settings.ADMIN_URL, admin.site.urls),
]
