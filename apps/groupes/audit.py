import ipaddress
import hashlib
import logging
import math
import os
import re
from datetime import timedelta
from functools import lru_cache

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Q
from django.utils import timezone

from .models import AuditLog
from .throttle import client_ip

logger = logging.getLogger(__name__)

COUNTRY_NAMES = {
    "BJ": "Bénin", "BF": "Burkina Faso", "CI": "Côte d’Ivoire", "CM": "Cameroun",
    "DE": "Allemagne", "FR": "France", "GH": "Ghana", "GN": "Guinée",
    "GB": "Royaume-Uni", "ML": "Mali", "NE": "Niger", "NG": "Nigeria",
    "SN": "Sénégal", "TG": "Togo", "US": "États-Unis",
}
_SECRET_KEY = re.compile(
    r"password|passwd|token|secret|authorization|cookie|session.?id|csrf|api.?key|private.?key|card|iban|bank|account.?number|routing|swift|bic|cvv|cvc",
    re.I,
)
_SECRET_VALUE = re.compile(
    r"(?i)(password|passwd|token|secret|authorization|cookie|session.?id|csrf|api.?key|private.?key|card|iban|bank|account.?number|routing|swift|bic|cvv|cvc)\s*[:=]\s*[^,;\s]+|\bBearer\s+[^,;\s]+"
)


def _country_name(code: str) -> str:
    if code in COUNTRY_NAMES:
        return COUNTRY_NAMES[code]
    try:
        import pycountry

        country = pycountry.countries.get(alpha_2=code)
        return country.name if country else code
    except ImportError:
        return code


def _version(pattern: str, user_agent: str) -> str:
    match = re.search(pattern, user_agent, re.IGNORECASE)
    return match.group(1) if match else ""


def _redact_text(value: str) -> str:
    return _SECRET_VALUE.sub(
        lambda match: f"{match.group(1)}=[masqué]" if match.group(1) else "Bearer [masqué]",
        value or "",
    )


def _browser(user_agent: str) -> str:
    for pattern, name in (
        (r"Edg(?:e|A|iOS)?/([\d.]+)", "Edge"),
        (r"OPR/([\d.]+)", "Opera"),
        (r"SamsungBrowser/([\d.]+)", "Samsung Internet"),
        (r"(?:Firefox|FxiOS)/([\d.]+)", "Firefox"),
        (r"(?:Chrome|CriOS)/([\d.]+)", "Chrome"),
        (r"Version/([\d.]+).*Safari", "Safari"),
        (r"(?:MSIE |rv:)([\d.]+).*?(?:Trident|MSIE)", "Internet Explorer"),
    ):
        version = _version(pattern, user_agent)
        if version:
            return f"{name} {version.split('.')[0]}"
    return "Inconnu"


def _operating_system(user_agent: str) -> str:
    if re.search(r"Android", user_agent, re.IGNORECASE):
        return f"Android {_version(r'Android[ /-]([\d.]+)', user_agent)}".strip()
    if re.search(r"iPhone|iPad|iPod", user_agent, re.IGNORECASE):
        version = _version(r"(?:iPhone )?OS ([\d_]+)", user_agent).replace("_", ".")
        return f"iOS {version}" if version else "iOS"
    if re.search(r"Windows NT", user_agent, re.IGNORECASE):
        version = _version(r"Windows NT ([\d.]+)", user_agent)
        versions = {"10.0": "10/11", "6.3": "8.1", "6.2": "8", "6.1": "7", "6.0": "Vista", "5.1": "XP"}
        return f"Windows {versions.get(version, version)}".strip()
    if re.search(r"CrOS", user_agent, re.IGNORECASE):
        return "ChromeOS"
    if re.search(r"Mac OS X|Macintosh", user_agent, re.IGNORECASE):
        version = _version(r"Mac OS X ([\d_]+)", user_agent).replace("_", ".")
        return f"macOS {version}" if version else "macOS"
    if re.search(r"Linux", user_agent, re.IGNORECASE):
        return "Linux"
    return "Inconnu"


def _device(user_agent: str) -> str:
    if re.search(r"iPad|Tablet", user_agent, re.IGNORECASE):
        return "Tablette"
    if re.search(r"iPhone|iPod|Android.*Mobile|Mobile", user_agent, re.IGNORECASE):
        return "Téléphone"
    return "Ordinateur" if user_agent else "Inconnu"


@lru_cache(maxsize=2)
def _geo_reader(path, modified_ns, size):
    try:
        import geoip2.database

        return geoip2.database.Reader(path)
    except Exception:
        logger.info("Base GeoIP indisponible : %s", path)
        return None


def _get_geo_reader(path):
    if not path:
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return _geo_reader(path, stat.st_mtime_ns, stat.st_size)


def _local_ip(ip: str) -> bool:
    try:
        return not ipaddress.ip_address(ip).is_global
    except ValueError:
        return True


