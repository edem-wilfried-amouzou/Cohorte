from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import BulkGroupsForm, ClasseForm, GroupeForm, ImportForm, MembreForm
from .models import Classe, Groupe, Membre, normalize
from .services import importing
from .services.allocation import auto_assign
from .services.exporting import EXPORTERS, collect, filename
from .services.qr import qr_svg
from .throttle import too_many


def _classe(request, pk) -> Classe:
    return get_object_or_404(Classe, pk=pk, owner=request.user)


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _groupes_with_size(classe):
    groupes = list(classe.groupes.annotate(size=Count("membres")))
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
        groupe = Groupe.objects.select_for_update().filter(pk=groupe_id, classe_id=member.classe_id).first()
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
        request.user.classes.annotate(
            n_membres=Count("membres", distinct=True),
            n_groupes=Count("groupes", distinct=True),
            n_affectes=Count("membres", filter=Q(membres__groupe__isnull=False), distinct=True),
        )
    )
    for c in classes:
        c.n_sans = c.n_membres - c.n_affectes
        c.pct = round(100 * c.n_affectes / c.n_membres) if c.n_membres else 0
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
        "stats": _stats(classe),
        "groupes": _groupes_with_size(classe),
        "sans": classe.membres.sans_groupe().order_by("search_name")[:12],
        "public_url": request.build_absolute_uri(reverse("groupes:join", args=[classe.token])),
        "tab": "overview",
    })


@login_required
@require_http_methods(["GET", "POST"])
def classe_settings(request, pk):
    classe = _classe(request, pk)
    form = ClasseForm(request.POST or None, instance=classe)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Réglages enregistrés.")
        return redirect("groupes:settings", pk=classe.pk)
    return render(request, "groupes/classe_form.html", {"form": form, "classe": classe, "tab": "settings"})


@login_required
@require_POST
def classe_regenerate(request, pk):
    classe = _classe(request, pk)
    classe.regenerate_token()
    messages.success(request, "Nouveau lien créé. L'ancien ne fonctionne plus.")
    return redirect("groupes:detail", pk=classe.pk)


@login_required
@require_POST
def classe_delete(request, pk):
    classe = _classe(request, pk)
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
    classe = _classe(request, pk)
    add_form = MembreForm(classe=classe)
    if request.method == "POST":
        add_form = MembreForm(request.POST, classe=classe)
        if add_form.is_valid():
            member = add_form.save(commit=False)
            member.classe, member.source = classe, Membre.Source.MANUAL
            if member.groupe_id:
                member.responded_at = timezone.now()
            member.save()
            messages.success(request, f"{member.full_name} ajouté.")
            return redirect("groupes:members", pk=classe.pk)

    flt, q = request.GET.get("f", ""), request.GET.get("q", "").strip()
    qs = classe.membres.select_related("groupe").search(q)
    qs = {
        "sans": qs.sans_groupe(),
        "attente": qs.en_attente(),
        "affectes": qs.affectes(),
        "verifier": qs.filter(verified=False),
    }.get(flt, qs)
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    return render(request, "groupes/members.html", {
        "classe": classe, "page": page, "groupes": _groupes_with_size(classe),
        "add_form": add_form, "f": flt, "q": q, "stats": _stats(classe), "tab": "members",
    })


@login_required
@require_http_methods(["GET", "POST"])
def member_edit(request, pk, mpk):
    classe = _classe(request, pk)
    member = get_object_or_404(Membre, pk=mpk, classe=classe)
    form = MembreForm(request.POST or None, instance=member, classe=classe)
    if request.method == "POST" and form.is_valid():
        member = form.save(commit=False)
        if member.groupe_id and not member.responded_at:
            member.responded_at = timezone.now()
        member.save()
        messages.success(request, "Modifications enregistrées.")
        return redirect("groupes:members", pk=classe.pk)
    return render(request, "groupes/member_form.html", {"form": form, "classe": classe, "member": member, "tab": "members"})


@login_required
@require_POST
def member_delete(request, pk, mpk):
    classe = _classe(request, pk)
    get_object_or_404(Membre, pk=mpk, classe=classe).delete()
    if _is_htmx(request):
        return HttpResponse("")
    messages.success(request, "Personne retirée de la liste.")
    return redirect("groupes:members", pk=classe.pk)


@login_required
@require_POST
def member_verify(request, pk, mpk):
    classe = _classe(request, pk)
    member = get_object_or_404(Membre.objects.select_related("groupe"), pk=mpk, classe=classe)
    member.verified = True
    member.save(update_fields=["verified"])
    return render(request, "groupes/_member_row.html", {"m": member, "classe": classe, "groupes": _groupes_with_size(classe)})


