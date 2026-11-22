# ruff: noqa: F401
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ..forms import (
    PublicListForm,
    ActiviteForm,
    BulkGroupsForm,
    ChampMembreForm,
    ClasseForm,
    CollaborateurForm,
    ExternalRequestForm,
    GroupeForm,
    ImportForm,
    MembreForm,
)
from ..models import (
    Activite, ChampMembre, Classe, Collaborateur, DemandeMembre, Groupe, Membre, Participation,
    compose_full_name, normalize,
)
from ..services import importing
from ..services.allocation import auto_assign
from ..services.exporting import EXPORTERS, collect, collect_activity, filename
from ..services.qr import qr_svg
from ..throttle import too_many
from ..audit import log_action
from .public import _public_classe
from ._common import (
    _classe,
    _activite,
    _can_delete,
    _is_htmx,
    _groupes_with_size,
    _stats,
    _assign,
    _int_or_none,
)


# --- Champs et demandes ------------------------------------------------------
@login_required
@require_http_methods(["GET", "POST"])
def member_fields(request, pk):
    classe = _classe(request, pk, write=True)
    if request.method == "POST":
        if "delete_field" in request.POST:
            field = get_object_or_404(ChampMembre, pk=_int_or_none(request.POST.get("delete_field")), classe=classe)
            field.delete()
            messages.success(request, "Champ supprimé.")
            return redirect("groupes:member_fields", pk=classe.pk)
        field_id = _int_or_none(request.POST.get("field_id"))
        form = ChampMembreForm(request.POST, instance=get_object_or_404(ChampMembre, pk=field_id, classe=classe) if field_id else None)
        if form.is_valid():
            field = form.save(commit=False)
            field.classe = classe
            field.save()
            messages.success(request, "Champ enregistré." if field_id else "Nouveau champ ajouté.")
            return redirect("groupes:member_fields", pk=classe.pk)
    else:
        form = ChampMembreForm()
    return render(request, "groupes/member_fields.html", {
        "classe": classe,
        "form": form,
        "fields": classe.champs_membres.order_by("position", "id"),
        "can_edit": True,
        "tab": "members",
    })


@login_required
@require_POST
def demande_review(request, pk, dpk):
    classe = _classe(request, pk, write=True)
    demande = get_object_or_404(DemandeMembre, pk=dpk, classe=classe)
    action = request.POST.get("action")
    if action == "accept":
        if Membre.objects.filter(classe=classe).by_name(demande.full_name).exists():
            messages.error(request, "Cette personne est déjà présente dans la liste.")
            return redirect("groupes:members", pk=classe.pk)
        Membre.objects.create(
            classe=classe,
            full_name=demande.full_name,
            sex=demande.sex,
            reference=demande.reference,
            custom_data=demande.custom_data,
            source=Membre.Source.LINK,
            verified=True,
        )
        demande.status = DemandeMembre.Status.ACCEPTED
        demande.reviewed_at = timezone.now()
        demande.reviewed_by = request.user
        demande.save(update_fields=["status", "reviewed_at", "reviewed_by"])
        messages.success(request, f"{demande.full_name} ajouté à la liste.")
    elif action == "reject":
        demande.status = DemandeMembre.Status.REJECTED
        demande.reviewed_at = timezone.now()
        demande.reviewed_by = request.user
        demande.save(update_fields=["status", "reviewed_at", "reviewed_by"])
        messages.success(request, f"Demande de {demande.full_name} refusée.")
    else:
        messages.error(request, "Action inconnue.")
    return redirect("groupes:members", pk=classe.pk)


@require_http_methods(["GET", "POST"])
def external_join(request, token):
    classe = _public_classe(token)
    if not classe.accepting or not classe.external_intake_open:
        return render(request, "public/closed.html", {"classe": classe}, status=200)
    form = ExternalRequestForm(classe=classe, data=request.POST or None)
    if request.method == "POST":
        if request.POST.get("website"):
            return redirect("groupes:external_done", token=classe.token)
    if request.method == "POST" and form.is_valid():
        custom_data = {}
        for field in classe.champs_membres.filter(public=True).order_by("position", "id"):
            value = form.cleaned_data.get(field.key)
            if value not in (None, ""):
                custom_data[field.key] = value
        for key, value in request.POST.items():
            if key in {"full_name", "sex", "reference", "website", "csrfmiddlewaretoken"}:
                continue
            if not value or key.startswith("custom_") is False and key not in {field.key for field in classe.champs_membres.filter(public=True)}:
                continue
            normalized_key = key[7:] if key.startswith("custom_") else key
            if normalized_key and value.strip():
                custom_data[normalized_key] = value.strip()
        demande = DemandeMembre.objects.create(
            classe=classe,
            full_name=form.cleaned_data["full_name"],
            sex=form.cleaned_data.get("sex", ""),
            reference=form.cleaned_data.get("reference", ""),
            custom_data=custom_data,
        )
        request.session[f"external_joined:{classe.token}"] = {"name": demande.full_name, "pending": True}
        return redirect("groupes:external_done", token=classe.token)
    return render(request, "public/external_join.html", {"classe": classe, "form": form})


@require_GET
def external_done(request, token):
    classe = _public_classe(token)
    return render(request, "public/external_done.html", {
        "classe": classe,
        "result": request.session.get(f"external_joined:{classe.token}"),
    })