def _location(request, ip: str) -> tuple[str, str, str, str, float | None, float | None]:
    """Résout le lieu au moment de la création du log, sans appel réseau."""
    if _local_ip(ip):
        return "", "", "", "", None, None
    if getattr(settings, "TRUST_CLOUDFLARE_HEADERS", False):
        code = request.META.get("HTTP_CF_IPCOUNTRY", "").strip().upper()
        if re.fullmatch(r"[A-Z]{2}", code) and code not in {"XX", "T1"}:
            return (
                _country_name(code), code,
                request.META.get("HTTP_CF_REGION", "")[:100],
                request.META.get("HTTP_CF_IPCITY", "")[:100],
                _float_or_none(request.META.get("HTTP_CF_IPLATITUDE"), -90, 90),
                _float_or_none(request.META.get("HTTP_CF_IPLONGITUDE"), -180, 180),
            )

    path = getattr(settings, "GEOIP_CITY_DB", "") or getattr(settings, "GEOIP_PATH", "")
    if not path:
        return "", "", "", "", None, None
    reader = _get_geo_reader(str(path))
    if reader is None:
        return "", "", "", "", None, None
    try:
        result = reader.city(ip)
        code = (result.country.iso_code or "")[:2]
        country = result.country.names.get("fr") or result.country.name or _country_name(code)
        region = result.subdivisions.most_specific.names.get("fr") or result.subdivisions.most_specific.name or ""
        city = result.city.names.get("fr") or result.city.name or ""
        latitude = _float_or_none(result.location.latitude, -90, 90)
        longitude = _float_or_none(result.location.longitude, -180, 180)
        return country[:80], code, region[:100], city[:100], latitude, longitude
    except Exception:
        return "", "", "", "", None, None


def _float_or_none(value, minimum, maximum):
    try:
        number = float(value)
        return number if math.isfinite(number) and minimum <= number <= maximum else None
    except (TypeError, ValueError):
        return None


def client_details(request, ip: str | None = None) -> dict[str, object]:
    user_agent = _redact_text(request.META.get("HTTP_USER_AGENT", ""))[:512]
    country, country_code, region, city, latitude, longitude = _location(request, ip or client_ip(request))
    return {
        "country": country, "country_code": country_code, "region": region, "city": city,
        "latitude": latitude, "longitude": longitude,
        "operating_system": _operating_system(user_agent), "browser": _browser(user_agent),
        "device": _device(user_agent), "user_agent": user_agent,
    }


