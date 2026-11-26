from django.conf import settings
from django.core.cache import cache
import ipaddress


def client_ip(request) -> str:
    """IP réelle du client.

    Derrière N proxys de confiance (TRUSTED_PROXY_COUNT), la vraie IP est la N-ième en partant
    de la droite dans X-Forwarded-For : les valeurs ajoutées par le client à gauche sont ignorées.
    """
    if getattr(settings, "TRUST_CLOUDFLARE_HEADERS", False):
        candidate = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    proxies = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    if proxies > 0:
        parts = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
        if len(parts) >= proxies:
            return parts[-proxies]
    return request.META.get("REMOTE_ADDR", "?")


def too_many(request, scope: str, limit: int, window: int = 60) -> bool:
    """Limiteur simple par IP (cache partagé entre workers)."""
    key = f"rl:{scope}:{client_ip(request)}"
    cache.add(key, 0, window)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        count = 1
    return count > limit


def too_many_key(key: str, limit: int, window: int = 60) -> bool:
    """Limiteur par clé libre (ex. nom d'utilisateur), pour freiner le bourrage d'identifiants."""
    key = f"rlk:{key}"[:200]
    cache.add(key, 0, window)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        count = 1
    return count > limit