@login_required
@require_POST
def member_assign(request, pk, mpk):
    """Affectation depuis la liste (HTMX → ligne HTML) ou le tableau (glisser-déposer → JSON)."""
    classe = _classe(request, pk)
    member = get_object_or_404(Membre.objects.select_related("groupe"), pk=mpk, classe=classe)
    ok, error = _assign(member, _int_or_none(request.POST.get("groupe")))
    if _is_htmx(request):
        member.refresh_from_db()
        return render(request, "groupes/_member_row.html", {
            "m": member, "classe": classe, "groupes": _groupes_with_size(classe), "error": error,
        })
    if not ok:
        return JsonResponse({"ok": False, "error": error}, status=409)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def auto_assign_view(request, pk):
    classe = _classe(request, pk)
    if not classe.groupes.exists():
        messages.error(request, "Crée d'abord au moins un groupe.")
        return redirect("groupes:groups", pk=classe.pk)
    result = auto_assign(classe)
    if result["assigned"]:
        messages.success(request, f"{result['assigned']} personne(s) réparties de façon équilibrée.")
    if result["left"]:
        messages.warning(request, f"{result['left']} personne(s) restent sans groupe (groupes complets ou aucun groupe libre).")
    if not result["assigned"] and not result["left"]:
        messages.info(request, "Tout le monde a déjà un groupe.")
    return redirect("groupes:groups", pk=classe.pk)


# --- Groupes -------------------------------------------------------------------
@login_required
def groups(request, pk):
    classe = _classe(request, pk)
    columns = list(classe.groupes.prefetch_related("membres"))
    for g in columns:
        g.people = sorted(g.membres.all(), key=lambda m: m.search_name)
        g.full = g.capacity is not None and len(g.people) >= g.capacity
    return render(request, "groupes/groups.html", {
        "classe": classe, "columns": columns,
        "sans": classe.membres.sans_groupe().order_by("search_name"),
        "form": GroupeForm(classe=classe), "bulk": BulkGroupsForm(), "tab": "groups",
    })


@login_required
@require_POST
def group_create(request, pk):
    classe = _classe(request, pk)
    form = GroupeForm(request.POST, classe=classe)
    if form.is_valid():
        group = form.save(commit=False)
        group.classe = classe
        group.save()
        messages.success(request, f"{group.short_label} créé.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("groupes:groups", pk=classe.pk)


@login_required
@require_POST
def groups_bulk(request, pk):
    classe = _classe(request, pk)
    form = BulkGroupsForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Indique un nombre de groupes entre 1 et 100.")
        return redirect("groupes:groups", pk=classe.pk)
    existing = set(classe.groupes.values_list("number", flat=True))
    capacity = form.cleaned_data["capacity"]
    new = [
        Groupe(classe=classe, number=n, capacity=capacity)
        for n in range(1, form.cleaned_data["count"] + 1) if n not in existing
    ]
    Groupe.objects.bulk_create(new)
    messages.success(request, f"{len(new)} groupe(s) créé(s).")
    return redirect("groupes:groups", pk=classe.pk)


@login_required
@require_http_methods(["GET", "POST"])
def group_edit(request, pk, gpk):
    classe = _classe(request, pk)
    group = get_object_or_404(Groupe, pk=gpk, classe=classe)
    form = GroupeForm(request.POST or None, instance=group, classe=classe)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Groupe modifié.")
        return redirect("groupes:groups", pk=classe.pk)
    return render(request, "groupes/group_form.html", {"form": form, "classe": classe, "group": group, "tab": "groups"})


@login_required
@require_POST
def group_delete(request, pk, gpk):
    classe = _classe(request, pk)
    group = get_object_or_404(Groupe, pk=gpk, classe=classe)
    group.delete()
    messages.success(request, "Groupe supprimé. Ses membres sont maintenant sans groupe.")
    return redirect("groupes:groups", pk=classe.pk)


# --- Import / export -----------------------------------------------------------
@login_required
@require_http_methods(["GET", "POST"])
def import_view(request, pk):
    classe = _classe(request, pk)
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


# --- Page publique (lien partagé) ----------------------------------------------
def _public_classe(token) -> Classe:
    return get_object_or_404(Classe, token=token)


@require_http_methods(["GET", "POST"])
def join(request, token):
    classe = _public_classe(token)
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
                member = classe.membres.select_for_update().filter(search_name=normalize(values["name"])).first()

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
    return render(request, "public/done.html", {
        "classe": classe, "result": request.session.get(f"joined:{classe.token}"),
    })
