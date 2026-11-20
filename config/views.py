from pathlib import Path

from django.conf import settings
from django.db import connection
from django.http import FileResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def health(request):
    """Sonde légère pour Render / UptimeRobot : vérifie que la base répond."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "error"}, status=503)


@require_GET
def service_worker(request):
    """Sert le service worker à la racine pour qu'il contrôle tout le site."""
    path = Path(settings.BASE_DIR) / "static" / "pwa" / "sw.js"
    response = FileResponse(open(path, "rb"), content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response
