import logging
import uuid
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone

from fake_payments.provider import FakePaymentProvider
from store.models import Order, PaymentAttempt, Reservation
from store.payments.provider import PaymentStatus
from store.services.commerce import finalize_order

logger = logging.getLogger(__name__)
LEASE_SECONDS = 30
MAX_BATCH = 10


def _backoff(attempt):
    base = min(2 ** max(attempt.attempts - 1, 0), 60)
    jitter = (attempt.id.int % 1000) / 1000
    return timedelta(seconds=min(base + jitter, 60))


def claim_due_attempt(attempt_id=None, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        query = (
            PaymentAttempt.objects.select_for_update(skip_locked=True)
            .filter(order__status=Order.Status.PENDING, next_retry_at__lte=now)
            .filter(Q(lease_deadline__isnull=True) | Q(lease_deadline__lt=now))
            .order_by("next_retry_at", "id")
        )
        if attempt_id:
            query = query.filter(id=attempt_id)
        attempt = query.first()
        if not attempt:
            return None
        attempt.lease_token = uuid.uuid4()
        attempt.lease_deadline = now + timedelta(seconds=LEASE_SECONDS)
        attempt.attempts += 1
        if attempt.attempts >= 5 and not attempt.flagged_at:
            attempt.flagged_at = now
        attempt.save(update_fields=["lease_token", "lease_deadline", "attempts", "flagged_at"])
        return attempt.id, attempt.lease_token


def _owned_attempt(attempt_id, token, *, lock=False):
    query = PaymentAttempt.objects
    if lock:
        query = query.select_for_update()
    return query.select_related("order").filter(id=attempt_id, lease_token=token).first()


def _reschedule(attempt_id, token, message, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        attempt = _owned_attempt(attempt_id, token, lock=True)
        if not attempt:
            return False
        attempt.last_error = message[:200]
        attempt.next_retry_at = now + _backoff(attempt)
        attempt.lease_token = None
        attempt.lease_deadline = None
        attempt.save(update_fields=["last_error", "next_retry_at", "lease_token", "lease_deadline"])
        return True


def process_claim(claim, provider=None, now=None):
    attempt_id, token = claim
    provider = provider or FakePaymentProvider()
    now = now or timezone.now()
    try:
        with transaction.atomic():
            attempt = _owned_attempt(attempt_id, token, lock=True)
            if not attempt:
                return "stale"
            reservation = Reservation.objects.select_for_update().get(order=attempt.order)
            if not attempt.initiated_at and reservation.expires_at <= now:
                attempt.outcome = PaymentAttempt.Outcome.FAILED
                attempt.provider_reference = "expired-before-initiation"
                attempt.save(update_fields=["outcome", "provider_reference"])
                expired_order = attempt.order_id
            else:
                expired_order = None
                if not attempt.initiated_at:
                    attempt.initiated_at = now
                    attempt.save(update_fields=["initiated_at"])
        if expired_order:
            finalize_order(expired_order, succeeded=False, failure_code="reservation_expired")
            _finish_lease(attempt_id, token)
            return "failed"

        attempt = _owned_attempt(attempt_id, token)
        if not attempt:
            return "stale"
        if attempt.outcome == PaymentAttempt.Outcome.SUCCEEDED:
            result_status = PaymentStatus.SUCCEEDED
        elif attempt.outcome == PaymentAttempt.Outcome.FAILED:
            result_status = PaymentStatus.FAILED
        else:
            result = provider.get_status(str(attempt.provider_key))
            if result.status == PaymentStatus.NOT_FOUND:
                result = provider.pay(
                    str(attempt.provider_key), attempt.order.net_cents, attempt.order.currency
                )
            result_status = result.status
            if result_status in (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED):
                with transaction.atomic():
                    owned = _owned_attempt(attempt_id, token, lock=True)
                    if not owned:
                        return "stale"
                    owned.outcome = (
                        PaymentAttempt.Outcome.SUCCEEDED
                        if result_status == PaymentStatus.SUCCEEDED
                        else PaymentAttempt.Outcome.FAILED
                    )
                    owned.provider_reference = result.provider_reference or ""
                    owned.save(update_fields=["outcome", "provider_reference"])
        if result_status in (PaymentStatus.UNKNOWN, PaymentStatus.NOT_FOUND):
            _reschedule(attempt_id, token, "provider outcome unknown", now)
            return "unknown"

        with transaction.atomic():
            owned = _owned_attempt(attempt_id, token, lock=True)
            if not owned:
                return "stale"
            fault = owned.local_fault
            if fault:
                owned.local_fault = ""
                owned.save(update_fields=["local_fault"])
        finalize_order(
            attempt.order_id,
            succeeded=result_status == PaymentStatus.SUCCEEDED,
            fault=fault or None,
        )
        _finish_lease(attempt_id, token)
        return "confirmed" if result_status == PaymentStatus.SUCCEEDED else "failed"
    except Exception as exc:  # recovery must retain the known provider result
        logger.exception(
            "payment_attempt_processing_failed",
            extra={"attempt_id": str(attempt_id), "operation": "process_payment_attempt"},
        )
        try:
            _reschedule(attempt_id, token, str(exc), now)
        except DatabaseError:
            logger.exception(
                "payment_attempt_reschedule_failed",
                extra={"attempt_id": str(attempt_id), "operation": "reschedule"},
            )
            raise
        if isinstance(exc, DatabaseError):
            raise
        return "retry"


def _finish_lease(attempt_id, token):
    PaymentAttempt.objects.filter(id=attempt_id, lease_token=token).update(
        lease_token=None, lease_deadline=None, last_error=""
    )


def run_cycle(attempt_id=None, provider=None, now=None, limit=MAX_BATCH):
    outcomes = []
    for _ in range(1 if attempt_id else limit):
        claim = claim_due_attempt(attempt_id=attempt_id, now=now)
        if not claim:
            break
        outcomes.append(process_claim(claim, provider=provider, now=now))
    old = (now or timezone.now()) - timedelta(minutes=15)
    PaymentAttempt.objects.filter(
        order__status=Order.Status.PENDING, order__created_at__lt=old, flagged_at__isnull=True
    ).update(flagged_at=now or timezone.now())
    return outcomes
