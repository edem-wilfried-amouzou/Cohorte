# ruff: noqa: F401
from types import SimpleNamespace
from urllib.parse import urlencode

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
    Activite, ChampMembre, Classe, Collaborateur, DemandeMembre, Document, DossierDocument,
    Groupe, Membre, Participation,
    compose_full_name, normalize,
)
from ..services import importing
from ..services.allocation import auto_assign
from ..services.documents import can_preview_in_browser, folder_is_public
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
    ctx = {
        "classe": classe, "activite": activity, "groupes": groups, "values": {}, "error": "",
        "annonces": activity.commentaires.filter(is_public=True)[:5],
    }
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
    groups = activity.groupes.select_related("leader").prefetch_related("participations__membre").order_by(
        F("presentation_order").asc(nulls_last=True), "number",
    )
    if gpk is not None:
        groups = groups.filter(pk=gpk)
        if not groups.exists():
            raise Http404
    for group in groups:
        group.people = sorted([
            participation.membre
            for participation in group.participations.all()
            if participation.responded_at
        ], key=lambda member: (
            normalize(member.last_name or member.full_name),
            normalize(member.first_name or ""),
        ))
    return render(request, "public/activity_view.html", {
        "activite": activity, "classe": activity.classe, "groupes": groups,
        "selected_group": gpk is not None,
        "annonces": activity.commentaires.filter(is_public=True)[:5],
        "total_members": sum(len(group.people) for group in groups),
    })


@require_GET
def public_activity_index(request):
    """Catalogue global des exposés/projets dont le propriétaire a activé la consultation publique."""
    query = (request.GET.get("q") or "").strip()[:100]
    activities = Activite.objects.filter(is_public=True).select_related("classe").annotate(
        group_count=Count("groupes", distinct=True),
        participant_count=Count(
            "participations", filter=Q(participations__responded_at__isnull=False), distinct=True,
        ),
    )
    if query:
        activities = activities.filter(
            Q(title__icontains=query) | Q(classe__name__icontains=query),
        )
    page = Paginator(activities.order_by("classe__name", "title"), 30).get_page(request.GET.get("page"))
    return render(request, "public/activity_index.html", {"page": page, "q": query})


@require_GET
def public_portal(request, token):
    """Page publique d'une classe : activités partagées, groupes et dossiers visibles."""
    classe = _public_classe(token)
    view = request.GET.get("view", "members")
    if view not in {"members", "documents", "activities"}:
        view = "members"
    if request.GET.get("folder"):
        view = "documents"
    if request.GET.get("activity"):
        view = "activities"
    if view == "documents" and not classe.public_documents:
        view = "members"
    activities = classe.activites.filter(
        Q(is_public=True) | Q(is_open=True, deadline__isnull=True) | Q(is_open=True, deadline__gt=timezone.now()),
    ).annotate(
        group_count=Count("groupes", distinct=True),
        participant_count=Count(
            "participations", filter=Q(participations__responded_at__isnull=False), distinct=True,
        ),
    ).order_by("title")
    all_folders = list(
        classe.dossiers_documents.select_related("classe", "activite", "parent").prefetch_related("documents").order_by(
            "activite__title", "parent__name", "name",
        )
    )
    public_folders = [folder for folder in all_folders if folder_is_public(folder)] if classe.public_documents else []
    children_by_parent = {}
    for folder in public_folders:
        children_by_parent.setdefault(folder.parent_id, []).append(folder)
        folder.visible_documents = [document for document in folder.documents.all() if document.is_public]
        folder.downloadable_documents = [
            document for document in folder.visible_documents
            if folder.allow_public_downloads and document.allow_public_downloads
        ]
        for document in folder.visible_documents:
            document.public_previewable = can_preview_in_browser(document)
            document.public_downloadable = folder.allow_public_downloads and document.allow_public_downloads
        folder.total_size = sum(document.byte_size for document in folder.visible_documents)

    def count_downloadable(folder):
        children = children_by_parent.get(folder.pk, [])
        folder.subfolder_count = len(children)
        direct = len(folder.downloadable_documents)
        folder.downloadable_count = direct + sum(count_downloadable(child) for child in children)
        return folder.downloadable_count

    for root in children_by_parent.get(None, []):
        count_downloadable(root)

    folder_id = request.GET.get("folder")
    if folder_id and classe.public_documents:
        current_folder = get_object_or_404(DossierDocument, pk=folder_id, classe=classe)
        if not folder_is_public(current_folder):
            raise Http404
        current_folder = next(folder for folder in public_folders if folder.pk == current_folder.pk)
    else:
        current_folder = None
    folders = children_by_parent.get(current_folder.pk if current_folder else None, [])
    if current_folder:
        documents = current_folder.visible_documents
    elif classe.public_documents:
        documents = list(classe.documents.filter(folder__isnull=True, is_public=True))
        for document in documents:
            document.public_previewable = can_preview_in_browser(document)
            document.public_downloadable = document.allow_public_downloads
    else:
        documents = []
    breadcrumbs = []
    parent = current_folder
    while parent:
        breadcrumbs.insert(0, parent)
        parent = parent.parent

    member_context = _public_member_directory(request, classe) if view == "members" else {}
    selected_activity = None
    selected_groups = []
    total_group_members = 0
    activity_id = request.GET.get("activity")
    if view == "activities" and activity_id:
        selected_activity = get_object_or_404(Activite, pk=activity_id, classe=classe, is_public=True)
        selected_groups = list(
            selected_activity.groupes.select_related("leader").prefetch_related("participations__membre").order_by(
                F("presentation_order").asc(nulls_last=True), "number",
            )
        )
        for group in selected_groups:
            group.people = sorted([
                participation.membre for participation in group.participations.all()
                if participation.responded_at
            ], key=lambda member: (
                normalize(member.last_name or member.full_name), normalize(member.first_name or ""),
            ))
            total_group_members += len(group.people)

    dashboard_url = None
    if request.user.is_authenticated and (
        classe.owner_id == request.user.pk
        or classe.collaborateurs.filter(user=request.user).exists()
    ):
        dashboard_url = reverse("groupes:detail", args=[classe.pk])

    return render(request, "public/portal.html", {
        "classe": classe,
        "activites": activities,
        "folders": folders,
        "all_public_folders": public_folders,
        "current_folder": current_folder,
        "documents": documents,
        "folder_breadcrumbs": breadcrumbs,
        "view": view,
        "selected_activity": selected_activity,
        "selected_groups": selected_groups,
        "total_group_members": total_group_members,
        "public_documents_enabled": classe.public_documents,
        "public_list_editing": _public_editing_open(classe),
        "member_count": classe.membres.count(),
        "portal_url": request.build_absolute_uri(reverse("groupes:public_portal", args=[classe.token])),
        "dashboard_url": dashboard_url,
        **member_context,
    })


