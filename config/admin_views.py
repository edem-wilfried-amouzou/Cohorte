from functools import wraps
import csv
import json
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_http_methods

from apps.groupes.audit import log_action
from apps.groupes.forms import RetourPlateformeUpdateForm
from apps.groupes.models import Activite, AuditLog, Classe, RetourPlateforme
from .admin_forms import StaffProfileForm


def superuser_only(view_func):
    @login_required(login_url="accounts:login")
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_superuser:
            log_action(
                request, "acces_refuse", detail=f"Console : {_route_for_denial(request)}",
                succeeded=False, response_status=403,
            )
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


@superuser_only
@require_GET
def home(request):
    log_action(request, "console_consultee", detail="Vue d’ensemble")
    recent_logs = AuditLog.objects.select_related("user", "classe")[:12]
    recent_admin_logs = LogEntry.objects.select_related("user", "content_type")[:10]
    checks = [
        ("Mode production", not settings.DEBUG),
        ("Redirection HTTPS", bool(getattr(settings, "SECURE_SSL_REDIRECT", False))),
        ("Cookies de session sécurisés", bool(getattr(settings, "SESSION_COOKIE_SECURE", False))),
        ("Cookie CSRF sécurisé", bool(getattr(settings, "CSRF_COOKIE_SECURE", False))),
        ("Hôtes définis explicitement", "*" not in settings.ALLOWED_HOSTS),
    ]
    day_ago = timezone.now() - timedelta(hours=24)
    day_logs = AuditLog.objects.filter(created_at__gte=day_ago)
    active_users = list(
        day_logs.filter(actor_user_id__isnull=False).values("actor_user_id", "actor_identifier")
        .annotate(total=Count("pk")).order_by("-total")[:5]
    )
    countries = list(
        day_logs.filter(action="connexion", succeeded=True).exclude(country_code="")
        .values("country", "country_code").annotate(total=Count("pk")).order_by("-total", "country")[:10]
    )
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
        "login_success_24h": day_logs.filter(action="connexion", succeeded=True).count(),
        "login_failure_24h": day_logs.filter(action="connexion_echec").count(),
        "denied_24h": day_logs.filter(Q(response_status=403) | Q(action="acces_refuse")).count(),
        "active_users": active_users,
        "countries": countries,
        "sensitive_logs": AuditLog.objects.filter(
            Q(action__icontains="supprim") | Q(action__icontains="suppression") | Q(action__icontains="import")
            | Q(response_status=403) | Q(action="acces_refuse") | Q(action__icontains="equipe")
        )[:8],
        "security_checks": checks,
    })


@superuser_only
@require_GET
def technical(request):
    log_action(request, "documentation_console_consultee")
    return render(request, "console/technical.html")


@superuser_only
@require_GET
def users(request):
    log_action(request, "utilisateurs_console_consultes")
    User = get_user_model()
    query = (request.GET.get("q") or "").strip()[:100]
    role = request.GET.get("role", "all")
    results = User.objects.all().annotate(
        owned_classes=Count("classes", distinct=True),
        permission_count=Count("user_permissions", distinct=True),
    ).prefetch_related("user_permissions").order_by("username")
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
@require_http_methods(["GET", "POST"])
def staff_create(request):
    if request.method == "GET":
        log_action(request, "creation_staff_consultee")
    form = StaffProfileForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        staff_user = form.save()
        log_action(
            request, "profil_staff_cree", target=staff_user,
            detail=f"{staff_user.username} · {staff_user.user_permissions.count()} permission(s)",
            before={"is_staff": False, "is_superuser": False},
            after={"is_staff": True, "is_superuser": False, "permissions": list(staff_user.user_permissions.values_list("codename", flat=True))},
        )
        messages.success(request, f"Le profil staff « {staff_user.username} » a été créé.")
        return redirect("console:users")
    if request.method == "POST":
        log_action(
            request, "profil_staff_creation_echec",
            detail=request.POST.get("username", "")[:150],
            succeeded=False, response_status=200,
        )
    return render(request, "console/staff_create.html", {"form": form})


def _logs_filter(request):
    query = (request.GET.get("q") or "").strip()[:100]
    period = request.GET.get("period", "")
    user_id = request.GET.get("user", "")
    action = request.GET.get("action", "")
    object_type = request.GET.get("object_type", "")[:100]
    object_id = request.GET.get("object_id", "")[:100]
    ip = request.GET.get("ip", "")[:64].strip()
    country_code = request.GET.get("country", "")[:2].upper()
    city = request.GET.get("city", "")[:100].strip()
    result = request.GET.get("result", "")
    start = parse_date(request.GET.get("from", ""))
    end = parse_date(request.GET.get("to", ""))
    now = timezone.localtime()
    if period in {"today", "7d", "30d"}:
        days = {"today": 1, "7d": 7, "30d": 30}[period]
        start = now.date() if period == "today" else (now - timedelta(days=days - 1)).date()
        end = now.date()
    results = AuditLog.objects.select_related("user", "classe")
    if start:
        results = results.filter(created_at__gte=timezone.make_aware(datetime.combine(start, time.min)))
    if end:
        results = results.filter(created_at__lt=timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min)))
    if user_id.isdigit():
        results = results.filter(actor_user_id=int(user_id))
    else:
        user_id = ""
    actions = list(AuditLog.objects.order_by("action").values_list("action", flat=True).distinct())
    if action in actions:
        results = results.filter(action=action)
    else:
        action = ""
    if object_type:
        results = results.filter(object_type__iexact=object_type)
    if object_id:
        results = results.filter(object_id__icontains=object_id)
    if ip:
        results = results.filter(ip=ip)
    if country_code:
        results = results.filter(country_code=country_code)
    if city:
        results = results.filter(city=city)
    if result == "success":
        results = results.filter(succeeded=True)
    elif result == "failure":
        results = results.filter(Q(succeeded=False) | Q(response_status__gte=400))
    else:
        result = ""
    if query:
        match = (
            Q(action__icontains=query) | Q(detail__icontains=query) | Q(ip__icontains=query)
            | Q(country__icontains=query) | Q(country_code__icontains=query) | Q(city__icontains=query)
            | Q(region__icontains=query) | Q(path__icontains=query) | Q(object_id__icontains=query)
            | Q(object_label__icontains=query) | Q(actor_identifier__icontains=query)
            | Q(user__username__icontains=query) | Q(user__email__icontains=query)
        )
        if query.isdigit():
            match |= Q(actor_user_id=int(query))
        results = results.filter(match)
    filters = {
        "q": query, "period": period, "user": user_id, "action": action,
        "object_type": object_type, "object_id": object_id, "ip": ip,
        "country": country_code, "city": city, "result": result,
        "from": start.isoformat() if start else "", "to": end.isoformat() if end else "",
    }
    return results, filters, actions


