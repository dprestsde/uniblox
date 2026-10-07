from datetime import timedelta
from threading import Barrier, Event, Thread
from unittest.mock import Mock, patch

from django.db import DatabaseError, close_old_connections, transaction
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

    def test_legacy_add_replay_without_snapshot_returns_conflict(self):
        self.assertEqual(self.add(1, "legacy-add").status_code, 201)
        from store.models import IdempotencyRecord

        IdempotencyRecord.objects.filter(operation="cart.add", key="legacy-add").update(
            response_snapshot={}
        )
        replay = self.add(1, "legacy-add")
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.data["error"]["code"], "idempotency_result_unavailable")
        self.assertEqual(CartItem.objects.get().quantity, 1)

    def test_add_replay_returns_immutable_snapshot_after_cart_changes(self):
        first = self.add(1, "immutable-add")
        original = first.json()
        cart_id = original["id"]

        self.assertEqual(self.add(1, "second-add").status_code, 201)
        replay = self.add(1, "immutable-add")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), original)
        self.assertEqual(CartItem.objects.get(cart_id=cart_id).quantity, 2)

        updated = self.client.patch(
            f"/api/v1/carts/{cart_id}/items/{self.product.id}",
            {"quantity": 3},
            format="json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.add(1, "immutable-add").json(), original)

        removed = self.client.delete(f"/api/v1/carts/{cart_id}/items/{self.product.id}")
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(self.add(1, "immutable-add").json(), original)
        self.assertFalse(CartItem.objects.filter(cart_id=cart_id).exists())

        self.assertEqual(self.add(1, "replacement-add").status_code, 201)
        self.assertEqual(self.checkout("rollover-checkout").status_code, 202)
        new_cart = self.add(1, "new-cart-add")
        self.assertNotEqual(new_cart.data["id"], cart_id)
        rollover_replay = self.add(1, "immutable-add")
        self.assertEqual(rollover_replay.json(), original)
        self.assertEqual(self.customer.carts.filter(status=Cart.Status.OPEN).count(), 1)

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
        coupon = Coupon.objects.create(
            program=RewardProgram.objects.get(),
            code="RECOVER10",
            percentage=10,
            milestone=1,
        )
        self.add(1)
        response = self.checkout(coupon_code=coupon.code)
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
        with self.assertLogs("store.services.worker", level="ERROR") as captured:
            self.assertEqual(run_cycle(attempt_id=attempt.id), ["retry"])
        self.assertEqual(captured.records[0].attempt_id, str(attempt.id))
        self.assertIsNotNone(captured.records[0].exc_info)
        self.assertIn("fail-after-writes", str(captured.records[0].exc_info[1]))
        order.refresh_from_db()
        coupon.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.reservation.units.filter(status="RESERVED").count(), 1)
        self.assertEqual(coupon.status, Coupon.Status.RESERVED)
        self.assertEqual(coupon.owner_order_id, order.id)
        PaymentAttempt.objects.filter(id=attempt.id).update(next_retry_at=timezone.now())
        self.assertEqual(run_cycle(attempt_id=attempt.id), ["confirmed"])
        coupon.refresh_from_db()
        self.assertEqual(coupon.status, Coupon.Status.REDEEMED)

    def test_zero_total_recovery_never_calls_provider(self):
        coupon = Coupon.objects.create(
            program=RewardProgram.objects.get(),
            code="FREE100",
            percentage=100,
            milestone=1,
        )
        self.add(1)
        cart = self.customer.carts.get(status=Cart.Status.OPEN)
        with patch(
            "store.services.commerce.finalize_order", side_effect=RuntimeError("local fault")
        ):
            with self.assertRaises(RuntimeError):
                checkout(cart.id, coupon.code, "zero-total-checkout")

        order = Order.objects.get(cart=cart)
        attempt = order.payment_attempt
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(attempt.outcome, PaymentAttempt.Outcome.SUCCEEDED)
        self.assertIsNotNone(attempt.initiated_at)
        self.assertEqual(attempt.provider_reference, "zero-total")

        provider = Mock()
        self.assertEqual(run_cycle(attempt_id=attempt.id, provider=provider), ["confirmed"])
        provider.pay.assert_not_called()
        provider.get_status.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CONFIRMED)

    def test_payment_attempt_database_error_propagates_after_reschedule(self):
        self.add(1)
        response = self.checkout()
        order = Order.objects.get(id=response.data["id"])
        provider = Mock()
        provider.get_status.side_effect = DatabaseError("provider database unavailable")

        with self.assertRaises(DatabaseError):
            run_cycle(attempt_id=order.payment_attempt.id, provider=provider)

        order.payment_attempt.refresh_from_db()
        self.assertIsNone(order.payment_attempt.lease_token)
        self.assertIn("provider database unavailable", order.payment_attempt.last_error)

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
            snapshot, _ = add_cart_item(customer.id, product.id, 1, key)
            results.append(snapshot["id"])
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

    def test_two_customers_competing_for_one_coupon_have_one_winner(self):
        program = RewardProgram.objects.create(key="default", orders_per_coupon=1, percentage=10)
        coupon = Coupon.objects.create(program=program, code="ONLYONE", percentage=10, milestone=1)
        product = Product.objects.create(name="Coupon product", price_cents=100)
        InventoryUnit.objects.bulk_create([InventoryUnit(product=product) for _ in range(2)])
        carts = []
        for number in range(2):
            customer = Customer.objects.create(
                name=f"Coupon Buyer {number}", email=f"coupon-{number}@test.example"
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
                order, _ = checkout(cart.id, coupon.code, key)
                results.append(("order", order.id, cart.id))
            except DomainError as exc:
                results.append((exc.code, None, cart.id))
            finally:
                close_old_connections()

        threads = [
            Thread(target=run, args=(cart, f"coupon-race-{index}"))
            for index, cart in enumerate(carts)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sum(result[0] == "order" for result in results), 1)
        self.assertEqual(sum(result[0] == "coupon_reserved" for result in results), 1)
        losing_cart_id = next(result[2] for result in results if result[0] == "coupon_reserved")
        self.assertEqual(Cart.objects.get(id=losing_cart_id).status, Cart.Status.OPEN)
        coupon.refresh_from_db()
        self.assertEqual(coupon.status, Coupon.Status.RESERVED)
        self.assertEqual(Order.objects.filter(status=Order.Status.PENDING).count(), 1)

    def test_finalization_and_same_customer_coupon_checkout_do_not_deadlock(self):
        program = RewardProgram.objects.create(key="default", orders_per_coupon=1, percentage=10)
        coupon = Coupon.objects.create(program=program, code="LOCKED", percentage=10, milestone=1)
        customer = Customer.objects.create(name="Lock Buyer", email="lock@test.example")
        first_product = Product.objects.create(name="First", price_cents=100)
        second_product = Product.objects.create(name="Second", price_cents=100)
        InventoryUnit.objects.create(product=first_product)
        InventoryUnit.objects.create(product=second_product)
        first_cart = customer.carts.create()
        CartItem.objects.create(cart=first_cart, product=first_product, quantity=1)
        first_order, _ = checkout(first_cart.id, coupon.code, "first-lock-order")
        second_cart = customer.carts.create()
        CartItem.objects.create(cart=second_cart, product=second_product, quantity=1)

        customer_locked = Event()
        finalizer_started = Event()
        allow_checkout = Event()
        results = []

        def competing_checkout():
            close_old_connections()
            try:
                with transaction.atomic():
                    Customer.objects.select_for_update().get(id=customer.id)
                    customer_locked.set()
                    allow_checkout.wait(timeout=5)
                    try:
                        checkout(second_cart.id, coupon.code, "second-lock-order")
                    except DomainError as exc:
                        results.append(exc.code)
            finally:
                close_old_connections()

        def finalize():
            close_old_connections()
            try:
                customer_locked.wait(timeout=5)
                finalizer_started.set()
                from store.services.commerce import finalize_order

                finalize_order(first_order.id, succeeded=True)
                results.append("finalized")
            finally:
                close_old_connections()

        checkout_thread = Thread(target=competing_checkout)
        finalize_thread = Thread(target=finalize)
        checkout_thread.start()
        finalize_thread.start()
        self.assertTrue(finalizer_started.wait(timeout=5))
        allow_checkout.set()
        checkout_thread.join(timeout=10)
        finalize_thread.join(timeout=10)

        self.assertFalse(checkout_thread.is_alive())
        self.assertFalse(finalize_thread.is_alive())
        self.assertCountEqual(results, ["coupon_reserved", "finalized"])
        first_order.refresh_from_db()
        self.assertEqual(first_order.status, Order.Status.CONFIRMED)
        self.assertEqual(Cart.objects.get(id=second_cart.id).status, Cart.Status.OPEN)
