import uuid
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from fake_payments.models import FakePayment
from store.models import CartItem, Customer, InventoryUnit, Order, PaymentAttempt, Product
from store.services.commerce import checkout
from store.services.worker import run_cycle


class Command(BaseCommand):
    help = (
        "Run isolated success, payment failure, timeout, lost-response, "
        "and recovery demonstrations."
    )

    def handle(self, *args, **options):
        call_command("seed_demo", verbosity=0)
        scenarios = (
            ("success", FakePayment.Outcome.SUCCEEDED, False, ""),
            ("payment-failed", FakePayment.Outcome.FAILED, False, ""),
            ("lost-response", FakePayment.Outcome.SUCCEEDED, True, ""),
            ("failed-confirmation", FakePayment.Outcome.SUCCEEDED, False, "after_success_writes"),
            (
                "failed-failure-finalization",
                FakePayment.Outcome.FAILED,
                False,
                "after_failure_writes",
            ),
            ("timeout-before-processing", FakePayment.Outcome.SUCCEEDED, False, "expired"),
        )
        for label, provider_outcome, lose_response, fault in scenarios:
            suffix = uuid.uuid4().hex[:8]
            customer = Customer.objects.create(
                name=f"Demo {label}", email=f"{label}-{suffix}@example.test"
            )
            product = Product.objects.create(name=f"Demo product {suffix}", price_cents=1000)
            InventoryUnit.objects.create(product=product)
            cart = customer.carts.create()
            CartItem.objects.create(cart=cart, product=product, quantity=1)
            order, _ = checkout(cart.id, None, f"demo-{suffix}")
            attempt = PaymentAttempt.objects.get(order=order)
            if fault == "expired":
                order.reservation.expires_at = timezone.now() - timedelta(seconds=1)
                order.reservation.save(update_fields=["expires_at"])
            else:
                FakePayment.objects.using("payments").create(
                    attempt_id=attempt.provider_key,
                    amount_cents=order.net_cents,
                    currency=order.currency,
                    outcome=provider_outcome,
                    lose_response_once=lose_response,
                )
                if fault:
                    attempt.local_fault = fault
                    attempt.save(update_fields=["local_fault"])
            first = run_cycle(attempt_id=attempt.id)
            attempt.refresh_from_db()
            if order.status == Order.Status.PENDING or first in (["unknown"], ["retry"]):
                PaymentAttempt.objects.filter(id=attempt.id).update(next_retry_at=timezone.now())
                second = run_cycle(attempt_id=attempt.id)
            else:
                second = []
            order.refresh_from_db()
            self.stdout.write(
                f"{label}: before=PENDING first={first} recovery={second} final={order.status}"
            )
