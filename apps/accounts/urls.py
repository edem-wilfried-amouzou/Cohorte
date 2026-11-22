from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"

urlpatterns = [
    path("connexion/", views.ThrottledLoginView.as_view(), name="login"),
    path("deconnexion/", auth_views.LogoutView.as_view(), name="logout"),
    path("inscription/", views.signup, name="signup"),
    path("profil/", views.profile, name="profile"),
    path("profil/supprimer/", views.delete_account, name="delete_account"),
    path("profil/export/", views.export_data, name="export_data"),
    path("mot-de-passe/oublie/", views.ThrottledPasswordResetView.as_view(), name="password_reset"),
    path("mot-de-passe/envoye/", auth_views.PasswordResetDoneView.as_view(template_name="registration/password_reset_done.html"), name="password_reset_done"),
    path(
        "mot-de-passe/reinitialiser/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path("mot-de-passe/termine/", auth_views.PasswordResetCompleteView.as_view(template_name="registration/password_reset_complete.html"), name="password_reset_complete"),
]
