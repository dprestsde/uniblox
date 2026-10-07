from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from rest_framework.decorators import api_view
from rest_framework.response import Response

from config.errors import DomainError
from store.models import Cart, Coupon, Customer, InventoryUnit, Order, Product
from store.services.commerce import (
    add_cart_item,
    checkout,
    generate_coupon,
    get_or_create_open_cart,
    remove_cart_item,
    require_idempotency_key,
    set_cart_item,
)


def _positive_int(value, field="quantity"):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DomainError("invalid_input", f"{field} must be a positive integer.")
    return value


def _uuid(value, resource):
    from uuid import UUID

    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise DomainError("invalid_id", f"Invalid {resource} ID.") from exc


def customer_data(customer):
    return {
        "id": str(customer.id),
        "name": customer.name,
        "email": customer.email,
        "orders_count": customer.orders_count,
    }


def product_data(product):
    return {
        "id": str(product.id),
        "name": product.name,
        "price_cents": product.price_cents,
        "currency": "USD",
        "available_quantity": getattr(product, "available_quantity", None)
        if hasattr(product, "available_quantity")
        else InventoryUnit.objects.filter(
            product=product, status=InventoryUnit.Status.AVAILABLE
        ).count(),
    }


def cart_data(cart):
    if cart.status == Cart.Status.CHECKOUT_STARTED and hasattr(cart, "order"):
        order = cart.order
        items = [
            {
                "product_id": str(item.product_id),
                "name": item.product_name,
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
                "line_total_cents": item.line_total_cents,
            }
            for item in order.items.all().order_by("product_id")
        ]
        total = order.gross_cents
    else:
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
        total = sum(item["line_total_cents"] for item in items)
    return {
        "id": str(cart.id),
        "customer_id": str(cart.customer_id),
        "status": cart.status,
        "currency": "USD",
        "subtotal_cents": total,
        "items": items,
    }


def order_data(order):
    return {
        "id": str(order.id),
        "customer_id": str(order.customer_id),
        "cart_id": str(order.cart_id),
        "status": order.status,
        "currency": order.currency,
        "gross_cents": order.gross_cents,
        "discount_cents": order.discount_cents,
        "net_cents": order.net_cents,
        "coupon": {
            "code": order.coupon_code,
            "percentage": order.coupon_percentage,
        }
        if order.coupon_code
        else None,
        "failure_code": order.failure_code or None,
        "items": [
            {
                "product_id": str(item.product_id),
                "name": item.product_name,
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
                "line_total_cents": item.line_total_cents,
            }
            for item in order.items.all().order_by("product_id")
        ],
        "created_at": order.created_at.isoformat(),
        "finalized_at": order.finalized_at.isoformat() if order.finalized_at else None,
    }


def coupon_data(coupon):
    return {
        "id": str(coupon.id),
        "code": coupon.code,
        "percentage": coupon.percentage,
        "milestone": coupon.milestone,
        "status": coupon.status,
        "owner_order_id": str(coupon.owner_order_id) if coupon.owner_order_id else None,
    }


@api_view(["POST"])
def customers(request):
    if "orders_count" in request.data:
        raise DomainError("read_only_field", "orders_count is read-only.")
    name = str(request.data.get("name", "")).strip()
    email = str(request.data.get("email", "")).strip().lower()
    try:
        validate_email(email)
    except ValidationError as exc:
        raise DomainError("invalid_input", "A valid name and email are required.") from exc
    if not name:
        raise DomainError("invalid_input", "A valid name and email are required.")
    try:
        customer = Customer.objects.create(name=name, email=email)
    except IntegrityError as exc:
        raise DomainError(
            "duplicate_email", "A customer with this email exists.", status=409
        ) from exc
    return Response(customer_data(customer), status=201)


@api_view(["GET"])
def customer_detail(request, customer_id):
    customer = Customer.objects.filter(id=_uuid(customer_id, "customer")).first()
    if not customer:
        raise DomainError("customer_not_found", "Customer not found.", status=404)
    return Response(customer_data(customer))


@api_view(["GET"])
def products(request):
    rows = Product.objects.annotate(
        available_quantity=Count(
            "inventory_units", filter=Q(inventory_units__status=InventoryUnit.Status.AVAILABLE)
        )
    ).order_by("id")
    return _paginated(request, rows, product_data)


@api_view(["GET"])
def product_detail(request, product_id):
    product = Product.objects.filter(id=_uuid(product_id, "product")).first()
    if not product:
        raise DomainError("product_not_found", "Product not found.", status=404)
    return Response(product_data(product))


