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

from .forms import (
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
from .models import (
    Activite, ChampMembre, Classe, Collaborateur, DemandeMembre, Groupe, Membre, Participation,
    compose_full_name, normalize,
)
from .services import importing
from .services.allocation import auto_assign
from .services.exporting import EXPORTERS, collect, collect_activity, filename
from .services.qr import qr_svg
from .throttle import too_many


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


# --- Tableau de bord ----------------------------------------------------------
@login_required
def dashboard(request):
    classes = list(
        Classe.objects.filter(Q(owner=request.user) | Q(collaborateurs__user=request.user)).annotate(
            n_membres=Count("membres", distinct=True),
            n_activites=Count("activites", distinct=True),
        ).distinct()
    )
    roles = {
        item.classe_id: item.role
        for item in Collaborateur.objects.filter(user=request.user, classe__in=classes)
    }
    for classe in classes:
        classe.access_role = "owner" if classe.owner_id == request.user.pk else roles.get(classe.pk, "viewer")
    return render(request, "groupes/dashboard.html", {"classes": classes})


@login_required
@require_http_methods(["GET", "POST"])
def classe_create(request):
    form = ClasseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        classe = form.save(commit=False)
        classe.owner = request.user
        classe.save()
        messages.success(request, "Classe créée. Ajoute maintenant ta liste de personnes.")
        return redirect("groupes:import", pk=classe.pk)
    return render(request, "groupes/classe_form.html", {"form": form, "creating": True})


@login_required
def classe_detail(request, pk):
    classe = _classe(request, pk)
    return render(request, "groupes/classe_detail.html", {
        "classe": classe,
        "stats": {"total": classe.membres.count(), "a_verifier": classe.membres.filter(verified=False).count()},
        "activites": classe.activites.annotate(
            n_groupes=Count("groupes", distinct=True),
            n_reponses=Count("participations", filter=Q(participations__responded_at__isnull=False), distinct=True),
        ),
        "can_edit": request.class_role != Collaborateur.Role.VIEWER,
        "is_owner": request.class_role == "owner",
        "role_label": dict(Collaborateur.Role.choices).get(request.class_role, "Propriétaire"),
        "tab": "overview",
    })


@login_required
@require_http_methods(["GET", "POST"])
def collaborators(request, pk):
    classe = _classe(request, pk, owner=True)
    form = CollaborateurForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.user
        if user.pk == request.user.pk:
            form.add_error("account", "Tu es déjà propriétaire de cette classe.")
        else:
            Collaborateur.objects.update_or_create(
                classe=classe, user=user, defaults={"role": form.cleaned_data["role"]},
            )
            messages.success(request, f"Accès mis à jour pour {user.get_full_name() or user.username}.")
            return redirect("groupes:collaborators", pk=classe.pk)
    return render(request, "groupes/collaborators.html", {
        "classe": classe, "form": form,
        "collaborateurs": classe.collaborateurs.select_related("user"),
        "tab": "team",
    })


@login_required
@require_POST
def collaborator_remove(request, pk, cpk):
    classe = _classe(request, pk, owner=True)
    collaborator = get_object_or_404(Collaborateur, pk=cpk, classe=classe)
    collaborator.delete()
    messages.success(request, "Accès du collaborateur retiré.")
    return redirect("groupes:collaborators", pk=classe.pk)


@login_required
def activities(request, pk):
    classe = _classe(request, pk)
    data = list(classe.activites.annotate(
        n_groupes=Count("groupes", distinct=True),
        n_affectes=Count("participations", filter=Q(participations__groupe__isnull=False), distinct=True),
        n_reponses=Count("participations", filter=Q(participations__responded_at__isnull=False), distinct=True),
    ))
    return render(request, "groupes/activities.html", {
        "classe": classe, "activites": data, "form": ActiviteForm(),
        "can_edit": request.class_role != Collaborateur.Role.VIEWER, "tab": "activities",
    })


@login_required
@require_POST
def activity_create(request, pk):
    classe = _classe(request, pk, write=True)
    form = ActiviteForm(request.POST)
    if form.is_valid():
        activity = form.save(commit=False)
        activity.classe = classe
        activity.save()
        messages.success(request, "Activité créée. Tu peux maintenant créer ses groupes.")
        return redirect("groupes:activity_detail", pk=classe.pk, apk=activity.pk)
    return render(request, "groupes/activities.html", {
        "classe": classe, "activites": classe.activites.all(), "form": form,
        "can_edit": True, "tab": "activities",
    })


@login_required
def activity_detail(request, pk, apk):
    classe = _classe(request, pk)
    activity = _activite(request, pk, apk)
    groups = list(activity.groupes.annotate(size=Count("participations")))
    participation_by_member = {
        item.membre_id: item
        for item in activity.participations.select_related("membre", "groupe")
    }
    participants = []
    for member in classe.membres.order_by("search_name"):
        participation = participation_by_member.get(member.pk)
        participants.append(SimpleNamespace(
            membre=member,
            groupe=participation.groupe if participation else None,
            groupe_id=participation.groupe_id if participation else None,
            status_label=participation.status_label if participation else "Pas encore répondu",
        ))
    answered = activity.participations.filter(responded_at__isnull=False)
    stats = {
        "total": classe.membres.count(),
        "affectes": answered.filter(groupe__isnull=False).count(),
        "declares": answered.filter(groupe__isnull=True).count(),
        "attente": classe.membres.exclude(participations__activite=activity, participations__responded_at__isnull=False).distinct().count(),
    }
    return render(request, "groupes/activity_detail.html", {
        "classe": classe, "activite": activity, "groupes": groups, "participants": participants,
        "membres": classe.membres.order_by("search_name"), "group_form": GroupeForm(classe=classe, activite=activity),
        "bulk": BulkGroupsForm(), "form": ActiviteForm(instance=activity), "stats": stats,
        "can_edit": request.class_role != Collaborateur.Role.VIEWER,
        "public_url": request.build_absolute_uri(reverse("groupes:activity_join", args=[activity.token])),
        "view_url": request.build_absolute_uri(reverse("groupes:activity_public_view", args=[activity.view_token])),
        "tab": "activities",
    })


@login_required
def activity_qr(request, pk, apk):
    activity = _activite(request, pk, apk)
    url = request.build_absolute_uri(reverse("groupes:activity_join", args=[activity.token]))
    return HttpResponse(qr_svg(url), content_type="image/svg+xml")


@login_required
@require_POST
def activity_group_create(request, pk, apk):
    activity = _activite(request, pk, apk, write=True)
    form = GroupeForm(request.POST, classe=activity.classe, activite=activity)
    if form.is_valid():
        group = form.save(commit=False)
        group.classe, group.activite = activity.classe, activity
        group.save()
        messages.success(request, f"{group.short_label} créé.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_group_edit(request, pk, apk, gpk):
    activity = _activite(request, pk, apk, write=True)
    group = get_object_or_404(Groupe, pk=gpk, activite=activity)
    form = GroupeForm(request.POST, instance=group, classe=activity.classe, activite=activity)
    if form.is_valid():
        form.save()
        messages.success(request, f"{group.short_label} modifié.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_group_delete(request, pk, apk, gpk):
    activity = _activite(request, pk, apk, write=True)
    group = get_object_or_404(Groupe, pk=gpk, activite=activity)
    group.delete()
    messages.success(request, "Groupe supprimé. Les membres restent dans la classe et passent sans groupe pour cette activité.")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_groups_bulk(request, pk, apk):
    activity = _activite(request, pk, apk, write=True)
    form = BulkGroupsForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Indique un nombre de groupes entre 1 et 100.")
        return redirect("groupes:activity_detail", pk=pk, apk=apk)
    existing = set(activity.groupes.values_list("number", flat=True))
    groups = [
        Groupe(classe=activity.classe, activite=activity, number=number, capacity=form.cleaned_data["capacity"])
        for number in range(1, form.cleaned_data["count"] + 1) if number not in existing
    ]
    Groupe.objects.bulk_create(groups)
    messages.success(request, f"{len(groups)} groupe(s) créé(s).")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_assign(request, pk, apk):
    activity = _activite(request, pk, apk, write=True)
    member = get_object_or_404(Membre, pk=_int_or_none(request.POST.get("membre")), classe=activity.classe)
    group_id = _int_or_none(request.POST.get("groupe"))
    group = None
    if group_id:
        group = get_object_or_404(Groupe, pk=group_id, activite=activity)
        current = Participation.objects.filter(activite=activity, groupe=group).count()
        existing = Participation.objects.filter(activite=activity, membre=member).first()
        if group.capacity is not None and current >= group.capacity and (existing is None or existing.groupe_id != group.pk):
            messages.error(request, f"{group.short_label} est complet.")
            return redirect("groupes:activity_detail", pk=pk, apk=apk)
    Participation.objects.update_or_create(
        activite=activity, membre=member,
        defaults={"groupe": group, "responded_at": timezone.now()},
    )
    messages.success(request, f"Affectation de {member.full_name} mise à jour.")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_regenerate(request, pk, apk):
    activity = _activite(request, pk, apk, write=True, full=True)
    activity.regenerate_token()
    messages.success(request, "Nouveau lien créé. L'ancien ne fonctionne plus.")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_view_regenerate(request, pk, apk):
    activity = _activite(request, pk, apk, write=True, full=True)
    activity.regenerate_view_token()
    messages.success(request, "Nouveau lien de consultation créé. L’ancien lien est révoqué.")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_update(request, pk, apk):
    activity = _activite(request, pk, apk, write=True)
    form = ActiviteForm(request.POST, instance=activity)
    if form.is_valid():
        form.save()
        messages.success(request, "Réglages de l’activité enregistrés.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_delete(request, pk, apk):
    activity = _activite(request, pk, apk, write=True, full=True)
    activity.delete()
    messages.success(request, "Activité supprimée.")
    return redirect("groupes:activities", pk=pk)


@login_required
@require_http_methods(["GET", "POST"])
def classe_settings(request, pk):
    classe = _classe(request, pk, owner=request.method == "POST")
    form = ClasseForm(request.POST or None, instance=classe)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Réglages enregistrés.")
        return redirect("groupes:settings", pk=classe.pk)
    return render(request, "groupes/classe_form.html", {
        "form": form, "classe": classe, "is_owner": request.class_role == "owner", "tab": "settings",
    })


@login_required
@require_POST
def classe_regenerate(request, pk):
    classe = _classe(request, pk, owner=True)
    classe.regenerate_token()
    messages.success(request, "Nouveau lien créé. L'ancien ne fonctionne plus.")
    return redirect("groupes:detail", pk=classe.pk)


@login_required
@require_POST
def classe_delete(request, pk):
    classe = _classe(request, pk, owner=True)
    classe.delete()
    messages.success(request, "Classe supprimée.")
    return redirect("groupes:dashboard")


@login_required
@require_GET
def classe_qr(request, pk):
    classe = _classe(request, pk)
    url = request.build_absolute_uri(reverse("groupes:join", args=[classe.token]))
    return HttpResponse(qr_svg(url), content_type="image/svg+xml")


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
    qs = classe.membres.search(q)
    if flt == "verifier":
        qs = qs.filter(verified=False)
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    return render(request, "groupes/members.html", {
        "classe": classe, "page": page, "add_form": add_form, "form": ImportForm(),
        "custom_fields": classe.champs_membres.all(),
        "f": flt, "q": q,
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
    get_object_or_404(Membre, pk=mpk, classe=classe).delete()
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
    messages.success(request, f"{len(new)} groupe(s) créé(s).")
    return redirect("groupes:groups", pk=classe.pk)


@login_required
@require_http_methods(["GET", "POST"])
def group_edit(request, pk, gpk):
    classe = _classe(request, pk, write=request.method == "POST")
    group = get_object_or_404(Groupe, pk=gpk, classe=classe, activite__isnull=True)
    form = GroupeForm(request.POST or None, instance=group, classe=classe)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Groupe modifié.")
        return redirect("groupes:groups", pk=classe.pk)
    return render(request, "groupes/group_form.html", {"form": form, "classe": classe, "group": group, "tab": "groups"})


@login_required
@require_POST
def group_delete(request, pk, gpk):
    classe = _classe(request, pk, write=True)
    group = get_object_or_404(Groupe, pk=gpk, classe=classe, activite__isnull=True)
    group.delete()
    messages.success(request, "Groupe supprimé. Ses membres sont maintenant sans groupe.")
    return redirect("groupes:groups", pk=classe.pk)


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


# --- Import / export -----------------------------------------------------------
@login_required
@require_http_methods(["GET", "POST"])
def import_view(request, pk):
    classe = _classe(request, pk, write=request.method == "POST", full=request.method == "POST")
    form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            upload = form.cleaned_data.get("file")
            rows = importing.parse_upload(upload) if upload else importing.parse_text(form.cleaned_data["text"])
            if not rows:
                raise importing.ImportError_("Aucun nom trouvé. Vérifie que la liste contient au moins une colonne de noms.")
            result = importing.import_rows(classe, rows)
        except importing.ImportError_ as exc:
            form.add_error(None, str(exc))
        else:
            parts = [f"{result['created']} personne(s) ajoutée(s)"]
            if result["duplicates"]:
                parts.append(f"{result['duplicates']} déjà présente(s)")
            if result["groups_created"]:
                parts.append(f"{result['groups_created']} groupe(s) créé(s)")
            messages.success(request, ", ".join(parts) + ".")
            return redirect("groupes:members", pk=classe.pk)
    return render(request, "groupes/import.html", {"form": form, "classe": classe, "tab": "import"})


@login_required
@require_GET
def export_view(request, pk, fmt):
    classe = _classe(request, pk)
    if fmt not in EXPORTERS:
        raise Http404
    build, content_type = EXPORTERS[fmt]
    response = HttpResponse(build(collect(classe)), content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename(classe, fmt)}"'
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_GET
def activity_export_view(request, pk, apk, fmt):
    activity = _activite(request, pk, apk)
    if fmt not in EXPORTERS:
        raise Http404
    build, content_type = EXPORTERS[fmt]
    response = HttpResponse(build(collect_activity(activity)), content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename(activity, fmt)}"'
    response["Cache-Control"] = "no-store"
    return response


# --- Page publique (lien partagé) ----------------------------------------------
def _public_classe(token) -> Classe:
    return get_object_or_404(Classe, token=token)


def _public_activite(token) -> Activite:
    return get_object_or_404(Activite.objects.select_related("classe"), token=token)


@require_http_methods(["GET", "POST"])
def activity_join(request, token):
    activity = _public_activite(token)
    classe = activity.classe
    if not activity.accepting:
        return render(request, "public/closed.html", {"classe": classe, "activite": activity}, status=200)
    groups = list(activity.groupes.annotate(size=Count("participations")))
    ctx = {"classe": classe, "activite": activity, "groupes": groups, "values": {}, "error": ""}
    if request.method == "GET":
        return render(request, "public/activity_join.html", ctx)

    values = {"name": request.POST.get("name", "").strip()[:120], "member_id": request.POST.get("member_id", ""), "groupe": request.POST.get("groupe", "")}
    ctx["values"] = values

    def fail(message):
        ctx["error"] = message
        return render(request, "public/activity_join.html", ctx, status=400)

    if request.POST.get("website"):
        return redirect("groupes:activity_join_done", token=activity.token)
    if too_many(request, "activity_join", 20):
        return fail("Trop de tentatives. Patiente une minute puis réessaie.")
    member_id = _int_or_none(values["member_id"])
    try:
        with transaction.atomic():
            member_query = classe.membres.select_for_update()
            member = member_query.filter(pk=member_id).first() if member_id else member_query.by_name(values["name"]).first()
            if member is None:
                return fail("Choisis ton nom dans la liste de la classe.")
            participation = activity.participations.select_for_update().filter(membre=member).first()
            if participation and participation.responded_at:
                return fail("Ta réponse pour cette activité a déjà été enregistrée. Contacte l'organisateur pour la modifier.")
            group = None
            if values["groupe"] != "none":
                group = activity.groupes.select_for_update().filter(pk=_int_or_none(values["groupe"])).first()
                if group is None:
                    return fail("Choisis un groupe, ou « Pas de groupe ».")
                if group.capacity is not None and group.participations.count() >= group.capacity:
                    return fail(f"{group.short_label} est complet. Choisis un autre groupe.")
            if participation:
                participation.groupe = group
                participation.responded_at = timezone.now()
                participation.save(update_fields=["groupe", "responded_at"])
            else:
                Participation.objects.create(activite=activity, membre=member, groupe=group, responded_at=timezone.now())
    except IntegrityError:
        return fail("Ta réponse vient d'être enregistrée. Actualise la page pour voir son état.")
    request.session[f"activity_joined:{activity.token}"] = {"name": member.full_name, "group": group.label if group else ""}
    return redirect("groupes:activity_join_done", token=activity.token)


@require_GET
def activity_join_search(request, token):
    activity = _public_activite(token)
    if not activity.accepting or too_many(request, "activity_search", 90):
        return JsonResponse({"results": []})
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})
    answered = activity.participations.filter(responded_at__isnull=False).values_list("membre_id", flat=True)
    found = activity.classe.membres.exclude(pk__in=answered).search(query)[:8]
    return JsonResponse({"results": [{"id": member.pk, "name": member.full_name} for member in found]})


@require_GET
def activity_join_done(request, token):
    activity = _public_activite(token)
    return render(request, "public/done.html", {
        "classe": activity.classe, "activite": activity,
        "result": request.session.get(f"activity_joined:{activity.token}"),
    })


@require_GET
def activity_public_view(request, token, gpk=None):
    activity = get_object_or_404(Activite.objects.select_related("classe"), view_token=token)
    if not activity.is_public:
        raise Http404
    groups = activity.groupes.prefetch_related("participations__membre").order_by(
        F("presentation_order").asc(nulls_last=True), "number",
    )
    if gpk is not None:
        groups = groups.filter(pk=gpk)
        if not groups.exists():
            raise Http404
    for group in groups:
        group.people = [
            participation.membre
            for participation in group.participations.all()
            if participation.responded_at
        ]
    return render(request, "public/activity_view.html", {
        "activite": activity, "classe": activity.classe, "groupes": groups,
        "selected_group": gpk is not None,
        "total_members": sum(len(group.people) for group in groups),
    })


def _public_editing_open(classe) -> bool:
    return bool(classe.accepting and classe.external_intake_open)


@require_GET
def public_members(request, token):
    """Lien unique : tout le monde voit la liste, et peut la compléter si le délégué l'a autorisé."""
    classe = _public_classe(token)
    q = (request.GET.get("q") or "").strip()
    sort = request.GET.get("sort", "list")
    custom_fields = list(classe.champs_membres.filter(public=True).order_by("position", "id"))
    members = list(classe.membres.search(""))
    if q:
        tokens = normalize(q).split()
        members = [
            member for member in members
            if all(
                token_ in normalize(" ".join([member.full_name, member.reference, *map(str, member.custom_data.values())]))
                for token_ in tokens
            )
        ]
    if sort == "alpha":
        members.sort(key=lambda member: member.full_name.casefold())
    elif sort in {field.key for field in custom_fields}:
        members.sort(key=lambda member: str(member.custom_data.get(sort, "")).casefold())
    editing = _public_editing_open(classe)
    return render(request, "public/list.html", {
        "classe": classe, "members": members, "q": q, "sort": sort,
        "custom_fields": custom_fields, "editing": editing,
        "edit_form": PublicListForm(classe=classe, mode="edit") if editing else None,
        "add_form": PublicListForm(classe=classe, mode="add") if editing else None,
        "show_number": any(member.number for member in members),
        "show_sex": any(member.sex for member in members),
        "show_reference": any(member.reference for member in members),
    })


def _public_guard(request, classe):
    """Retourne une redirection si la modification publique n'est pas possible, sinon None."""
    back = redirect("groupes:public_members", token=classe.token)
    if not _public_editing_open(classe):
        messages.error(request, "Les modifications sont fermées pour cette liste.")
        return back
    if request.POST.get("website"):  # champ piège pour les robots
        return back
    if too_many(request, "list_edit", 30):
        messages.error(request, "Trop de modifications d'un coup. Patiente une minute puis réessaie.")
        return back
    return None


def _form_error_text(form) -> str:
    return " ".join(str(error) for errors in form.errors.values() for error in errors) or "Informations invalides."


def _clean_custom(form, classe) -> dict:
    values = {}
    for field in classe.champs_membres.filter(public=True):
        if field.key in form.fields:
            value = form.cleaned_data.get(field.key)
            values[field.key] = "" if value in (None, "") else str(value).strip()
    return values


@require_POST
def public_member_edit(request, token, mpk):
    classe = _public_classe(token)
    blocked = _public_guard(request, classe)
    if blocked:
        return blocked
    member = get_object_or_404(Membre, pk=mpk, classe=classe)
    form = PublicListForm(request.POST, classe=classe, mode="edit")
    if form.is_valid():
        if form.cleaned_data.get("sex"):
            member.sex = form.cleaned_data["sex"]
        member.reference = form.cleaned_data.get("reference", "")
        data = dict(member.custom_data or {})
        for key, value in _clean_custom(form, classe).items():
            if value:
                data[key] = value
            else:
                data.pop(key, None)
        member.custom_data = data
        member.save(update_fields=["sex", "reference", "custom_data"])
        messages.success(request, f"Informations de {member.full_name} mises à jour.")
    else:
        messages.error(request, _form_error_text(form))
    return redirect("groupes:public_members", token=classe.token)


@require_POST
def public_member_add(request, token):
    classe = _public_classe(token)
    blocked = _public_guard(request, classe)
    if blocked:
        return blocked
    form = PublicListForm(request.POST, classe=classe, mode="add")
    if form.is_valid():
        first, last = form.cleaned_data["first_name"], form.cleaned_data["last_name"]
        if classe.membres.by_name(compose_full_name(first, last)).exists():
            messages.error(request, "Ce nom est déjà dans la liste. Utilise « Modifier » sur sa ligne.")
        else:
            try:
                Membre.objects.create(
                    classe=classe, first_name=first, last_name=last,
                    sex=form.cleaned_data.get("sex", ""), reference=form.cleaned_data.get("reference", ""),
                    custom_data={k: v for k, v in _clean_custom(form, classe).items() if v},
                    verified=False, source=Membre.Source.LINK,
                )
                messages.success(request, "Tu as été ajouté(e) à la liste. Le délégué va vérifier ton nom.")
            except IntegrityError:
                messages.error(request, "Ce nom est déjà dans la liste.")
    else:
        messages.error(request, _form_error_text(form))
    return redirect("groupes:public_members", token=classe.token)


@require_http_methods(["GET", "POST"])
def join(request, token):
    classe = _public_classe(token)
    legacy_activity = classe.activites.filter(token=token).first()
    if legacy_activity:
        return redirect("groupes:activity_join", token=legacy_activity.token)
    if not classe.accepting:
        return render(request, "public/closed.html", {"classe": classe}, status=200)

    groupes = _groupes_with_size(classe)
    ctx = {"classe": classe, "groupes": groupes, "values": {}, "error": ""}
    if request.method == "GET":
        return render(request, "public/join.html", ctx)

    values = {
        "name": request.POST.get("name", "").strip()[:120],
        "member_id": request.POST.get("member_id", ""),
        "groupe": request.POST.get("groupe", ""),
    }
    ctx["values"] = values

    def fail(message, status=400):
        ctx["error"] = message
        return render(request, "public/join.html", ctx, status=status)

    if request.POST.get("website"):  # champ piège pour les robots
        return redirect("groupes:join_done", token=classe.token)
    if too_many(request, "join", 20):
        return fail("Trop de tentatives. Patiente une minute puis réessaie.", status=429)
    if not values["name"] and not values["member_id"]:
        return fail("Indique ton nom.")
    if values["groupe"] != "none" and _int_or_none(values["groupe"]) is None:
        return fail("Choisis ton groupe, ou « Pas de groupe ».")

    try:
        with transaction.atomic():
            member_id = _int_or_none(values["member_id"])
            if member_id:
                member = classe.membres.select_for_update().filter(pk=member_id).first()
            else:
                member = classe.membres.select_for_update().by_name(values["name"]).first()

            if member is None:
                if not classe.allow_unlisted:
                    return fail("Ce nom n'est pas dans la liste de la classe. Choisis-le dans les suggestions, ou préviens ton délégué.")
                member = Membre(classe=classe, full_name=values["name"], verified=False, source=Membre.Source.LINK)
            elif member.groupe_id or member.responded_at:
                return fail("Ce nom a déjà été enregistré. S'il y a une erreur, préviens ton délégué.")

            group = None
            if values["groupe"] != "none":
                group = Groupe.objects.select_for_update().filter(pk=_int_or_none(values["groupe"]), classe=classe).first()
                if group is None:
                    return fail("Ce groupe n'existe pas.")
                if group.capacity is not None and group.membres.count() >= group.capacity:
                    return fail(f"{group.short_label} est complet. Choisis un autre groupe.")

            member.groupe, member.responded_at = group, timezone.now()
            if member.pk is None:
                member.source = Membre.Source.LINK
            member.save()
    except IntegrityError:
        return fail("Ce nom vient d'être enregistré par quelqu'un d'autre. Préviens ton délégué si c'est une erreur.")

    request.session[f"joined:{classe.token}"] = {
        "name": member.full_name, "group": group.label if group else "", "pending": not member.verified,
    }
    return redirect("groupes:join_done", token=classe.token)


@require_GET
def join_search(request, token):
    classe = _public_classe(token)
    legacy_activity = classe.activites.filter(token=token).first()
    if legacy_activity:
        return activity_join_search(request, legacy_activity.token)
    if not classe.accepting or too_many(request, "search", 90):
        return JsonResponse({"results": []})
    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})
    found = classe.membres.en_attente().search(q)[:8]
    return JsonResponse({"results": [{"id": m.pk, "name": m.full_name} for m in found]})


@require_GET
def join_done(request, token):
    classe = _public_classe(token)
    legacy_activity = classe.activites.filter(token=token).first()
    if legacy_activity:
        return redirect("groupes:activity_join_done", token=legacy_activity.token)
    return render(request, "public/done.html", {
        "classe": classe, "result": request.session.get(f"joined:{classe.token}"),
    })