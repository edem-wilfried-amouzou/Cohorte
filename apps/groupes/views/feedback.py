from urllib.parse import urlsplit

from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from ..forms import RetourPlateformeForm
from ..throttle import too_many


def _safe_return_url(request, value):
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return value
    return "/"


@require_http_methods(["GET", "POST"])
def feedback(request):
    """Reçoit les suggestions et signalements depuis le formulaire du pied de page."""
    if request.method == "GET":
        sent = request.session.pop("feedback_sent", False)
        return_to = _safe_return_url(request, request.session.pop("feedback_return_to", ""))
        form = RetourPlateformeForm(initial={
            "submitter_name": request.user.get_full_name() if request.user.is_authenticated else "",
            "submitter_email": request.user.email if request.user.is_authenticated else "",
        })
        return render(request, "feedback.html", {"form": form, "sent": sent, "return_to": return_to})

    return_to = _safe_return_url(request, request.POST.get("next", ""))
    if request.POST.get("website"):
        request.session["feedback_sent"] = True
        request.session["feedback_return_to"] = return_to
        return redirect("groupes:feedback")

    form = RetourPlateformeForm(request.POST)
    if too_many(request, "platform_feedback", 5, 3600):
        form.add_error(None, "Trop de messages ont été envoyés depuis ce réseau. Réessaie plus tard.")
    if form.is_valid():
        item = form.save(commit=False)
        if request.user.is_authenticated:
            item.submitted_by = request.user
            item.submitter_name = item.submitter_name or request.user.get_full_name() or request.user.get_username()
            item.submitter_email = item.submitter_email or request.user.email
        item.source_path = urlsplit(return_to).path[:255]
        item.save()
        request.session["feedback_sent"] = True
        request.session["feedback_return_to"] = return_to
        return redirect("groupes:feedback")

    return render(request, "feedback.html", {"form": form, "sent": False, "return_to": return_to}, status=400)
