from django.core.cache import cache


def too_many(request, scope: str, limit: int, window: int = 60) -> bool:
    """Limiteur simple par IP. Derrière un proxy, configure-le pour transmettre la vraie IP."""
    ip = request.META.get("REMOTE_ADDR", "?")
    key = f"rl:{scope}:{ip}"
    cache.add(key, 0, window)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        count = 1
    return count > limit
