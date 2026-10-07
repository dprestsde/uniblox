import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler

from store.exceptions import OutputContractError, ServiceError

logger = logging.getLogger(__name__)


def json_exception_handler(exc, context):
    if isinstance(exc, OutputContractError):
        logger.error("output_contract_error", extra={"operation": "api_response"})
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
    if isinstance(exc, ServiceError):
        error = {
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
        }
        if exc.details is not None:
            error["details"] = exc.details
        return Response({"error": error}, status=exc.status)
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
    code = exc.get_codes() if hasattr(exc, "get_codes") else "error"
    if not isinstance(code, str):
        code = "validation_error"
    message = "Invalid request." if code == "validation_error" else str(exc)
    error = {"code": code, "message": message, "retryable": False}
    if code == "validation_error":
        error["details"] = response.data
    response.data = {"error": error}
    return response
