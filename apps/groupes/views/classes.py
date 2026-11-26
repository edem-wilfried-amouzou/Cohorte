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
    CommentaireForm,
    PublicListForm,
    ActiviteForm,
    ActivityGroupsImportForm,
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
from ..services import group_importing, importing
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
        log_action(request, "classe_creee", classe=classe, target=classe, detail=classe.name,
                   after=snapshot_fields(classe, "name", "description", "allow_unlisted", "external_intake_open", "public_show_reference", "public_documents"))
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
        "portal_url": request.build_absolute_uri(reverse("groupes:public_portal", args=[classe.token])),
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
            existing = Collaborateur.objects.filter(classe=classe, user=user).first()
            before = snapshot_fields(existing, "role")
            collaborator, _ = Collaborateur.objects.update_or_create(
                classe=classe, user=user, defaults={"role": form.cleaned_data["role"]},
            )
            log_action(request, "equipe_ajout", classe=classe, target=collaborator,
                       detail=f"{user.username} ({form.cleaned_data['role']})",
                       before=before, after=snapshot_fields(collaborator, "role"))
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
    log_action(request, "equipe_retrait", classe=classe, target=collaborator,
               detail=collaborator.user.username,
               before=snapshot_fields(collaborator, "role", "user", "classe"))
    collaborator.delete()
    messages.success(request, "Accès du collaborateur retiré.")
    return redirect("groupes:collaborators", pk=classe.pk)


@login_required
def activities(request, pk):
    classe = _classe(request, pk)
    return render(request, "groupes/activities.html", {
        "classe": classe, "activites": _activity_rows(classe), "form": ActiviteForm(),
        "can_edit": request.class_role != Collaborateur.Role.VIEWER, "tab": "activities",
    })


def _activity_rows(classe):
    return list(classe.activites.annotate(
        n_groupes=Count("groupes", distinct=True),
        n_affectes=Count("participations", filter=Q(participations__groupe__isnull=False), distinct=True),
        n_reponses=Count("participations", filter=Q(participations__responded_at__isnull=False), distinct=True),
    ))


@login_required
@require_POST
def activity_create(request, pk):
    classe = _classe(request, pk, write=True)
    form = ActiviteForm(request.POST)
    if form.is_valid():
        activity = form.save(commit=False)
        activity.classe = classe
        activity.save()
        log_action(request, "activite_creee", classe=classe, target=activity, detail=activity.title,
                   after=snapshot_fields(activity, "title", "kind", "is_public", "is_open", "deadline"))
        messages.success(request, "Activité créée. Tu peux maintenant créer ses groupes.")
        return redirect("groupes:activity_detail", pk=classe.pk, apk=activity.pk)
    return render(request, "groupes/activities.html", {
        "classe": classe, "activites": _activity_rows(classe), "form": form,
        "can_edit": True, "tab": "activities",
    })


