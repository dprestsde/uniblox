import hashlib
import json

from store.exceptions import ServiceError
from store.models import IdempotencyRecord


def fingerprint(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyService:
    def find_replay(self, operation, scope, key, request_fingerprint):
        record = IdempotencyRecord.objects.filter(operation=operation, scope=scope, key=key).first()
        if not record:
            return None
        if record.request_fingerprint != request_fingerprint:
            raise ServiceError(
                "idempotency_key_conflict",
                "The idempotency key was already used with different input.",
                status=409,
            )
        return record

    def record(
        self,
        *,
        operation,
        scope,
        key,
        request_fingerprint,
        resource_type,
        resource_id,
        response_status,
        response_snapshot=None,
    ):
        return IdempotencyRecord.objects.create(
            operation=operation,
            scope=scope,
            key=key,
            request_fingerprint=request_fingerprint,
            resource_type=resource_type,
            resource_id=resource_id,
            response_status=response_status,
            response_snapshot=response_snapshot or {},
        )
