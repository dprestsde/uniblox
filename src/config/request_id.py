import contextvars
import logging
import uuid

request_id = contextvars.ContextVar("request_id", default=None)
logger = logging.getLogger(__name__)


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Generate locally; do not trust caller-controlled text in structured logs.
        identifier = uuid.uuid4().hex
        token = request_id.set(identifier)
        request.request_id = identifier
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = identifier
            logger.info(
                "request_complete", extra={"path": request.path, "status": response.status_code}
            )
            return response
        finally:
            request_id.reset(token)
