import time
from datetime import timedelta

from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.utils import timezone

from store.exceptions import ServiceError
from store.models import (
    Cart,
    CartItem,
    Coupon,
    Customer,
    InventoryUnit,
    Order,
    OrderItem,
    PaymentAttempt,
    Reservation,
)
from store.services.common import IdempotencyService, fingerprint
from store.services.presenters import order_dto

MAX_MONEY_CENTS = 9_000_000_000_000_000
RESERVATION_MINUTES = 5
ALLOCATION_RETRY_SECONDS = 5


class AllocationContention(Exception):
    pass


class OrderService:
    def __init__(self, *, idempotency_service=None, clock=None, monotonic=None):
        self.idempotency = idempotency_service or IdempotencyService()
        self.clock = clock or timezone.now
        self.monotonic = monotonic or time.monotonic

    @staticmethod
    def _available_quantity(product_id):
        return InventoryUnit.objects.filter(
            product_id=product_id, status=InventoryUnit.Status.AVAILABLE
        ).count()

    def _allocate(self, cart_id, coupon_code, key, request_fingerprint, *, wait_for_locks):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '2s'")
            customer_id = (
                Cart.objects.filter(id=cart_id).values_list("customer_id", flat=True).first()
            )
            if not customer_id:
                raise ServiceError("cart_not_found", "Cart not found.", status=404)
            customer = Customer.objects.select_for_update().get(id=customer_id)
            cart = Cart.objects.select_for_update().get(id=cart_id)
            replay = self.idempotency.find_replay(
                "checkout", str(cart.id), key, request_fingerprint
            )
            if replay:
                return Order.objects.get(id=replay.resource_id), True
            if Order.objects.filter(cart=cart).exists():
                raise ServiceError(
                    "cart_already_checked_out", "This cart already has an order.", status=409
                )
            if cart.status != Cart.Status.OPEN:
                raise ServiceError("cart_frozen", "This cart is no longer editable.", status=409)
            items = list(
                CartItem.objects.select_related("product").filter(cart=cart).order_by("product_id")
            )
            if not items:
                raise ServiceError(
                    "empty_cart", "Add at least one product before checkout.", status=409
                )

            gross = sum(item.product.price_cents * item.quantity for item in items)
            if gross > MAX_MONEY_CENTS:
                raise ServiceError(
                    "amount_too_large", "The order total exceeds the supported range."
                )

            coupon = None
            percentage = 0
            if coupon_code:
                coupon = Coupon.objects.select_for_update().filter(code=coupon_code).first()
                if not coupon:
                    raise ServiceError("coupon_not_found", "Coupon not found.", status=404)
                if coupon.status != Coupon.Status.AVAILABLE:
                    code = (
                        "coupon_reserved"
                        if coupon.status == Coupon.Status.RESERVED
                        else "coupon_redeemed"
                    )
                    raise ServiceError(code, "Coupon is already reserved or redeemed.", status=409)
                percentage = coupon.percentage
            discount = (gross * percentage + 50) // 100
            net = gross - discount
            order = Order.objects.create(
                cart=cart,
                customer=customer,
                gross_cents=gross,
                discount_cents=discount,
                net_cents=net,
                coupon_code=coupon.code if coupon else "",
                coupon_percentage=percentage,
            )
            reservation = Reservation.objects.create(
                order=order, expires_at=self.clock() + timedelta(minutes=RESERVATION_MINUTES)
            )
            for item in items:
                query = InventoryUnit.objects.filter(
                    product=item.product, status=InventoryUnit.Status.AVAILABLE
                ).order_by("product_id", "id")
                units = list(
                    query.select_for_update(skip_locked=not wait_for_locks)[: item.quantity]
                )
                if len(units) < item.quantity:
                    visible = self._available_quantity(item.product_id)
                    if not wait_for_locks and visible >= item.quantity:
                        raise AllocationContention
                    raise ServiceError(
                        "insufficient_inventory",
                        "Reduce the quantity to the available quantity.",
                        status=409,
                        details={
                            "product_id": str(item.product_id),
                            "requested": item.quantity,
                            "available": visible,
                        },
                    )
                InventoryUnit.objects.filter(id__in=[unit.id for unit in units]).update(
                    status=InventoryUnit.Status.RESERVED, reservation=reservation
                )
                OrderItem.objects.create(
                    order=order,
                    product=item.product,
                    product_name=item.product.name,
                    quantity=item.quantity,
                    unit_price_cents=item.product.price_cents,
                    line_total_cents=item.product.price_cents * item.quantity,
                )
            if coupon:
                coupon.status = Coupon.Status.RESERVED
                coupon.owner_order = order
                coupon.save(update_fields=["status", "owner_order"])
            cart.status = Cart.Status.CHECKOUT_STARTED
            cart.save(update_fields=["status", "updated_at"])
            attempt_values = {"order": order, "next_retry_at": self.clock()}
            if net == 0:
                attempt_values.update(
                    outcome=PaymentAttempt.Outcome.SUCCEEDED,
                    initiated_at=self.clock(),
                    provider_reference="zero-total",
                )
            PaymentAttempt.objects.create(**attempt_values)
            self.idempotency.record(
                operation="checkout",
                scope=str(cart.id),
                key=key,
                request_fingerprint=request_fingerprint,
                resource_type="order",
                resource_id=order.id,
                response_status=202,
            )
            return order, False

    def checkout(self, *, cart_id, coupon_code, idempotency_key):
        request_fingerprint = fingerprint({"coupon_code": coupon_code or None})
        deadline = self.monotonic() + ALLOCATION_RETRY_SECONDS
        wait_for_locks = False
        while True:
            try:
                order, replayed = self._allocate(
                    cart_id,
                    coupon_code,
                    idempotency_key,
                    request_fingerprint,
                    wait_for_locks=wait_for_locks,
                )
                if order.net_cents == 0 and order.status == Order.Status.PENDING:
                    self.finalize(order.id, succeeded=True)
                    order.refresh_from_db()
                return order_dto(order), replayed
            except AllocationContention:
                wait_for_locks = True
            except DatabaseError as exc:
                if self.monotonic() >= deadline:
                    raise ServiceError(
                        "inventory_busy",
                        "Inventory is busy; retry the checkout.",
                        status=503,
                        retryable=True,
                    ) from exc
            if self.monotonic() >= deadline:
                raise ServiceError(
                    "inventory_busy",
                    "Inventory is busy; retry the checkout.",
                    status=503,
                    retryable=True,
                )

    def get(self, order_id):
        order = Order.objects.filter(id=order_id).first()
        if not order:
            raise ServiceError("order_not_found", "Order not found.", status=404)
        return order_dto(order)

    def finalize(self, order_id, *, succeeded, failure_code="payment_failed", fault=None):
        with transaction.atomic():
            customer_id = (
                Order.objects.filter(id=order_id).values_list("customer_id", flat=True).first()
            )
            if not customer_id:
                raise ServiceError("order_not_found", "Order not found.", status=404)
            Customer.objects.select_for_update().get(id=customer_id)
            order = Order.objects.select_for_update().get(id=order_id)
            if order.status != Order.Status.PENDING:
                expected = Order.Status.CONFIRMED if succeeded else Order.Status.FAILED
                if order.status != expected:
                    raise ServiceError(
                        "terminal_order_conflict", "Order is already terminal.", status=409
                    )
                return order_dto(order)
            PaymentAttempt.objects.select_for_update().get(order=order)
            reservation = Reservation.objects.select_for_update().get(order=order)
            coupons = list(Coupon.objects.select_for_update().filter(owner_order=order))
            units = InventoryUnit.objects.select_for_update().filter(reservation=reservation)
            now = self.clock()
            if succeeded:
                units.update(status=InventoryUnit.Status.SOLD)
                reservation.status = Reservation.Status.CONFIRMED
                order.status = Order.Status.CONFIRMED
                for coupon in coupons:
                    if coupon.status != Coupon.Status.RESERVED:
                        raise ServiceError(
                            "coupon_ownership_conflict",
                            "Coupon ownership is inconsistent.",
                            status=409,
                        )
                    coupon.status = Coupon.Status.REDEEMED
                    coupon.redeemed_at = now
                    coupon.save(update_fields=["status", "redeemed_at"])
                Customer.objects.filter(id=order.customer_id).update(
                    orders_count=F("orders_count") + 1
                )
            else:
                units.update(status=InventoryUnit.Status.AVAILABLE, reservation=None)
                reservation.status = Reservation.Status.RELEASED
                reservation.release_reason = failure_code
                order.status = Order.Status.FAILED
                order.failure_code = failure_code
                for coupon in coupons:
                    if (
                        coupon.status == Coupon.Status.RESERVED
                        and coupon.owner_order_id == order.id
                    ):
                        coupon.status = Coupon.Status.AVAILABLE
                        coupon.owner_order = None
                        coupon.save(update_fields=["status", "owner_order"])
            if fault:
                raise RuntimeError(fault)
            reservation.save(update_fields=["status", "release_reason"])
            order.finalized_at = now
            order.save(update_fields=["status", "failure_code", "finalized_at"])
            return order_dto(order)
