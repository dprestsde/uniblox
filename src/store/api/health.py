import logging

from rest_framework.views import APIView

from store.api.serializers import HealthSerializer
from store.api.views import validated_response
from store.exceptions import ServiceError
from store.services.health import HealthService

logger = logging.getLogger(__name__)


class LiveHealthView(APIView):
    def get(self, request):
        return validated_response(HealthSerializer, HealthService.liveness())


class ReadyHealthView(APIView):
    def get(self, request):
        try:
            result = HealthService().readiness()
        except Exception as exc:
            logger.warning("readiness_check_failed")
            raise ServiceError(
                "service_unavailable", "Service is not ready.", status=503, retryable=True
            ) from exc
        return validated_response(HealthSerializer, result)


class ApiNotFoundView(APIView):
    def not_found(self, request, *args, **kwargs):
        raise ServiceError("not_found", "API resource not found.", status=404)

    get = not_found
    post = not_found
    put = not_found
    patch = not_found
    delete = not_found
