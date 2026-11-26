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
            log_action(request, "import", classe=classe, detail=", ".join(parts))
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
    content = build(collect(classe))
    log_action(request, "export_classe", classe=classe, target=classe, detail=fmt)
    response = HttpResponse(content, content_type=content_type)
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
    content = build(collect_activity(activity))
    log_action(request, "export_activite", classe=activity.classe, target=activity, detail=fmt)
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename(activity, fmt)}"'
    response["Cache-Control"] = "no-store"
    return response


