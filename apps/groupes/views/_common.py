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


def _classe(request, pk, *, write=False, owner=False, full=False) -> Classe:
    classe = get_object_or_404(Classe, pk=pk)
    if classe.owner_id == request.user.pk:
        request.class_role = "owner"
        return classe
    collaborator = Collaborateur.objects.filter(classe=classe, user=request.user).first()
    if collaborator is None or owner:
        raise Http404
    if write and collaborator.role == Collaborateur.Role.VIEWER:
        raise Http404
    if full and collaborator.role != Collaborateur.Role.CO_DELEGATE:
        raise Http404
    request.class_role = collaborator.role
    return classe


def _activite(request, pk, apk, *, write=False, owner=False, full=False) -> Activite:
    return get_object_or_404(Activite, pk=apk, classe=_classe(request, pk, write=write, owner=owner, full=full))


def _can_delete(request) -> bool:
    """Propriétaire et co-délégué peuvent supprimer et importer ; le secrétaire et le lecteur non."""
    return request.class_role in ("owner", Collaborateur.Role.CO_DELEGATE)


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _groupes_with_size(classe):
    groupes = list(classe.groupes.filter(activite__isnull=True).annotate(size=Count("membres")))
    for g in groupes:
        g.full = g.capacity is not None and g.size >= g.capacity
    return groupes


def _stats(classe) -> dict:
    return classe.membres.aggregate(
        total=Count("pk"),
        affectes=Count("pk", filter=Q(groupe__isnull=False)),
        declares=Count("pk", filter=Q(groupe__isnull=True, responded_at__isnull=False)),
        attente=Count("pk", filter=Q(groupe__isnull=True, responded_at__isnull=True)),
        a_verifier=Count("pk", filter=Q(verified=False)),
    )


def _assign(member: Membre, groupe_id) -> tuple[bool, str]:
    """Affecte (ou retire) une personne en respectant la capacité du groupe."""
    now = timezone.now()
    if not groupe_id:
        member.groupe = None
        member.save(update_fields=["groupe"])
        return True, ""
    with transaction.atomic():
        groupe = Groupe.objects.select_for_update().filter(
            pk=groupe_id, classe_id=member.classe_id, activite__isnull=True,
        ).first()
        if groupe is None:
            return False, "Groupe introuvable."
        if groupe.pk != member.groupe_id and groupe.capacity is not None and groupe.membres.count() >= groupe.capacity:
            return False, f"{groupe.short_label} est complet."
        member.groupe = groupe
        member.responded_at = member.responded_at or now
        member.save(update_fields=["groupe", "responded_at"])
    return True, ""


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


