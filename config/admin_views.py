from functools import wraps

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.groupes.audit import log_action
from apps.groupes.forms import RetourPlateformeUpdateForm
from apps.groupes.models import Activite, AuditLog, Classe, RetourPlateforme


def superuser_only(view_func):
    @login_required(login_url="accounts:login")
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_superuser:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


@superuser_only
def home(request):
    recent_logs = AuditLog.objects.select_related("user", "classe")[:12]
    recent_admin_logs = LogEntry.objects.select_related("user", "content_type")[:10]
    checks = [
        ("Mode production", not settings.DEBUG),
        ("Redirection HTTPS", bool(getattr(settings, "SECURE_SSL_REDIRECT", False))),
        ("Cookies de session sécurisés", bool(getattr(settings, "SESSION_COOKIE_SECURE", False))),
        ("Cookie CSRF sécurisé", bool(getattr(settings, "CSRF_COOKIE_SECURE", False))),
        ("Hôtes définis explicitement", "*" not in settings.ALLOWED_HOSTS),
    ]
    return render(request, "console/home.html", {
        "user_count": get_user_model().objects.count(),
        "staff_count": get_user_model().objects.filter(is_staff=True, is_superuser=False).count(),
        "superuser_count": get_user_model().objects.filter(is_superuser=True).count(),
        "class_count": Classe.objects.count(),
        "activity_count": Activite.objects.count(),
        "feedback_new_count": RetourPlateforme.objects.filter(status=RetourPlateforme.Status.NEW).count(),
        "recent_feedback": RetourPlateforme.objects.select_related("submitted_by")[:5],
        "recent_logs": recent_logs,
        "recent_admin_logs": recent_admin_logs,
        "security_checks": checks,
    })


@superuser_only
def technical(request):
    return render(request, "console/technical.html")


@superuser_only
def users(request):
    User = get_user_model()
    query = (request.GET.get("q") or "").strip()[:100]
    role = request.GET.get("role", "all")
    results = User.objects.all().annotate(owned_classes=Count("classes", distinct=True)).order_by("username")
    if query:
        results = results.filter(
            Q(username__icontains=query) | Q(email__icontains=query)
            | Q(first_name__icontains=query) | Q(last_name__icontains=query),
        )
    if role == "admins":
        results = results.filter(is_staff=True, is_superuser=False)
    elif role == "superusers":
        results = results.filter(is_superuser=True)
    elif role == "inactive":
        results = results.filter(is_active=False)
    else:
        role = "all"
    page = Paginator(results, 50).get_page(request.GET.get("page"))
    return render(request, "console/users.html", {"page": page, "q": query, "role": role})


@superuser_only
def logs(request):
    query = (request.GET.get("q") or "").strip()[:100]
    action = request.GET.get("action", "")
    results = AuditLog.objects.select_related("user", "classe")
    if query:
        results = results.filter(
            Q(action__icontains=query) | Q(detail__icontains=query) | Q(ip__icontains=query)
            | Q(country__icontains=query) | Q(country_code__icontains=query)
            | Q(browser__icontains=query) | Q(operating_system__icontains=query)
            | Q(device__icontains=query) | Q(user_agent__icontains=query)
            | Q(user__username__icontains=query) | Q(user__email__icontains=query)
            | Q(classe__name__icontains=query),
        )
    actions = list(AuditLog.objects.order_by("action").values_list("action", flat=True).distinct())
    if action in actions:
        results = results.filter(action=action)
    else:
        action = ""
    page = Paginator(results, 50).get_page(request.GET.get("page"))
    admin_results = LogEntry.objects.select_related("user", "content_type")
    if query:
        admin_results = admin_results.filter(
            Q(object_repr__icontains=query) | Q(change_message__icontains=query)
            | Q(user__username__icontains=query) | Q(content_type__model__icontains=query),
        )
    admin_page = Paginator(admin_results, 50).get_page(request.GET.get("admin_page"))
    return render(request, "console/logs.html", {
        "page": page, "admin_page": admin_page, "q": query, "action": action, "actions": actions,
    })


@superuser_only
def feedbacks(request):
    query = (request.GET.get("q") or "").strip()[:100]
    status = request.GET.get("status", "new")
    kind = request.GET.get("kind", "")
    results = RetourPlateforme.objects.select_related("submitted_by", "handled_by")
    if query:
        results = results.filter(
            Q(subject__icontains=query) | Q(message__icontains=query)
            | Q(submitter_name__icontains=query) | Q(submitter_email__icontains=query),
        )
    if status == "all":
        status = "all"
    elif status in RetourPlateforme.Status.values:
        results = results.filter(status=status)
    else:
        status = "all"
    if kind in RetourPlateforme.Kind.values:
        results = results.filter(kind=kind)
    else:
        kind = ""
    page = Paginator(results, 30).get_page(request.GET.get("page"))
    return render(request, "console/feedbacks.html", {
        "page": page, "q": query, "status": status, "kind": kind,
        "statuses": RetourPlateforme.Status.choices, "kinds": RetourPlateforme.Kind.choices,
        "new_count": RetourPlateforme.objects.filter(status=RetourPlateforme.Status.NEW).count(),
    })


@superuser_only
@require_http_methods(["GET", "POST"])
def feedback_detail(request, pk):
    item = get_object_or_404(RetourPlateforme.objects.select_related("submitted_by", "handled_by"), pk=pk)
    form = RetourPlateformeUpdateForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.handled_by = request.user
        item.save()
        log_action(request, "retour_plateforme_traite", detail=f"#{item.pk} · {item.status_label}")
        messages.success(request, "Le suivi du retour a été mis à jour.")
        return redirect("console:feedback_detail", pk=item.pk)
    return render(request, "console/feedback_detail.html", {"item": item, "form": form})
