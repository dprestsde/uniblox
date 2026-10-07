import logging

from django.http import JsonResponse
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class DomainError(APIException):
    status_code = 400

    def __init__(self, code, message, *, status=400, retryable=False, details=None):
        self.status_code = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details
        super().__init__(message, code=code)


def json_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        logger.error(
            "unhandled_api_exception",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"operation": "api_request"},
        )
        return Response(
            {
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "retryable": True,
                }
            },
            status=500,
        )
    if isinstance(exc, DomainError):
        error = {
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
        }
        if exc.details is not None:
            error["details"] = exc.details
        response.data = {"error": error}
        return response
    code = exc.get_codes() if hasattr(exc, "get_codes") else "error"
    if not isinstance(code, str):
        code = "validation_error"
    message = "Invalid request." if code == "validation_error" else str(exc)
    response.data = {"error": {"code": code, "message": message, "retryable": False}}
    return response


def api_not_found(request, exception=None):
    return JsonResponse(
        {
            "error": {
                "code": "not_found",
                "message": "API resource not found.",
                "retryable": False,
            }
        },
        status=404,
    )


def unavailable():
    return JsonResponse(
        {
            "error": {
                "code": "service_unavailable",
                "message": "Service is not ready.",
                "retryable": True,
            }
        },
        status=503,
    )
