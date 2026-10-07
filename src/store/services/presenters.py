from store.models import Cart, InventoryUnit


def customer_dto(customer):
    return {
        "id": str(customer.id),
        "name": customer.name,
        "email": customer.email,
        "orders_count": customer.orders_count,
    }


def product_dto(product):
    available = getattr(product, "available_quantity", None)
    if available is None:
        available = InventoryUnit.objects.filter(
            product=product, status=InventoryUnit.Status.AVAILABLE
        ).count()
    return {
        "id": str(product.id),
        "name": product.name,
        "price_cents": product.price_cents,
        "currency": "USD",
        "available_quantity": available,
    }


def cart_dto(cart):
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
        subtotal = order.gross_cents
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
        subtotal = sum(item["line_total_cents"] for item in items)
    return {
        "id": str(cart.id),
        "customer_id": str(cart.customer_id),
        "status": cart.status,
        "currency": "USD",
        "subtotal_cents": subtotal,
        "items": items,
    }


def order_dto(order):
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


def coupon_dto(coupon):
    return {
        "id": str(coupon.id),
        "code": coupon.code,
        "percentage": coupon.percentage,
        "milestone": coupon.milestone,
        "status": coupon.status,
        "owner_order_id": str(coupon.owner_order_id) if coupon.owner_order_id else None,
    }


def page_dto(rows, *, count, page, page_size, encode):
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "results": [encode(row) for row in rows],
    }
