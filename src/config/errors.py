from django.http import JsonResponse
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


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
        return None
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
