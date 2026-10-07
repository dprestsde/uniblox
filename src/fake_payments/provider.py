from django.db import transaction

from fake_payments.models import FakePayment
from store.payments.provider import PaymentResult, PaymentStatus


class FakePaymentProvider:
    """A durable provider simulator whose writes use the independent payments database."""

    def pay(self, attempt_id, amount_minor, currency):
        with transaction.atomic(using="payments"):
            payment, _ = FakePayment.objects.using("payments").get_or_create(
                attempt_id=attempt_id,
                defaults={
                    "amount_cents": amount_minor,
                    "currency": currency,
                    "outcome": FakePayment.Outcome.SUCCEEDED,
                },
            )
            if payment.amount_cents != amount_minor or payment.currency != currency:
                raise ValueError("provider key was reused with a different amount or currency")
            if payment.lose_response_once:
                payment.lose_response_once = False
                payment.save(using="payments", update_fields=["lose_response_once"])
                return PaymentResult(PaymentStatus.UNKNOWN)
            return self._result(payment)

    def get_status(self, attempt_id):
        payment = FakePayment.objects.using("payments").filter(attempt_id=attempt_id).first()
        if not payment:
            return PaymentResult(PaymentStatus.NOT_FOUND)
        if payment.lose_response_once:
            return PaymentResult(PaymentStatus.NOT_FOUND)
        return self._result(payment)

    def configure(self, attempt_id, amount_minor, currency, outcome, *, lose_response_once=False):
        return FakePayment.objects.using("payments").create(
            attempt_id=attempt_id,
            amount_cents=amount_minor,
            currency=currency,
            outcome=outcome,
            lose_response_once=lose_response_once,
        )

    @staticmethod
    def _result(payment):
        status = (
            PaymentStatus.SUCCEEDED
            if payment.outcome == FakePayment.Outcome.SUCCEEDED
            else PaymentStatus.FAILED
        )
        return PaymentResult(status, str(payment.provider_reference))
