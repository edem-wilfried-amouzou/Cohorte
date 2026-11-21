from django.urls import path

from . import admin_views

app_name = "console"

urlpatterns = [
    path("", admin_views.home, name="home"),
    path("utilisateurs/", admin_views.users, name="users"),
    path("journaux/", admin_views.logs, name="logs"),
    path("retours/", admin_views.feedbacks, name="feedbacks"),
    path("retours/<int:pk>/", admin_views.feedback_detail, name="feedback_detail"),
    path("documentation/", admin_views.technical, name="technical"),
]
