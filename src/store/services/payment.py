import logging
import uuid
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone

from fake_payments.provider import FakePaymentProvider
from store.models import Order, PaymentAttempt, Reservation
from store.payments.provider import PaymentStatus
from store.services.order import OrderService

logger = logging.getLogger(__name__)
LEASE_SECONDS = 30
MAX_BATCH = 10


class PaymentService:
    def __init__(self, *, provider=None, order_service=None, clock=None):
        self.provider = provider or FakePaymentProvider()
        self.clock = clock or timezone.now
        self.order_service = order_service or OrderService(clock=self.clock)

    @staticmethod
    def _backoff(attempt):
        base = min(2 ** max(attempt.attempts - 1, 0), 60)
        jitter = (attempt.id.int % 1000) / 1000
        return timedelta(seconds=min(base + jitter, 60))

    def claim_due_attempt(self, attempt_id=None, now=None):
        now = now or self.clock()
        with transaction.atomic():
            query = (
                PaymentAttempt.objects.select_for_update(skip_locked=True, of=("self",))
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

    @staticmethod
    def _owned_attempt(attempt_id, token, *, lock=False):
        query = PaymentAttempt.objects
        if lock:
            query = query.select_for_update(of=("self",))
        return query.select_related("order").filter(id=attempt_id, lease_token=token).first()

    def _reschedule(self, attempt_id, token, message, now=None):
        now = now or self.clock()
        with transaction.atomic():
            attempt = self._owned_attempt(attempt_id, token, lock=True)
            if not attempt:
                return False
            attempt.last_error = message[:200]
            attempt.next_retry_at = now + self._backoff(attempt)
            attempt.lease_token = None
            attempt.lease_deadline = None
            attempt.save(
                update_fields=["last_error", "next_retry_at", "lease_token", "lease_deadline"]
            )
            return True

    @staticmethod
    def _finish_lease(attempt_id, token):
        PaymentAttempt.objects.filter(id=attempt_id, lease_token=token).update(
            lease_token=None, lease_deadline=None, last_error=""
        )

    def process_claim(self, claim, now=None):
        attempt_id, token = claim
        now = now or self.clock()
        try:
            with transaction.atomic():
                attempt = self._owned_attempt(attempt_id, token, lock=True)
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
                self.order_service.finalize(
                    expired_order, succeeded=False, failure_code="reservation_expired"
                )
                self._finish_lease(attempt_id, token)
                return "failed"

            attempt = self._owned_attempt(attempt_id, token)
            if not attempt:
                return "stale"
            if attempt.outcome == PaymentAttempt.Outcome.SUCCEEDED:
                result_status = PaymentStatus.SUCCEEDED
            elif attempt.outcome == PaymentAttempt.Outcome.FAILED:
                result_status = PaymentStatus.FAILED
            else:
                result = self.provider.get_status(str(attempt.provider_key))
                if result.status == PaymentStatus.NOT_FOUND:
                    result = self.provider.pay(
                        str(attempt.provider_key), attempt.order.net_cents, attempt.order.currency
                    )
                result_status = result.status
                if result_status in (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED):
                    with transaction.atomic():
                        owned = self._owned_attempt(attempt_id, token, lock=True)
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
                self._reschedule(attempt_id, token, "provider outcome unknown", now)
                return "unknown"

            with transaction.atomic():
                owned = self._owned_attempt(attempt_id, token, lock=True)
                if not owned:
                    return "stale"
                fault = owned.local_fault
                if fault:
                    owned.local_fault = ""
                    owned.save(update_fields=["local_fault"])
            self.order_service.finalize(
                attempt.order_id,
                succeeded=result_status == PaymentStatus.SUCCEEDED,
                fault=fault or None,
            )
            self._finish_lease(attempt_id, token)
            return "confirmed" if result_status == PaymentStatus.SUCCEEDED else "failed"
        except Exception as exc:
            logger.exception(
                "payment_attempt_processing_failed",
                extra={"attempt_id": str(attempt_id), "operation": "process_payment_attempt"},
            )
            try:
                self._reschedule(attempt_id, token, str(exc), now)
            except DatabaseError:
                logger.exception(
                    "payment_attempt_reschedule_failed",
                    extra={"attempt_id": str(attempt_id), "operation": "reschedule"},
                )
                raise
            if isinstance(exc, DatabaseError):
                raise
            return "retry"

    def run_cycle(self, attempt_id=None, now=None, limit=MAX_BATCH):
        outcomes = []
        for _ in range(1 if attempt_id else limit):
            claim = self.claim_due_attempt(attempt_id=attempt_id, now=now)
            if not claim:
                break
            outcomes.append(self.process_claim(claim, now=now))
        current = now or self.clock()
        old = current - timedelta(minutes=15)
        PaymentAttempt.objects.filter(
            order__status=Order.Status.PENDING,
            order__created_at__lt=old,
            flagged_at__isnull=True,
        ).update(flagged_at=current)
        return outcomes
