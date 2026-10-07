from django.http import JsonResponse
from rest_framework.views import exception_handler


def json_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    code = exc.get_codes() if hasattr(exc, "get_codes") else "error"
    if not isinstance(code, str):
        code = "validation_error"
    response.data = {"error": {"code": code, "message": str(exc), "retryable": False}}
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