@login_required
def activity_detail(request, pk, apk):
    classe = _classe(request, pk)
    activity = _activite(request, pk, apk)
    groups = list(activity.groupes.select_related("leader").annotate(size=Count("participations")))
    participation_by_member = {
        item.membre_id: item
        for item in activity.participations.select_related("membre", "groupe")
    }
    participants = []
    for member in classe.membres.order_by(Lower("last_name"), Lower("first_name"), "search_name"):
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
        "membres": classe.membres.exclude(
            participations__activite=activity, participations__groupe__isnull=False,
        ).distinct().order_by(Lower("last_name"), Lower("first_name"), "search_name"),
        "group_form": GroupeForm(classe=classe, activite=activity),
        "group_import_form": ActivityGroupsImportForm(),
        "bulk": BulkGroupsForm(), "form": ActiviteForm(instance=activity), "stats": stats,
        "commentaires": activity.commentaires.select_related("author"), "comment_form": CommentaireForm(),
        "can_edit": request.class_role != Collaborateur.Role.VIEWER,
        "can_manage": request.class_role in ("owner", Collaborateur.Role.CO_DELEGATE),
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
        log_action(request, "groupe_activite_cree", classe=activity.classe, target=group, detail=group.label,
                   after=snapshot_fields(group, "number", "name", "capacity", "theme", "presentation_order", "leader"))
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
        before = snapshot_fields(group, "number", "name", "capacity", "theme", "presentation_order", "leader")
        form.save()
        log_action(request, "groupe_activite_modifie", classe=activity.classe, target=group, detail=group.label,
                   before=before, after=snapshot_fields(group, "number", "name", "capacity", "theme", "presentation_order", "leader"))
        messages.success(request, f"{group.short_label} modifié.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_group_delete(request, pk, apk, gpk):
    activity = _activite(request, pk, apk, write=True)
    group = get_object_or_404(Groupe, pk=gpk, activite=activity)
    log_action(request, "groupe_activite_supprime", classe=activity.classe, target=group, detail=group.label,
               before=snapshot_fields(group, "number", "name", "capacity", "theme", "presentation_order", "leader"))
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
    log_action(request, "groupes_activite_crees", classe=activity.classe, target=activity,
               detail=f"{len(groups)} groupe(s) · {activity.title}",
               after={"numeros": [group.number for group in groups]})
    messages.success(request, f"{len(groups)} groupe(s) créé(s).")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_groups_import(request, pk, apk):
    activity = _activite(request, pk, apk, write=True, full=True)
    if too_many(request, "activity_groups_import", 10, 3600):
        messages.error(request, "Trop d’imports effectués. Réessaie plus tard.")
        return redirect("groupes:activity_detail", pk=pk, apk=apk)

    form = ActivityGroupsImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Choisis un fichier Excel ou CSV.")
        return redirect(f"{reverse('groupes:activity_detail', args=[pk, apk])}#activity-groups-import-dialog")
    try:
        rows = group_importing.parse_upload(form.cleaned_data["file"])
        result = group_importing.import_rows(activity, rows)
    except group_importing.ImportError_ as exc:
        messages.error(request, str(exc))
        return redirect(f"{reverse('groupes:activity_detail', args=[pk, apk])}#activity-groups-import-dialog")

    log_action(
        request, "groupes_activite_importes", classe=activity.classe,
        target=activity, detail=f"{activity.title} · {result['assigned']} affectation(s)",
        after={"created_groups": result.get("created_groups", 0), "assigned": result["assigned"]},
        response_status=200,
    )
    return render(request, "groupes/activity_group_import_result.html", {
        "classe": activity.classe,
        "activite": activity,
        "result": result,
        "tab": "activities",
    })


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
    participation = Participation.objects.filter(activite=activity, membre=member).first()
    before = snapshot_fields(participation, "groupe", "responded_at")
    participation, _ = Participation.objects.update_or_create(
        activite=activity, membre=member,
        defaults={"groupe": group, "responded_at": timezone.now()},
    )
    log_action(request, "affectation_activite_modifiee", classe=activity.classe, target=participation,
               detail=f"{member.full_name} · {activity.title}", before=before,
               after=snapshot_fields(participation, "groupe", "responded_at"))
    messages.success(request, f"Affectation de {member.full_name} mise à jour.")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_assign_bulk(request, pk, apk):
    activity = _activite(request, pk, apk, write=True)
    raw_ids = request.POST.getlist("member_ids")
    member_ids = [_int_or_none(value) for value in raw_ids]
    if not member_ids or any(value is None for value in member_ids):
        messages.error(request, "Sélectionne des personnes à affecter.")
        return redirect("groupes:activity_detail", pk=pk, apk=apk)
    member_ids = list(dict.fromkeys(member_ids))
    if len(member_ids) > 5000:
        messages.error(request, "La sélection dépasse la limite de 5 000 personnes.")
        return redirect("groupes:activity_detail", pk=pk, apk=apk)

    members = list(activity.classe.membres.filter(pk__in=member_ids))
    if len(members) != len(member_ids):
        messages.error(request, "Une ou plusieurs personnes ne font pas partie de cette classe.")
        return redirect("groupes:activity_detail", pk=pk, apk=apk)
    group_id = _int_or_none(request.POST.get("group_id"))
    if group_id is None:
        messages.error(request, "Choisis un groupe de destination.")
        return redirect("groupes:activity_detail", pk=pk, apk=apk)

    with transaction.atomic():
        group = get_object_or_404(Groupe.objects.select_for_update(), pk=group_id, activite=activity)
        existing = {
            item.membre_id: item
            for item in Participation.objects.filter(activite=activity, membre_id__in=member_ids)
        }
        current_size = Participation.objects.filter(activite=activity, groupe=group).count()
        additions = sum(
            1 for member in members
            if member.pk not in existing or existing[member.pk].groupe_id != group.pk
        )
        if group.capacity is not None and current_size + additions > group.capacity:
            messages.error(request, f"La sélection dépasse la capacité de {group.short_label}.")
            return redirect("groupes:activity_detail", pk=pk, apk=apk)

        now = timezone.now()
        create = []
        update = []
        for member in members:
            participation = existing.get(member.pk)
            if participation is None:
                create.append(Participation(
                    activite=activity, membre=member, groupe=group, responded_at=now,
                ))
            elif participation.groupe_id != group.pk or participation.responded_at is None:
                participation.groupe = group
                participation.responded_at = participation.responded_at or now
                update.append(participation)
        if create:
            Participation.objects.bulk_create(create, batch_size=500)
        if update:
            Participation.objects.bulk_update(update, ["groupe", "responded_at"], batch_size=500)

    log_action(
        request, "membres_affectes_activite", classe=activity.classe,
        target=activity, detail=f"{len(members)} personne(s) · {activity.title} · {group.label}",
        before={"member_ids": member_ids[:100]},
        after={"group_id": group.pk, "group": group.label, "assigned_count": len(members)},
    )
    messages.success(request, f"{len(members)} personne(s) affectée(s) à {group.label}.")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_regenerate(request, pk, apk):
    activity = _activite(request, pk, apk, write=True, full=True)
    activity.regenerate_token()
    log_action(request, "lien_activite_renouvele", classe=activity.classe, detail=activity.title)
    messages.success(request, "Nouveau lien créé. L'ancien ne fonctionne plus.")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_view_regenerate(request, pk, apk):
    activity = _activite(request, pk, apk, write=True, full=True)
    activity.regenerate_view_token()
    log_action(request, "lien_consultation_renouvele", classe=activity.classe, detail=activity.title)
    messages.success(request, "Nouveau lien de consultation créé. L’ancien lien est révoqué.")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_update(request, pk, apk):
    activity = _activite(request, pk, apk, write=True)
    form = ActiviteForm(request.POST, instance=activity)
    if form.is_valid():
        before = snapshot_fields(activity, "title", "kind", "is_public", "is_open", "deadline")
        form.save()
        log_action(request, "activite_modifiee", classe=activity.classe, target=activity, detail=activity.title,
                   before=before, after=snapshot_fields(activity, "title", "kind", "is_public", "is_open", "deadline"))
        messages.success(request, "Réglages de l’activité enregistrés.")
    else:
        messages.error(request, " ".join(error for errors in form.errors.values() for error in errors))
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def activity_delete(request, pk, apk):
    activity = _activite(request, pk, apk, write=True, full=True)
    log_action(request, "activite_supprimee", classe=activity.classe, target=activity, detail=activity.title,
               before=snapshot_fields(activity, "title", "kind", "is_public", "is_open", "deadline"))
    activity.delete()
    messages.success(request, "Activité supprimée.")
    return redirect("groupes:activities", pk=pk)


@login_required
@require_http_methods(["GET", "POST"])
def classe_settings(request, pk):
    classe = _classe(request, pk, owner=request.method == "POST")
    form = ClasseForm(request.POST or None, instance=classe)
    if request.method == "POST" and form.is_valid():
        before = snapshot_fields(classe, "name", "description", "allow_unlisted", "external_intake_open", "public_show_reference", "public_documents", "allow_public_folder_creation", "allow_public_uploads")
        form.save()
        log_action(request, "classe_reglages_modifies", classe=classe, target=classe, detail=classe.name,
                   before=before,
                   after=snapshot_fields(classe, "name", "description", "allow_unlisted", "external_intake_open", "public_show_reference", "public_documents", "allow_public_folder_creation", "allow_public_uploads"))
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
    log_action(request, "lien_classe_renouvele", classe=classe, detail=classe.name)
    messages.success(request, "Nouveau lien créé. L'ancien ne fonctionne plus.")
    return redirect("groupes:detail", pk=classe.pk)


@login_required
@require_POST
def classe_delete(request, pk):
    classe = _classe(request, pk, owner=True)
    log_action(request, "classe_supprimee", classe=classe, target=classe, detail=classe.name,
               before=snapshot_fields(classe, "name", "description", "allow_unlisted", "external_intake_open", "public_show_reference", "public_documents"))
    classe.delete()
    messages.success(request, "Classe supprimée.")
    return redirect("groupes:dashboard")


@login_required
@require_GET
def classe_qr(request, pk):
    classe = _classe(request, pk)
    url = request.build_absolute_uri(reverse("groupes:join", args=[classe.token]))
    return HttpResponse(qr_svg(url), content_type="image/svg+xml")