def _public_editing_open(classe) -> bool:
    return bool(classe.accepting and classe.external_intake_open)


@require_GET
def public_members(request, token):
    """Lien unique : tout le monde voit la liste, et peut la compléter si le délégué l'a autorisé."""
    classe = _public_classe(token)
    query = urlencode({**request.GET.dict(), "view": "members"})
    return redirect(f"{reverse('groupes:public_portal', args=[classe.token])}?{query}")


def _public_member_directory(request, classe):
    q = (request.GET.get("q") or "").strip()
    sort = request.GET.get("sort", "alpha")
    custom_fields = list(classe.champs_membres.filter(public=True).order_by("position", "id"))
    members = list(classe.membres.search(""))
    if q:
        tokens = normalize(q).split()
        public_keys = {field.key for field in custom_fields}

        def haystack(member) -> str:
            # La recherche ne porte que sur ce que la page affiche : sinon elle révélerait les champs privés.
            parts = [member.full_name]
            if classe.public_show_reference:
                parts.append(member.reference)
            parts.extend(str(value) for key, value in member.custom_data.items() if key in public_keys)
            return normalize(" ".join(parts))

        members = [member for member in members if all(token_ in haystack(member) for token_ in tokens)]
    if sort == "alpha":
        members.sort(key=lambda member: (
            normalize(member.last_name or member.full_name),
            normalize(member.first_name or ""),
        ))
    elif sort in {field.key for field in custom_fields}:
        members.sort(key=lambda member: str(member.custom_data.get(sort, "")).casefold())
    editing = _public_editing_open(classe)
    return {
        "members": members, "q": q, "sort": sort,
        "custom_fields": custom_fields, "editing": editing,
        "edit_form": PublicListForm(classe=classe, mode="edit") if editing else None,
        "add_form": PublicListForm(classe=classe, mode="add") if editing else None,
        "show_number": any(member.number for member in members),
        "show_sex": any(member.sex for member in members),
        "show_reference": classe.public_show_reference and any(member.reference for member in members),
    }


def _public_guard(request, classe):
    """Retourne une redirection si la modification publique n'est pas possible, sinon None."""
    back = redirect(f"{reverse('groupes:public_portal', args=[classe.token])}?view=members")
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
        if classe.public_show_reference:
            member.reference = form.cleaned_data.get("reference", "")
        data = dict(member.custom_data or {})
        for key, value in _clean_custom(form, classe).items():
            if value:
                data[key] = value
            else:
                data.pop(key, None)
        member.custom_data = data
        member.save(update_fields=["sex", "reference", "custom_data"])
        log_action(request, "modif_publique", classe=classe, detail=member.full_name)
        messages.success(request, f"Informations de {member.full_name} mises à jour.")
    else:
        messages.error(request, _form_error_text(form))
    return redirect(f"{reverse('groupes:public_portal', args=[classe.token])}?view=members")


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
                log_action(request, "ajout_public", classe=classe, detail=compose_full_name(first, last))
                messages.success(request, "Tu as été ajouté(e) à la liste. Le délégué va vérifier ton nom.")
            except IntegrityError:
                messages.error(request, "Ce nom est déjà dans la liste.")
    else:
        messages.error(request, _form_error_text(form))
    return redirect(f"{reverse('groupes:public_portal', args=[classe.token])}?view=members")


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
