from datetime import timedelta
from threading import Barrier, Thread

from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from config.errors import DomainError
from fake_payments.models import FakePayment
from store.models import (
    Cart,
    CartItem,
    Coupon,
    Customer,
    InventoryUnit,
    Order,
    PaymentAttempt,
    Product,
    RewardProgram,
)
from store.services.commerce import add_cart_item, checkout
from store.services.worker import run_cycle


class CommerceTestCase(TransactionTestCase):
    databases = {"default", "payments"}

    def setUp(self):
        self.client = APIClient()
        self.customer = Customer.objects.create(name="Ada", email="ada@test.example")
        self.product = Product.objects.create(name="Keyboard", price_cents=1001)
        InventoryUnit.objects.bulk_create([InventoryUnit(product=self.product) for _ in range(3)])
        RewardProgram.objects.create(key="default", orders_per_coupon=1, percentage=10)

    def add(self, quantity=1, key="add-1"):
        return self.client.post(
            f"/api/v1/customers/{self.customer.id}/cart/items",
            {"product_id": str(self.product.id), "quantity": quantity},
            format="json",
            headers={"Idempotency-Key": key},
        )

    def checkout(self, key="checkout-1", coupon_code=None):
        cart = self.customer.carts.get(status="OPEN")
        body = {"coupon_code": coupon_code} if coupon_code else {}
        return self.client.post(
            f"/api/v1/carts/{cart.id}/checkout",
            body,
            format="json",
            headers={"Idempotency-Key": key},
        )

    def test_customer_normalization_duplicate_and_read_only_count(self):
        response = self.client.post(
            "/api/v1/customers",
            {"name": " Grace ", "email": " GRACE@EXAMPLE.TEST "},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["email"], "grace@example.test")
        duplicate = self.client.post(
            "/api/v1/customers",
            {"name": "Other", "email": "grace@example.test"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 409)
        read_only = self.client.post(
            "/api/v1/customers",
            {"name": "Other", "email": "other@example.test", "orders_count": 9},
            format="json",
        )
        self.assertEqual(read_only.status_code, 400)

    def test_add_is_idempotent_and_rejects_invalid_or_excess_quantity(self):
        first = self.add(2)
        replay = self.add(2)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data["items"][0]["quantity"], 2)
        conflict = self.add(1)
        self.assertEqual(conflict.status_code, 409)
        shortage = self.add(2, "add-2")
        self.assertEqual(shortage.status_code, 409)
        self.assertEqual(shortage.data["error"]["details"]["available"], 3)
        invalid = self.add(True, "add-3")
        self.assertEqual(invalid.status_code, 400)

    def test_checkout_reserves_snapshot_and_replays_terminal_order(self):
        self.add(1)
        response = self.checkout()
        self.assertEqual(response.status_code, 202)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.gross_cents, 1001)
        self.assertEqual(order.reservation.units.filter(status="RESERVED").count(), 1)
        self.product.price_cents = 9999
        self.product.save(update_fields=["price_cents"])
        self.assertEqual(self.client.get(f"/api/v1/orders/{order.id}").data["gross_cents"], 1001)
        run_cycle(attempt_id=order.payment_attempt.id)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.orders_count, 1)
        replay = self.client.post(
            f"/api/v1/carts/{order.cart_id}/checkout",
            {},
            format="json",
            headers={"Idempotency-Key": "checkout-1"},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data["id"], str(order.id))

    def test_payment_failure_releases_inventory_and_coupon(self):
        coupon = Coupon.objects.create(
            program=RewardProgram.objects.get(),
            code="SAVE10",
            percentage=10,
            milestone=1,
        )
        self.add(1)
        response = self.checkout(coupon_code=coupon.code)
        order = Order.objects.get(id=response.data["id"])
        FakePayment.objects.using("payments").create(
            attempt_id=order.payment_attempt.provider_key,
            amount_cents=order.net_cents,
            currency="USD",
            outcome=FakePayment.Outcome.FAILED,
        )
        run_cycle(attempt_id=order.payment_attempt.id)
        order.refresh_from_db()
        coupon.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FAILED)
        self.assertEqual(coupon.status, Coupon.Status.AVAILABLE)
        self.assertIsNone(coupon.owner_order_id)
        self.assertEqual(InventoryUnit.objects.filter(status="AVAILABLE").count(), 3)

    def test_lost_provider_response_and_failed_local_confirmation_recover(self):
        self.add(1)
        response = self.checkout()
        order = Order.objects.get(id=response.data["id"])
        attempt = order.payment_attempt
        FakePayment.objects.using("payments").create(
            attempt_id=attempt.provider_key,
            amount_cents=order.net_cents,
            currency="USD",
            outcome=FakePayment.Outcome.SUCCEEDED,
            lose_response_once=True,
        )
        self.assertEqual(run_cycle(attempt_id=attempt.id), ["unknown"])
        self.assertTrue(
            FakePayment.objects.using("payments").filter(attempt_id=attempt.provider_key).exists()
        )
        attempt.refresh_from_db()
        attempt.next_retry_at = timezone.now()
        attempt.local_fault = "fail-after-writes"
        attempt.save(update_fields=["next_retry_at", "local_fault"])
        self.assertEqual(run_cycle(attempt_id=attempt.id), ["retry"])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.reservation.units.filter(status="RESERVED").count(), 1)
        PaymentAttempt.objects.filter(id=attempt.id).update(next_retry_at=timezone.now())
        self.assertEqual(run_cycle(attempt_id=attempt.id), ["confirmed"])

    def test_expiry_before_initiation_does_not_call_provider(self):
        self.add(1)
        response = self.checkout()
        order = Order.objects.get(id=response.data["id"])
        order.reservation.expires_at = timezone.now() - timedelta(seconds=1)
        order.reservation.save(update_fields=["expires_at"])
        self.assertEqual(run_cycle(attempt_id=order.payment_attempt.id), ["failed"])
        order.refresh_from_db()
        self.assertEqual(order.failure_code, "reservation_expired")
        self.assertFalse(FakePayment.objects.using("payments").exists())

    def test_reward_rounding_generation_and_report(self):
        self.add(1)
        response = self.checkout()
        order = Order.objects.get(id=response.data["id"])
        run_cycle(attempt_id=order.payment_attempt.id)
        generated = self.client.post(
            "/api/v1/admin/coupons/generate",
            {},
            format="json",
            headers={"Idempotency-Key": "reward-1"},
        )
        self.assertEqual(generated.status_code, 201)
        replay = self.client.post(
            "/api/v1/admin/coupons/generate",
            {},
            format="json",
            headers={"Idempotency-Key": "reward-1"},
        )
        self.assertEqual(replay.status_code, 200)
        report = self.client.get("/api/v1/admin/reports/summary")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.data["gross_revenue_cents"], 1001)
        self.assertEqual(
            report.data["gross_revenue_cents"] - report.data["discounts_cents"],
            report.data["net_revenue_cents"],
        )

    def test_new_purchase_gets_a_new_open_cart(self):
        self.add(1)
        first_cart = self.customer.carts.get(status="OPEN")
        self.checkout()
        second = self.add(1, "new-cart-add")
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(second.data["id"], str(first_cart.id))
        self.assertEqual(self.customer.carts.filter(status="OPEN").count(), 1)