def _safe_snapshot(value):
    if isinstance(value, dict):
        return {str(key)[:100]: _safe_snapshot(item) for key, item in value.items() if not _SECRET_KEY.search(str(key))}
    if isinstance(value, (list, tuple)):
        return [_safe_snapshot(item) for item in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _redact_text(value)[:1000] if isinstance(value, str) else value
    return str(value)[:1000]


def snapshot_fields(instance, *fields):
    """Capture quelques champs explicites avant/après une modification."""
    if instance is None:
        return None
    snapshot = {}
    for name in fields:
        try:
            field = instance._meta.get_field(name)
            key = f"{name}_id" if field.is_relation else name
            snapshot[key] = getattr(instance, key)
        except (AttributeError, LookupError):
            continue
    return _safe_snapshot(snapshot)


def _safe_detail(value: str) -> str:
    value = _redact_text(value)
    return value[:300]


def _route(request) -> str:
    match = getattr(request, "resolver_match", None)
    return (getattr(match, "route", "") or request.path_info or "")[:300]


def _role(user) -> str:
    if not user or not getattr(user, "is_authenticated", False):
        return "anonyme"
    if user.is_superuser:
        return "super_admin"
    return "staff" if user.is_staff else "utilisateur"


def _request_role(request, user) -> str:
    role = getattr(request, "class_role", None)
    return role or _role(user)


def _alert_recipients():
    configured = [email for _, email in getattr(settings, "ADMINS", ()) if email]
    if configured:
        return configured
    try:
        from django.contrib.auth import get_user_model

        return list(get_user_model().objects.filter(is_superuser=True, is_active=True).exclude(email="").values_list("email", flat=True))
    except Exception:
        return []


def _distance_km(a: AuditLog, b: AuditLog) -> float:
    rad = math.pi / 180
    lat1, lon1, lat2, lon2 = (a.latitude * rad, a.longitude * rad, b.latitude * rad, b.longitude * rad)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def _alert_text(entry: AuditLog) -> str:
    now = entry.created_at or timezone.now()
    alerts = []
    if entry.action == "connexion_echec":
        latest = list(AuditLog.objects.filter(
            ip=entry.ip, action__in=("connexion", "connexion_echec"),
            created_at__gte=now - timedelta(minutes=15),
        )[:5])
        if len(latest) == 5 and all(item.action == "connexion_echec" for item in latest):
            alerts.append(f"5 échecs de connexion rapprochés depuis {entry.ip} (compte saisi : {entry.detail or 'inconnu'}).")
    if entry.action == "connexion" and entry.user_id and entry.country_code:
        previous_country = AuditLog.objects.filter(
            user_id=entry.user_id, action="connexion", succeeded=True,
        ).exclude(pk=entry.pk).exclude(country_code="").values_list("country_code", flat=True)
        if not previous_country.filter(country_code=entry.country_code).exists():
            alerts.append(f"Première connexion enregistrée depuis {entry.country or entry.country_code} pour {entry.actor_identifier}.")
    if entry.action == "connexion" and entry.user_id and entry.latitude is not None and entry.longitude is not None:
        previous = AuditLog.objects.filter(user_id=entry.user_id, action="connexion", succeeded=True).exclude(pk=entry.pk).exclude(latitude__isnull=True).order_by("-created_at").first()
        if previous and previous.created_at and (now - previous.created_at).total_seconds() > 0:
            hours = (now - previous.created_at).total_seconds() / 3600
            distance = _distance_km(previous, entry)
            if hours <= 8 and distance / hours > 900:
                alerts.append(f"Connexions à environ {distance:.0f} km d’écart en {hours:.1f} h pour {entry.actor_identifier}.")
    if "supprim" in entry.action or "suppression" in entry.action:
        count = re.search(r"\b(\d+)\b", entry.detail)
        if count and int(count.group(1)) >= 10:
            alerts.append(f"Suppression en masse ({count.group(1)} éléments) par {entry.actor_identifier}.")
    if entry.response_status == 403 or entry.action == "acces_refuse":
        denied = AuditLog.objects.filter(
            ip=entry.ip, created_at__gte=now - timedelta(minutes=15),
        ).filter(Q(response_status=403) | Q(action="acces_refuse")).count()
        if denied >= 5:
            alerts.append(f"{denied} accès refusés en 15 minutes depuis {entry.ip}.")
    return "\n".join(alerts)


def _send_alert(entry: AuditLog) -> None:
    message = _alert_text(entry)
    recipients = _alert_recipients() if message else []
    if not recipients:
        return
    try:
        key_source = f"{entry.ip}:{entry.actor_user_id or ''}:{entry.action}:{message.splitlines()[0]}"
        alert_key = "audit-alert:" + hashlib.sha256(key_source.encode()).hexdigest()
        if not cache.add(alert_key, True, timeout=900):
            return
        send_mail(
            subject=f"[Cohorte] Alerte de sécurité : {entry.action}",
            message=f"{message}\n\nAction : {entry.action}\nUtilisateur : {entry.actor_identifier or 'Anonyme'}\nIP : {entry.ip or 'Inconnue'}\nLieu : {entry.city or 'Inconnu'}, {entry.country or 'Inconnu'}\nHeure : {timezone.localtime(entry.created_at):%d/%m/%Y %H:%M:%S}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=True,
        )
    except Exception:
        logger.exception("Échec de l’envoi d’une alerte de sécurité")


def log_action(
    request, action: str, *, user=None, classe=None, detail: str = "", target=None,
    before=None, after=None, succeeded: bool | None = None, response_status: int | None = None,
) -> AuditLog | None:
    """Enregistre une action sensible sans mot de passe, jeton ou donnée bancaire."""
    try:
        actor = user or (request.user if getattr(request, "user", None) and request.user.is_authenticated else None)
        ip = client_ip(request)[:64]
        target = target or classe
        target_model = target if isinstance(target, type) else type(target) if target is not None else None
        target_pk = getattr(target, "pk", None)
        is_success = succeeded if succeeded is not None else action != "connexion_echec"
        if response_status is not None:
            status = response_status
        elif is_success:
            status = 200 if request.method == "GET" or request.headers.get("HX-Request") == "true" else 302
        else:
            status = 401 if action == "connexion_echec" else 403
        entry = AuditLog.objects.create(
            user=actor,
            actor_user_id=getattr(actor, "pk", None) if actor else None,
            actor_identifier=(getattr(actor, "username", "") or "")[:150] if actor else _safe_detail(detail).split(" ")[0][:150],
            actor_role=_request_role(request, actor),
            classe=classe,
            action=action[:40],
            detail=_safe_detail(detail),
            object_type=(target_model._meta.label if target_model else "")[:100],
            object_id=str(target_pk or "")[:100],
            object_label=_safe_detail(str(target))[:200] if target is not None else "",
            path=_route(request),
            response_status=status,
            succeeded=is_success,
            before_data=_safe_snapshot(before) if before is not None else None,
            after_data=_safe_snapshot(after) if after is not None else None,
            ip=ip,
            **client_details(request, ip),
        )
        _send_alert(entry)
        return entry
    except Exception:
        logger.exception("Échec du journal d'audit")
        return None
