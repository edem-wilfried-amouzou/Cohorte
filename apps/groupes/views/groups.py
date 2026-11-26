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
from ..audit import log_action, snapshot_fields
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


# --- Groupes -------------------------------------------------------------------
@login_required
def groups(request, pk):
    _classe(request, pk)
    return redirect("groupes:activities", pk=pk)


@login_required
@require_POST
def group_create(request, pk):
    classe = _classe(request, pk, write=True)
    form = GroupeForm(request.POST, classe=classe)
    if form.is_valid():
        group = form.save(commit=False)
        group.classe = classe
        group.activite = None
        group.save()
        log_action(request, "groupe_cree", classe=classe, target=group, detail=group.label,
                   after=snapshot_fields(group, "number", "name", "capacity", "theme", "presentation_order", "leader"))
        messages.success(request, f"{group.short_label} créé.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("groupes:groups", pk=classe.pk)


@login_required
@require_POST
def groups_bulk(request, pk):
    classe = _classe(request, pk, write=True)
    form = BulkGroupsForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Indique un nombre de groupes entre 1 et 100.")
        return redirect("groupes:groups", pk=classe.pk)
    existing = set(classe.groupes.filter(activite__isnull=True).values_list("number", flat=True))
    capacity = form.cleaned_data["capacity"]
    new = [
        Groupe(classe=classe, activite=None, number=n, capacity=capacity)
        for n in range(1, form.cleaned_data["count"] + 1) if n not in existing
    ]
    Groupe.objects.bulk_create(new)
    log_action(request, "groupes_crees", classe=classe, target=classe,
               detail=f"{len(new)} groupe(s)", after={"numeros": [group.number for group in new]})
    messages.success(request, f"{len(new)} groupe(s) créé(s).")
    return redirect("groupes:groups", pk=classe.pk)


@login_required
@require_http_methods(["GET", "POST"])
def group_edit(request, pk, gpk):
    classe = _classe(request, pk, write=request.method == "POST")
    group = get_object_or_404(Groupe, pk=gpk, classe=classe, activite__isnull=True)
    form = GroupeForm(request.POST or None, instance=group, classe=classe)
    if request.method == "POST" and form.is_valid():
        before = snapshot_fields(group, "number", "name", "capacity", "theme", "presentation_order", "leader")
        form.save()
        log_action(request, "groupe_modifie", classe=classe, target=group, detail=group.label,
                   before=before, after=snapshot_fields(group, "number", "name", "capacity", "theme", "presentation_order", "leader"))
        messages.success(request, "Groupe modifié.")
        return redirect("groupes:groups", pk=classe.pk)
    return render(request, "groupes/group_form.html", {"form": form, "classe": classe, "group": group, "tab": "groups"})


@login_required
@require_POST
def group_delete(request, pk, gpk):
    classe = _classe(request, pk, write=True)
    group = get_object_or_404(Groupe, pk=gpk, classe=classe, activite__isnull=True)
    log_action(request, "groupe_supprime", classe=classe, target=group, detail=group.label,
               before=snapshot_fields(group, "number", "name", "capacity", "theme", "presentation_order", "leader"))
    group.delete()
    messages.success(request, "Groupe supprimé. Ses membres sont maintenant sans groupe.")
    return redirect("groupes:groups", pk=classe.pk)


