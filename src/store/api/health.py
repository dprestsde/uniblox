import logging

from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from config.errors import unavailable

logger = logging.getLogger(__name__)


@require_GET
def live(request):
    return JsonResponse({"status": "live"})


@require_GET
def ready(request):
    try:
        for alias in ("default", "payments"):
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
    except Exception:
        logger.warning("readiness_check_failed")
        return unavailable()
    return JsonResponse({"status": "ready"})
