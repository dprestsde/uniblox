import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from fake_payments.provider import FakePaymentProvider
from store.exceptions import ServiceError
from store.models import (
    CartItem,
    Customer,
    InventoryUnit,
    Order,
    PaymentAttempt,
    Product,
    RewardProgram,
)
from store.services.order import OrderService
from store.services.payment import PaymentService

NAMESPACE = uuid.UUID("7ed6db86-c7b1-4f1d-b4d0-7c66a45bf81b")
DEFAULT_PRODUCTS = (
    ("Mechanical Keyboard", 8900, 3),
    ("Wireless Mouse", 4500, 5),
    ("USB-C Hub", 6200, 2),
    ("Laptop Stand", 5100, 4),
    ("Scarce Monitor", 25900, 1),
)


class DemoDataService:
    @staticmethod
    def stable_id(name):
        return uuid.uuid5(NAMESPACE, name)

    def seed(self):
        with transaction.atomic():
            program, created = RewardProgram.objects.get_or_create(
                key="default", defaults={"orders_per_coupon": 5, "percentage": 10}
            )
            if not created and (program.orders_per_coupon, program.percentage) != (5, 10):
                raise ServiceError(
                    "reward_program_conflict",
                    "Existing reward program conflicts with n=5, x=10.",
                    status=409,
                )
            for name, email in (
                ("Ada Demo", "ada@example.test"),
                ("Grace Demo", "grace@example.test"),
            ):
                Customer.objects.get_or_create(
                    id=self.stable_id(f"customer:{email}"),
                    defaults={"name": name, "email": email},
                )
            for name, price, quantity in DEFAULT_PRODUCTS:
                product, product_created = Product.objects.get_or_create(
                    id=self.stable_id(f"product:{name}"),
                    defaults={"name": name, "price_cents": price},
                )
                if product_created:
                    InventoryUnit.objects.bulk_create(
                        [InventoryUnit(product=product) for _ in range(quantity)]
                    )

    def reset_inventory(self):
        """Restore available stock for stable demo products without altering order history."""
        results = []
        with transaction.atomic():
            for name, price, target_quantity in DEFAULT_PRODUCTS:
                product, _ = Product.objects.select_for_update().get_or_create(
                    id=self.stable_id(f"product:{name}"),
                    defaults={"name": name, "price_cents": price},
                )
                available_units = list(
                    InventoryUnit.objects.select_for_update()
                    .filter(product=product, status=InventoryUnit.Status.AVAILABLE)
                    .order_by("id")
                )
                current_quantity = len(available_units)
                if current_quantity < target_quantity:
                    InventoryUnit.objects.bulk_create(
                        [
                            InventoryUnit(product=product)
                            for _ in range(target_quantity - current_quantity)
                        ]
                    )
                elif current_quantity > target_quantity:
                    InventoryUnit.objects.filter(
                        id__in=[unit.id for unit in available_units[target_quantity:]]
                    ).delete()
                results.append({"name": name, "available_quantity": target_quantity})
        return results


class DemoScenarioService:
    def __init__(self, *, order_service=None, payment_service=None, clock=None):
        self.clock = clock or timezone.now
        self.order_service = order_service or OrderService(clock=self.clock)
        self.provider = FakePaymentProvider()
        self.payment_service = payment_service or PaymentService(
            provider=self.provider, order_service=self.order_service, clock=self.clock
        )

    def run(self):
        scenarios = (
            ("success", "SUCCEEDED", False, ""),
            ("payment-failed", "FAILED", False, ""),
            ("lost-response", "SUCCEEDED", True, ""),
            ("failed-confirmation", "SUCCEEDED", False, "after_success_writes"),
            (
                "failed-failure-finalization",
                "FAILED",
                False,
                "after_failure_writes",
            ),
            ("timeout-before-processing", "SUCCEEDED", False, "expired"),
        )
        results = []
        for label, provider_outcome, lose_response, fault in scenarios:
            suffix = uuid.uuid4().hex[:8]
            customer = Customer.objects.create(
                name=f"Demo {label}", email=f"{label}-{suffix}@example.test"
            )
            product = Product.objects.create(name=f"Demo product {suffix}", price_cents=1000)
            InventoryUnit.objects.create(product=product)
            cart = customer.carts.create()
            CartItem.objects.create(cart=cart, product=product, quantity=1)
            order_data, _ = self.order_service.checkout(
                cart_id=cart.id, coupon_code=None, idempotency_key=f"demo-{suffix}"
            )
            order = Order.objects.get(id=order_data["id"])
            attempt = PaymentAttempt.objects.get(order=order)
            if fault == "expired":
                order.reservation.expires_at = self.clock() - timedelta(seconds=1)
                order.reservation.save(update_fields=["expires_at"])
            else:
                self.provider.configure(
                    str(attempt.provider_key),
                    order.net_cents,
                    order.currency,
                    provider_outcome,
                    lose_response_once=lose_response,
                )
                if fault:
                    attempt.local_fault = fault
                    attempt.save(update_fields=["local_fault"])
            first = self.payment_service.run_cycle(attempt_id=attempt.id)
            attempt.refresh_from_db()
            if order.status == Order.Status.PENDING or first in (["unknown"], ["retry"]):
                PaymentAttempt.objects.filter(id=attempt.id).update(next_retry_at=self.clock())
                second = self.payment_service.run_cycle(attempt_id=attempt.id)
            else:
                second = []
            order.refresh_from_db()
            results.append(
                f"{label}: before=PENDING first={first} recovery={second} final={order.status}"
            )
        return results