def _route_for_denial(request):
    match = getattr(request, "resolver_match", None)
    return getattr(match, "route", "") or request.path_info


def _log_filter_context(request, filters, actions):
    user_rows = AuditLog.objects.exclude(actor_user_id__isnull=True).values("actor_user_id", "actor_identifier").distinct().order_by("actor_identifier")[:500]
    users = [{"pk": row["actor_user_id"], "username": row["actor_identifier"]} for row in user_rows]
    countries = AuditLog.objects.exclude(country_code="").values_list("country_code", "country").distinct().order_by("country")[:250]
    query = request.GET.copy()
    query.pop("page", None)
    return {
        "filters": filters, "actions": actions, "users": users,
        "countries": countries, "query_string": query.urlencode(),
    }


@superuser_only
@require_GET
def logs(request):
    log_action(request, "journaux_consultes", detail="Filtres appliqués" if request.GET else "")
    results, filters, actions = _logs_filter(request)
    page = Paginator(results, 50).get_page(request.GET.get("page"))
    admin_results = LogEntry.objects.select_related("user", "content_type")
    if filters["q"]:
        admin_results = admin_results.filter(
            Q(object_repr__icontains=filters["q"]) | Q(change_message__icontains=filters["q"])
            | Q(user__username__icontains=filters["q"]) | Q(content_type__model__icontains=filters["q"]),
        )
    admin_page = Paginator(admin_results, 50).get_page(request.GET.get("admin_page"))
    context = _log_filter_context(request, filters, actions)
    context.update({"page": page, "admin_page": admin_page, "q": filters["q"]})
    return render(request, "console/logs.html", context)


@superuser_only
@require_GET
def log_detail(request, pk):
    event = get_object_or_404(AuditLog.objects.select_related("user", "classe"), pk=pk)
    log_action(request, "journal_detail_consulte", target=event, detail=f"Journal #{event.pk}")
    before = event.before_data if isinstance(event.before_data, dict) else {}
    after = event.after_data if isinstance(event.after_data, dict) else {}
    changed_fields = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
    changes = [{
        "field": key,
        "before": json.dumps(before.get(key), ensure_ascii=False, indent=2) if key in before else "—",
        "after": json.dumps(after.get(key), ensure_ascii=False, indent=2) if key in after else "—",
    } for key in changed_fields]
    related = AuditLog.objects.select_related("user").none()
    if event.object_type and event.object_id:
        related = AuditLog.objects.select_related("user").filter(object_type=event.object_type, object_id=event.object_id).exclude(pk=event.pk)[:20]
    return render(request, "console/log_detail.html", {
        "event": event, "changed_fields": changed_fields, "changes": changes, "related": related,
        "object_history": {"object_type": event.object_type, "object_id": event.object_id},
        "event_utc_label": event.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@superuser_only
@require_GET
def logs_export(request):
    results, filters, _ = _logs_filter(request)
    latest_id = AuditLog.objects.order_by("-pk").values_list("pk", flat=True).first() or 0
    results = results.filter(pk__lte=latest_id)
    log_action(request, "journaux_exportes", detail=f"{results.count()} ligne(s)")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="cohorte-journaux.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["Date UTC", "ID utilisateur", "Utilisateur", "Rôle", "Action", "Objet", "ID objet", "URL", "IP", "Pays", "Code pays", "Région", "Ville", "Code HTTP", "Résultat", "Détail", "Avant", "Après", "Navigateur", "Système", "User agent"])
    for event in results.iterator(chunk_size=500):
        row = [
            event.created_at.isoformat(), event.actor_user_id or "", event.actor_identifier, event.actor_role, event.action,
            event.object_label, event.object_id, event.path, event.ip, event.country, event.country_code,
            event.region, event.city, event.response_status, "Succès" if event.succeeded else "Échec",
            event.detail,
            json.dumps(event.before_data, ensure_ascii=False) if event.before_data is not None else "",
            json.dumps(event.after_data, ensure_ascii=False) if event.after_data is not None else "",
            event.browser, event.operating_system, event.user_agent,
        ]
        writer.writerow([_csv_safe(value) for value in row])
    return response


def _csv_safe(value):
    if not isinstance(value, str):
        return value
    return f"'{value}" if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value


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