@api_view(["GET", "PUT"])
def active_cart(request, customer_id):
    customer = Customer.objects.filter(id=_uuid(customer_id, "customer")).first()
    if not customer:
        raise DomainError("customer_not_found", "Customer not found.", status=404)
    cart = Cart.objects.filter(customer=customer, status=Cart.Status.OPEN).first()
    if request.method == "GET":
        if not cart:
            raise DomainError("cart_not_found", "No open cart exists.", status=404)
        return Response(cart_data(cart))
    with transaction.atomic():
        customer = Customer.objects.select_for_update().get(id=customer.id)
        cart, created = get_or_create_open_cart(customer)
    return Response(cart_data(cart), status=201 if created else 200)


@api_view(["POST"])
def cart_items(request, customer_id):
    quantity = _positive_int(request.data.get("quantity"))
    product_id = _uuid(request.data.get("product_id"), "product")
    item, replayed = add_cart_item(
        _uuid(customer_id, "customer"), product_id, quantity, require_idempotency_key(request)
    )
    return Response(cart_data(item.cart), status=200 if replayed else 201)


@api_view(["GET"])
def cart_detail(request, cart_id):
    cart = Cart.objects.select_related("customer").filter(id=_uuid(cart_id, "cart")).first()
    if not cart:
        raise DomainError("cart_not_found", "Cart not found.", status=404)
    return Response(cart_data(cart))


@api_view(["PATCH", "DELETE"])
def cart_item_detail(request, cart_id, product_id):
    cart_id = _uuid(cart_id, "cart")
    product_id = _uuid(product_id, "product")
    if request.method == "DELETE":
        remove_cart_item(cart_id, product_id)
        return Response(status=204)
    item = set_cart_item(cart_id, product_id, _positive_int(request.data.get("quantity")))
    return Response(cart_data(item.cart))


@api_view(["POST"])
def checkout_cart(request, cart_id):
    coupon_code = request.data.get("coupon_code")
    if coupon_code is not None:
        coupon_code = str(coupon_code).strip()
    order, replayed = checkout(
        _uuid(cart_id, "cart"), coupon_code, require_idempotency_key(request)
    )
    status = 202 if order.status == Order.Status.PENDING else 200
    return Response(order_data(order), status=status)


@api_view(["GET"])
def order_detail(request, order_id):
    order = Order.objects.filter(id=_uuid(order_id, "order")).first()
    if not order:
        raise DomainError("order_not_found", "Order not found.", status=404)
    return Response(order_data(order))


@api_view(["POST"])
def coupon_generate(request):
    coupon, replayed = generate_coupon(require_idempotency_key(request))
    return Response(coupon_data(coupon), status=200 if replayed else 201)


@api_view(["GET"])
def coupon_list(request):
    return _paginated(request, Coupon.objects.order_by("created_at", "id"), coupon_data)


@api_view(["GET"])
def report_summary(request):
    with transaction.atomic():
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        confirmed = Order.objects.filter(status=Order.Status.CONFIRMED)
        totals = confirmed.aggregate(
            confirmed_orders=Count("id"),
            gross_revenue_cents=Coalesce(Sum("gross_cents"), 0),
            discounts_cents=Coalesce(Sum("discount_cents"), 0),
            net_revenue_cents=Coalesce(Sum("net_cents"), 0),
        )
        purchased = (
            confirmed.values("items__product_id", "items__product_name")
            .annotate(quantity=Sum("items__quantity"))
            .order_by("items__product_id")
        )
        coupon_counts = {
            status.lower(): count
            for status, count in Coupon.objects.values_list("status").annotate(count=Count("id"))
        }
        coupon_counts = {
            "available": coupon_counts.get("available", 0),
            "reserved": coupon_counts.get("reserved", 0),
            "redeemed": coupon_counts.get("redeemed", 0),
        }
        coupon_counts["generated"] = sum(coupon_counts.values())
        return Response(
            {
                **totals,
                "purchased_quantities": [
                    {
                        "product_id": str(row["items__product_id"]),
                        "name": row["items__product_name"],
                        "quantity": row["quantity"],
                    }
                    for row in purchased
                ],
                "coupons": coupon_counts,
            }
        )


def _paginated(request, query, encode):
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
        page_size = min(max(int(request.query_params.get("page_size", 20)), 1), 100)
    except ValueError as exc:
        raise DomainError("invalid_pagination", "page and page_size must be integers.") from exc
    count = query.count()
    rows = query[(page - 1) * page_size : page * page_size]
    return Response(
        {
            "count": count,
            "page": page,
            "page_size": page_size,
            "results": [encode(row) for row in rows],
        }
    )
