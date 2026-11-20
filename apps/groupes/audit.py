import logging

from .models import AuditLog
from .throttle import client_ip

logger = logging.getLogger(__name__)


def log_action(request, action: str, *, user=None, classe=None, detail: str = "") -> None:
    """Enregistre une action sensible. Ne doit jamais faire échouer la requête."""
    try:
        actor = user or (request.user if getattr(request, "user", None) and request.user.is_authenticated else None)
        AuditLog.objects.create(
            user=actor, classe=classe, action=action[:40], detail=detail[:300], ip=client_ip(request)[:64],
        )
    except Exception:  # pragma: no cover - le journal est un bonus
        logger.exception("Échec du journal d'audit")
