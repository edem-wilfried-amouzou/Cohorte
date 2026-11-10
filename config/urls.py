from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("", TemplateView.as_view(template_name="landing.html"), name="landing"),
    path("compte/", include("apps.accounts.urls")),
    path("", include("apps.groupes.urls")),
    path("admin/", admin.site.urls),
]
