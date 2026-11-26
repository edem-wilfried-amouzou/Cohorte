# ruff: noqa: F401
"""Chef de groupe, commentaires sur les activités, affiches QR imprimables."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from ..audit import log_action, snapshot_fields
from ..forms import CommentaireForm
from ..models import Commentaire, Groupe, Membre
from ._common import _activite, _classe


@login_required
@require_POST
def activity_group_set_leader(request, pk, apk, gpk):
    """Désigne (ou retire) le chef d'un groupe d'activité. Le chef doit faire partie du groupe."""
    activity = _activite(request, pk, apk, write=True)
    group = get_object_or_404(Groupe, pk=gpk, activite=activity)
    before = snapshot_fields(group, "leader")
    raw = request.POST.get("leader", "")
    if not raw:
        group.leader = None
        group.save(update_fields=["leader"])
        log_action(request, "chef_groupe_retire", classe=activity.classe, target=group,
                   detail=group.label, before=before, after=snapshot_fields(group, "leader"))
        messages.success(request, f"Chef retiré pour {group.short_label}.")
    else:
        member = Membre.objects.filter(
            pk=raw if raw.isdigit() else 0, classe=activity.classe, participations__groupe=group,
        ).first()
        if member is None:
            messages.error(request, "Le chef doit être un membre de ce groupe.")
        else:
            group.leader = member
            group.save(update_fields=["leader"])
            log_action(request, "chef_groupe", classe=activity.classe, target=group,
                       detail=f"{group.label} : {member.full_name}", before=before,
                       after=snapshot_fields(group, "leader"))
            messages.success(request, f"{member.full_name} est chef de {group.short_label}.")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def comment_add(request, pk, apk):
    activity = _activite(request, pk, apk, write=True)
    form = CommentaireForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.activite, comment.author = activity, request.user
        comment.save()
        messages.success(request, "Commentaire ajouté.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs))
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
@require_POST
def comment_delete(request, pk, apk, cpk):
    activity = _activite(request, pk, apk, write=True)
    comment = get_object_or_404(Commentaire, pk=cpk, activite=activity)
    comment.delete()
    messages.success(request, "Commentaire supprimé.")
    return redirect("groupes:activity_detail", pk=pk, apk=apk)


@login_required
def classe_poster(request, pk):
    """Affiche A4 imprimable : QR code + lien pour rejoindre la classe."""
    classe = _classe(request, pk)
    return render(request, "groupes/poster.html", {
        "title": classe.name, "subtitle": classe.description or "Scanne pour consulter la liste et t'inscrire",
        "qr_url": reverse("groupes:qr", args=[classe.pk]),
        "link": request.build_absolute_uri(reverse("groupes:join", args=[classe.token])),
        "back": reverse("groupes:detail", args=[classe.pk]),
    })


@login_required
def activity_poster(request, pk, apk):
    activity = _activite(request, pk, apk)
    return render(request, "groupes/poster.html", {
        "title": activity.title,
        "subtitle": f"{activity.classe.name} · scanne pour choisir ton groupe",
        "qr_url": reverse("groupes:activity_qr", args=[pk, apk]),
        "link": request.build_absolute_uri(reverse("groupes:activity_join", args=[activity.token])),
        "back": reverse("groupes:activity_detail", args=[pk, apk]),
    })
