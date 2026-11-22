import logging
import re

from django.conf import settings

from .models import AuditLog
from .throttle import client_ip

logger = logging.getLogger(__name__)

COUNTRY_NAMES = {
    "BJ": "Bénin", "BF": "Burkina Faso", "CI": "Côte d’Ivoire", "CM": "Cameroun",
    "DE": "Allemagne", "FR": "France", "GH": "Ghana", "GN": "Guinée",
    "GB": "Royaume-Uni", "ML": "Mali", "NE": "Niger", "NG": "Nigeria",
    "SN": "Sénégal", "TG": "Togo", "US": "États-Unis",
}


def _version(pattern: str, user_agent: str) -> str:
    match = re.search(pattern, user_agent, re.IGNORECASE)
    return match.group(1) if match else ""


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
        android_version = _version(r"Android[ /-]([\d.]+)", user_agent)
        return f"Android {android_version}".strip()
    if re.search(r"iPhone|iPad|iPod", user_agent, re.IGNORECASE):
        version = _version(r"(?:iPhone )?OS ([\d_]+)", user_agent).replace("_", ".")
        return f"iOS {version}" if version else "iOS"
    if re.search(r"Windows NT", user_agent, re.IGNORECASE):
        match = re.search(r"Windows NT ([\d.]+)", user_agent, re.IGNORECASE)
        version = match.group(1) if match else ""
        windows_versions = {"10.0": "10/11", "6.3": "8.1", "6.2": "8", "6.1": "7", "6.0": "Vista", "5.1": "XP"}
        return f"Windows {windows_versions.get(version, version)}".strip()
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


def _country(request) -> tuple[str, str]:
    """Utilise le pays Cloudflare uniquement si son en-tête est explicitement approuvé."""
    if not getattr(settings, "TRUST_CLOUDFLARE_HEADERS", False):
        return "", ""
    code = request.META.get("HTTP_CF_IPCOUNTRY", "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", code) or code in {"XX", "T1"}:
        return "", ""
    return COUNTRY_NAMES.get(code, code), code


def client_details(request) -> dict[str, str]:
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:512]
    country, country_code = _country(request)
    return {
        "country": country,
        "country_code": country_code,
        "operating_system": _operating_system(user_agent),
        "browser": _browser(user_agent),
        "device": _device(user_agent),
        "user_agent": user_agent,
    }


def log_action(request, action: str, *, user=None, classe=None, detail: str = "") -> None:
    """Enregistre une action sensible. Ne doit jamais faire échouer la requête."""
    try:
        actor = user or (request.user if getattr(request, "user", None) and request.user.is_authenticated else None)
        AuditLog.objects.create(
            user=actor, classe=classe, action=action[:40], detail=detail[:300], ip=client_ip(request)[:64],
            **client_details(request),
        )
    except Exception:  # pragma: no cover - le journal est un bonus
        logger.exception("Échec du journal d'audit")
