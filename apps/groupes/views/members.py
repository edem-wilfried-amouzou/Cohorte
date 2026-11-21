# ruff: noqa: F401
from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.db.models.functions import Lower
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


# --- Membres -------------------------------------------------------------------
@login_required
@require_http_methods(["GET", "POST"])
def members(request, pk):
    classe = _classe(request, pk, write=request.method == "POST")
    add_form = MembreForm(classe=classe)
    pending_requests = classe.demandes_membres.filter(status=DemandeMembre.Status.PENDING).order_by("-submitted_at")
    if request.method == "POST":
        action = request.POST.get("bulk_action")
        if action:
            selected = classe.membres.filter(pk__in=request.POST.getlist("member_ids"))
            count = selected.count()
            if not count:
                messages.error(request, "Sélectionne au moins une personne.")
                return redirect("groupes:members", pk=classe.pk)
            if action == "delete" and not _can_delete(request):
                raise Http404
            if action == "verify":
                selected.update(verified=True)
                messages.success(request, f"{count} personne(s) validée(s).")
            elif action == "delete":
                log_action(request, "membres_supprimes", classe=classe, detail=f"{count} personne(s)")
                selected.delete()
                messages.success(request, f"{count} personne(s) retirée(s) de la classe.")
            elif action == "assign":
                group = get_object_or_404(Groupe, pk=_int_or_none(request.POST.get("group_id")), classe=classe, activite__isnull=False)
                existing = {
                    item.membre_id: item
                    for item in Participation.objects.filter(activite=group.activite, membre__in=selected)
                }
                already_in_group = Participation.objects.filter(activite=group.activite, groupe=group).count()
                moving_into_group = sum(item.groupe_id != group.pk for item in existing.values())
                new_participants = count - len(existing)
                if group.capacity is not None and already_in_group + moving_into_group + new_participants > group.capacity:
                    messages.error(request, f"La sélection dépasse la capacité de {group.short_label}.")
                    return redirect("groupes:members", pk=classe.pk)
                now = timezone.now()
                for member in selected:
                    Participation.objects.update_or_create(
                        activite=group.activite, membre=member,
                        defaults={"groupe": group, "responded_at": now},
                    )
                messages.success(request, f"{count} personne(s) affectée(s) à {group.activite.title} · {group.label}.")
            elif action == "clear_activity":
                activity = get_object_or_404(Activite, pk=_int_or_none(request.POST.get("activity_id")), classe=classe)
                Participation.objects.filter(activite=activity, membre__in=selected).update(groupe=None, responded_at=timezone.now())
                messages.success(request, f"Affectations retirées pour {count} personne(s) dans « {activity.title} ».")
            else:
                messages.error(request, "Action groupée inconnue.")
            return redirect("groupes:members", pk=classe.pk)
        add_form = MembreForm(request.POST, classe=classe)
        if add_form.is_valid():
            member = add_form.save(commit=False)
            member.classe, member.source = classe, Membre.Source.MANUAL
            if member.groupe_id:
                member.responded_at = timezone.now()
            member.custom_data = {**member.custom_data, **add_form.extra_custom_data}
            member.save()
            messages.success(request, f"{member.full_name} ajouté.")
            return redirect("groupes:members", pk=classe.pk)

    flt, q = request.GET.get("f", ""), request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "alpha")
    qs = classe.membres.search(q)
    if flt == "verifier":
        qs = qs.filter(verified=False)
    if sort == "list":
        qs = qs.order_by(F("number").asc(nulls_last=True), "last_name", "first_name", "search_name")
    else:
        sort = "alpha"
        qs = qs.order_by(Lower("last_name"), Lower("first_name"), "search_name")
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    return render(request, "groupes/members.html", {
        "classe": classe, "page": page, "add_form": add_form, "form": ImportForm(),
        "custom_fields": classe.champs_membres.all(),
        "f": flt, "q": q, "sort": sort,
        "stats": {"total": classe.membres.count(), "a_verifier": classe.membres.filter(verified=False).count()},
        "can_edit": request.class_role != Collaborateur.Role.VIEWER,
        "can_delete": _can_delete(request),
        "activities": classe.activites.prefetch_related("groupes"),
        "pending_requests": pending_requests,
        "tab": "members",
    })


@login_required
@require_http_methods(["GET", "POST"])
def member_edit(request, pk, mpk):
    classe = _classe(request, pk, write=request.method == "POST")
    member = get_object_or_404(Membre, pk=mpk, classe=classe)
    form = MembreForm(request.POST or None, instance=member, classe=classe)
    if request.method == "POST" and form.is_valid():
        member = form.save(commit=False)
        if member.groupe_id and not member.responded_at:
            member.responded_at = timezone.now()
        member.custom_data = {**member.custom_data, **form.extra_custom_data}
        member.save()
        messages.success(request, "Modifications enregistrées.")
        return redirect("groupes:members", pk=classe.pk)
    return render(request, "groupes/member_form.html", {
        "form": form, "classe": classe, "member": member,
        "can_edit": request.class_role != Collaborateur.Role.VIEWER, "tab": "members",
    })


@login_required
@require_POST
def member_delete(request, pk, mpk):
    classe = _classe(request, pk, write=True, full=True)
    member = get_object_or_404(Membre, pk=mpk, classe=classe)
    log_action(request, "membre_supprime", classe=classe, detail=member.full_name)
    member.delete()
    if _is_htmx(request):
        return HttpResponse("")
    messages.success(request, "Personne retirée de la liste.")
    return redirect("groupes:members", pk=classe.pk)


@login_required
@require_POST
def member_verify(request, pk, mpk):
    classe = _classe(request, pk, write=True)
    member = get_object_or_404(Membre.objects.select_related("groupe"), pk=mpk, classe=classe)
    member.verified = True
    member.save(update_fields=["verified"])
    return render(request, "groupes/_member_row.html", {
        "m": member, "classe": classe, "can_edit": True, "can_delete": _can_delete(request),
    })


@login_required
@require_POST
def member_assign(request, pk, mpk):
    """Affectation depuis la liste (HTMX → ligne HTML) ou le tableau (glisser-déposer → JSON)."""
    classe = _classe(request, pk, write=True)
    member = get_object_or_404(Membre.objects.select_related("groupe"), pk=mpk, classe=classe)
    ok, error = _assign(member, _int_or_none(request.POST.get("groupe")))
    if _is_htmx(request):
        member.refresh_from_db()
        return render(request, "groupes/_member_row.html", {
            "m": member, "classe": classe, "can_edit": True, "error": error,
        })
    if not ok:
        return JsonResponse({"ok": False, "error": error}, status=409)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def auto_assign_view(request, pk):
    _classe(request, pk)
    return redirect("groupes:activities", pk=pk)


