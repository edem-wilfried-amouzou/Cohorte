import json

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.groupes.audit import log_action
from apps.groupes.models import Classe
from apps.groupes.throttle import too_many, too_many_key

from .forms import DeleteAccountForm, LoginForm, ProfileForm, SignupForm


class ThrottledLoginView(auth_views.LoginView):
    """Connexion avec limite par IP et par nom d'utilisateur (contre le bourrage d'identifiants)."""

    template_name = "registration/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        username = (request.POST.get("username") or "").strip().lower()[:150]
        if too_many(request, "login", 15, 300) or (username and too_many_key(f"login-user:{username}", 8, 300)):
            form = self.get_form()
            form.add_error(None, "Trop de tentatives. Patiente quelques minutes puis réessaie.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        log_action(self.request, "connexion", user=form.get_user())
        return super().form_valid(form)


class ThrottledPasswordResetView(auth_views.PasswordResetView):
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def post(self, request, *args, **kwargs):
        if too_many(request, "password_reset", 5, 3600):
            return render(request, self.template_name, {
                "form": self.get_form(), "throttled": True,
            }, status=429)
        return super().post(request, *args, **kwargs)


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("groupes:dashboard")
    form = SignupForm(request.POST or None)
    if request.method == "POST":
        if too_many(request, "signup", 10, 3600):
            form.add_error(None, "Trop d'inscriptions depuis ce réseau. Réessaie plus tard.")
        elif form.is_valid():
            user = form.save()
            login(request, user)
            log_action(request, "inscription", user=user)
            return redirect("groupes:dashboard")
    return render(request, "accounts/signup.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user, prefix="p")
    delete_form = DeleteAccountForm(user=request.user, prefix="d")
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profil mis à jour.")
        return redirect("accounts:profile")
    owned = Classe.objects.filter(owner=request.user).count()
    return render(request, "accounts/profile.html", {"form": form, "delete_form": delete_form, "owned": owned})


@login_required
@require_POST
def delete_account(request):
    form = DeleteAccountForm(request.POST, user=request.user, prefix="d")
    if not form.is_valid():
        profile_form = ProfileForm(instance=request.user, prefix="p")
        owned = Classe.objects.filter(owner=request.user).count()
        return render(request, "accounts/profile.html", {"form": profile_form, "delete_form": form, "owned": owned}, status=400)
    user = request.user
    log_action(request, "suppression_compte", user=user, detail=f"{user.username}")
    logout(request)
    user.delete()
    messages.success(request, "Ton compte et tes classes ont été supprimés.")
    return redirect("landing")


@login_required
def export_data(request):
    """Export JSON de toutes les données du compte (classes dont il est propriétaire)."""
    classes = Classe.objects.filter(owner=request.user).prefetch_related("membres", "activites__groupes", "groupes")
    data = {
        "exporte_le": timezone.now().isoformat(),
        "utilisateur": {"nom": request.user.username, "email": request.user.email},
        "classes": [
            {
                "nom": c.name, "description": c.description,
                "membres": [
                    {"nom": m.full_name, "reference": m.reference, "sexe": m.sex, "champs": m.custom_data}
                    for m in c.membres.all()
                ],
                "activites": [
                    {"titre": a.title, "groupes": [g.label for g in a.groupes.all()]}
                    for a in c.activites.all()
                ],
            }
            for c in classes
        ],
    }
    response = HttpResponse(json.dumps(data, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="cohorte-mes-donnees.json"'
    return response
