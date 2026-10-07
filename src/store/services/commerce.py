import hashlib
import json
import time
import uuid
from datetime import timedelta

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone

from config.errors import DomainError
from store.models import (
    Cart,
    CartItem,
    Coupon,
    Customer,
    IdempotencyRecord,
    InventoryUnit,
    Order,
    OrderItem,
    PaymentAttempt,
    Product,
    Reservation,
    RewardProgram,
)

MAX_MONEY_CENTS = 9_000_000_000_000_000
RESERVATION_MINUTES = 5
ALLOCATION_RETRY_SECONDS = 5


def fingerprint(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def require_idempotency_key(request):
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 200:
        raise DomainError("idempotency_key_required", "Provide a valid Idempotency-Key header.")
    return key


def _replay(operation, scope, key, request_fingerprint):
    record = IdempotencyRecord.objects.filter(operation=operation, scope=scope, key=key).first()
    if not record:
        return None
    if record.request_fingerprint != request_fingerprint:
        raise DomainError(
            "idempotency_key_conflict",
            "The idempotency key was already used with different input.",
            status=409,
        )
    return record


def available_quantity(product_id):
    return InventoryUnit.objects.filter(
        product_id=product_id, status=InventoryUnit.Status.AVAILABLE
    ).count()


def open_cart_snapshot(cart):
    items = [
        {
            "product_id": str(item.product_id),
            "name": item.product.name,
            "quantity": item.quantity,
            "unit_price_cents": item.product.price_cents,
            "line_total_cents": item.product.price_cents * item.quantity,
        }
        for item in cart.items.select_related("product").order_by("product_id")
    ]
    return {
        "id": str(cart.id),
        "customer_id": str(cart.customer_id),
        "status": cart.status,
        "currency": "USD",
        "subtotal_cents": sum(item["line_total_cents"] for item in items),
        "items": items,
    }


def get_or_create_open_cart(customer):
    cart = Cart.objects.filter(customer=customer, status=Cart.Status.OPEN).first()
    if cart:
        return cart, False
    try:
        return Cart.objects.create(customer=customer), True
    except IntegrityError:
        return Cart.objects.get(customer=customer, status=Cart.Status.OPEN), False


def add_cart_item(customer_id, product_id, quantity, key):
    scope = str(customer_id)
    request_fingerprint = fingerprint({"product_id": str(product_id), "quantity": quantity})
    with transaction.atomic():
        customer = Customer.objects.select_for_update().filter(id=customer_id).first()
        if not customer:
            raise DomainError("customer_not_found", "Customer not found.", status=404)
        replay = _replay("cart.add", scope, key, request_fingerprint)
        if replay:
            if replay.response_snapshot:
                return replay.response_snapshot, True
            raise DomainError(
                "idempotency_result_unavailable",
                "The committed result for this legacy idempotency key is unavailable.",
                status=409,
            )
        product = Product.objects.filter(id=product_id).first()
        if not product:
            raise DomainError("product_not_found", "Product not found.", status=404)
        cart, _ = get_or_create_open_cart(customer)
        item = CartItem.objects.filter(cart=cart, product=product).first()
        resulting = (item.quantity if item else 0) + quantity
        available = available_quantity(product.id)
        if resulting > available:
            raise DomainError(
                "insufficient_inventory",
                "Reduce the quantity to the available quantity.",
                status=409,
                details={
                    "product_id": str(product.id),
                    "requested": resulting,
                    "available": available,
                },
            )
        if item:
            item.quantity = resulting
            item.save(update_fields=["quantity"])
        else:
            item = CartItem.objects.create(cart=cart, product=product, quantity=quantity)
        response_snapshot = open_cart_snapshot(cart)
        IdempotencyRecord.objects.create(
            operation="cart.add",
            scope=scope,
            key=key,
            request_fingerprint=request_fingerprint,
            resource_type="cart_item",
            resource_id=item.id,
            response_status=201,
            response_snapshot=response_snapshot,
        )
        return response_snapshot, False


def set_cart_item(cart_id, product_id, quantity):
    with transaction.atomic():
        cart = Cart.objects.select_for_update().filter(id=cart_id).first()
        if not cart:
            raise DomainError("cart_not_found", "Cart not found.", status=404)
        if cart.status != Cart.Status.OPEN:
            raise DomainError("cart_frozen", "Checked-out carts cannot be changed.", status=409)
        product = Product.objects.filter(id=product_id).first()
        if not product:
            raise DomainError("product_not_found", "Product not found.", status=404)
        available = available_quantity(product.id)
        if quantity > available:
            raise DomainError(
                "insufficient_inventory",
                "Reduce the quantity to the available quantity.",
                status=409,
                details={
                    "product_id": str(product.id),
                    "requested": quantity,
                    "available": available,
                },
            )
        item, _ = CartItem.objects.update_or_create(
            cart=cart, product=product, defaults={"quantity": quantity}
        )
        return item


def remove_cart_item(cart_id, product_id):
    with transaction.atomic():
        cart = Cart.objects.select_for_update().filter(id=cart_id).first()
        if not cart:
            raise DomainError("cart_not_found", "Cart not found.", status=404)
        if cart.status != Cart.Status.OPEN:
            raise DomainError("cart_frozen", "Checked-out carts cannot be changed.", status=409)
        CartItem.objects.filter(cart=cart, product_id=product_id).delete()


class AllocationContention(Exception):
    pass


def _allocate_order(cart_id, coupon_code, key, request_fingerprint, *, wait_for_locks):
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '2s'")
        customer_id = Cart.objects.filter(id=cart_id).values_list("customer_id", flat=True).first()
        if not customer_id:
            raise DomainError("cart_not_found", "Cart not found.", status=404)
        customer = Customer.objects.select_for_update().get(id=customer_id)
        cart = Cart.objects.select_for_update().get(id=cart_id)
        replay = _replay("checkout", str(cart.id), key, request_fingerprint)
        if replay:
            return Order.objects.get(id=replay.resource_id), True
        existing = Order.objects.filter(cart=cart).first()
        if existing:
            raise DomainError(
                "cart_already_checked_out", "This cart already has an order.", status=409
            )
        if cart.status != Cart.Status.OPEN:
            raise DomainError("cart_frozen", "This cart is no longer editable.", status=409)
        items = list(
            CartItem.objects.select_related("product").filter(cart=cart).order_by("product_id")
        )
        if not items:
            raise DomainError("empty_cart", "Add at least one product before checkout.", status=409)

        gross = sum(item.product.price_cents * item.quantity for item in items)
        if gross > MAX_MONEY_CENTS:
            raise DomainError("amount_too_large", "The order total exceeds the supported range.")

        coupon = None
        percentage = 0
        if coupon_code:
            coupon = Coupon.objects.select_for_update().filter(code=coupon_code).first()
            if not coupon:
                raise DomainError("coupon_not_found", "Coupon not found.", status=404)
            if coupon.status != Coupon.Status.AVAILABLE:
                code = (
                    "coupon_reserved"
                    if coupon.status == Coupon.Status.RESERVED
                    else "coupon_redeemed"
                )
                raise DomainError(code, "Coupon is already reserved or redeemed.", status=409)
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
            order=order, expires_at=timezone.now() + timedelta(minutes=RESERVATION_MINUTES)
        )
        for item in items:
            query = InventoryUnit.objects.filter(
                product=item.product, status=InventoryUnit.Status.AVAILABLE
            ).order_by("product_id", "id")
            query = query.select_for_update(skip_locked=not wait_for_locks)
            units = list(query[: item.quantity])
            if len(units) < item.quantity:
                visible = available_quantity(item.product_id)
                if not wait_for_locks and visible >= item.quantity:
                    raise AllocationContention
                raise DomainError(
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
        attempt_values = {"order": order, "next_retry_at": timezone.now()}
        if net == 0:
            attempt_values.update(
                outcome=PaymentAttempt.Outcome.SUCCEEDED,
                initiated_at=timezone.now(),
                provider_reference="zero-total",
            )
        PaymentAttempt.objects.create(**attempt_values)
        IdempotencyRecord.objects.create(
            operation="checkout",
            scope=str(cart.id),
            key=key,
            request_fingerprint=request_fingerprint,
            resource_type="order",
            resource_id=order.id,
            response_status=202,
            response_snapshot={},
        )
        return order, False


def checkout(cart_id, coupon_code, key):
    request_fingerprint = fingerprint({"coupon_code": coupon_code or None})
    deadline = time.monotonic() + ALLOCATION_RETRY_SECONDS
    wait_for_locks = False
    while True:
        try:
            order, replayed = _allocate_order(
                cart_id, coupon_code, key, request_fingerprint, wait_for_locks=wait_for_locks
            )
            if order.net_cents == 0 and order.status == Order.Status.PENDING:
                finalize_order(order.id, succeeded=True)
                order.refresh_from_db()
            return order, replayed
        except AllocationContention:
            wait_for_locks = True
        except DatabaseError as exc:
            if time.monotonic() >= deadline:
                raise DomainError(
                    "inventory_busy",
                    "Inventory is busy; retry the checkout.",
                    status=503,
                    retryable=True,
                ) from exc
        if time.monotonic() >= deadline:
            raise DomainError(
                "inventory_busy",
                "Inventory is busy; retry the checkout.",
                status=503,
                retryable=True,
            )


def finalize_order(order_id, *, succeeded, failure_code="payment_failed", fault=None):
    with transaction.atomic():
        customer_id = (
            Order.objects.filter(id=order_id).values_list("customer_id", flat=True).first()
        )
        if not customer_id:
            raise Order.DoesNotExist(order_id)
        Customer.objects.select_for_update().get(id=customer_id)
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status != Order.Status.PENDING:
            expected = Order.Status.CONFIRMED if succeeded else Order.Status.FAILED
            if order.status != expected:
                raise DomainError(
                    "terminal_order_conflict", "Order is already terminal.", status=409
                )
            return order
        PaymentAttempt.objects.select_for_update().get(order=order)
        reservation = Reservation.objects.select_for_update().get(order=order)
        coupons = list(Coupon.objects.select_for_update().filter(owner_order=order))
        units = InventoryUnit.objects.select_for_update().filter(reservation=reservation)
        now = timezone.now()
        if succeeded:
            units.update(status=InventoryUnit.Status.SOLD)
            reservation.status = Reservation.Status.CONFIRMED
            order.status = Order.Status.CONFIRMED
            for coupon in coupons:
                if coupon.status != Coupon.Status.RESERVED:
                    raise DomainError(
                        "coupon_ownership_conflict",
                        "Coupon ownership is inconsistent.",
                        status=409,
                    )
                coupon.status = Coupon.Status.REDEEMED
                coupon.redeemed_at = now
                coupon.save(update_fields=["status", "redeemed_at"])
            Customer.objects.filter(id=order.customer_id).update(orders_count=F("orders_count") + 1)
        else:
            units.update(status=InventoryUnit.Status.AVAILABLE, reservation=None)
            reservation.status = Reservation.Status.RELEASED
            reservation.release_reason = failure_code
            order.status = Order.Status.FAILED
            order.failure_code = failure_code
            for coupon in coupons:
                if coupon.status == Coupon.Status.RESERVED and coupon.owner_order_id == order.id:
                    coupon.status = Coupon.Status.AVAILABLE
                    coupon.owner_order = None
                    coupon.save(update_fields=["status", "owner_order"])
        if fault:
            raise RuntimeError(fault)
        reservation.save(update_fields=["status", "release_reason"])
        order.finalized_at = now
        order.save(update_fields=["status", "failure_code", "finalized_at"])
        return order


def generate_coupon(key):
    request_fingerprint = fingerprint({})
    with transaction.atomic():
        program = RewardProgram.objects.select_for_update().filter(key="default").first()
        if not program:
            raise DomainError(
                "reward_program_missing", "Reward program is not initialized.", status=409
            )
        replay = _replay("coupon.generate", program.key, key, request_fingerprint)
        if replay:
            return Coupon.objects.get(id=replay.resource_id), True
        confirmed = Order.objects.filter(status=Order.Status.CONFIRMED).count()
        eligible = confirmed // program.orders_per_coupon
        generated = set(program.coupons.values_list("milestone", flat=True))
        milestone = next(
            (number for number in range(1, eligible + 1) if number not in generated), None
        )
        if milestone is None:
            raise DomainError(
                "no_coupon_eligible", "No ungenerated reward milestone is eligible.", status=409
            )
        coupon = Coupon.objects.create(
            program=program,
            code=f"REWARD-{milestone}-{uuid.uuid4().hex[:10].upper()}",
            percentage=program.percentage,
            milestone=milestone,
        )
        IdempotencyRecord.objects.create(
            operation="coupon.generate",
            scope=program.key,
            key=key,
            request_fingerprint=request_fingerprint,
            resource_type="coupon",
            resource_id=coupon.id,
            response_status=201,
            response_snapshot={},
        )
        return coupon, False