class MoneyRoundingTest(TransactionTestCase):
    databases = {"default", "payments"}

    def test_half_up_rounding_is_applied_once_to_subtotal(self):
        customer = Customer.objects.create(name="Rounding", email="rounding@test.example")
        product = Product.objects.create(name="Cent item", price_cents=5)
        InventoryUnit.objects.create(product=product)
        cart = customer.carts.create()
        CartItem.objects.create(cart=cart, product=product, quantity=1)
        program = RewardProgram.objects.create(key="default", orders_per_coupon=1, percentage=10)
        coupon = Coupon.objects.create(program=program, code="TEN", percentage=10, milestone=1)
        order, _ = checkout(cart.id, coupon.code, "rounding-checkout")
        self.assertEqual(order.discount_cents, 1)
        self.assertEqual(order.net_cents, 4)


class CommerceConcurrencyTest(TransactionTestCase):
    databases = {"default", "payments"}
    reset_sequences = True

    def test_last_unit_competition_has_one_winner(self):
        product = Product.objects.create(name="Last unit", price_cents=100)
        InventoryUnit.objects.create(product=product)
        carts = []
        for number in range(2):
            customer = Customer.objects.create(
                name=f"Buyer {number}", email=f"buyer-{number}@test.example"
            )
            cart = customer.carts.create()
            CartItem.objects.create(cart=cart, product=product, quantity=1)
            carts.append(cart)
        barrier = Barrier(2)
        results = []

        def run(cart, key):
            close_old_connections()
            barrier.wait()
            try:
                order, _ = checkout(cart.id, None, key)
                results.append(("order", order.id))
            except DomainError as exc:
                results.append((exc.code, None))
            finally:
                close_old_connections()

        threads = [
            Thread(target=run, args=(cart, f"race-{index}")) for index, cart in enumerate(carts)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(sum(result[0] == "order" for result in results), 1)
        self.assertEqual(sum(result[0] == "insufficient_inventory" for result in results), 1)
        self.assertEqual(
            InventoryUnit.objects.filter(status=InventoryUnit.Status.RESERVED).count(), 1
        )

    def test_concurrent_first_additions_share_one_open_cart(self):
        customer = Customer.objects.create(name="One Cart", email="one-cart@test.example")
        products = [
            Product.objects.create(name=f"Product {number}", price_cents=100) for number in range(2)
        ]
        for product in products:
            InventoryUnit.objects.create(product=product)
        barrier = Barrier(2)
        results = []

        def run(product, key):
            close_old_connections()
            barrier.wait()
            item, _ = add_cart_item(customer.id, product.id, 1, key)
            results.append(item.cart_id)
            close_old_connections()

        threads = [
            Thread(target=run, args=(product, f"first-{index}"))
            for index, product in enumerate(products)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(customer.carts.filter(status=Cart.Status.OPEN).count(), 1)
