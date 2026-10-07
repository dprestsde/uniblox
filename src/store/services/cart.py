from django.db import IntegrityError, transaction

from store.exceptions import ServiceError
from store.models import Cart, CartItem, Customer, InventoryUnit, Product
from store.services.common import IdempotencyService, fingerprint
from store.services.presenters import cart_dto


class CartService:
    def __init__(self, idempotency_service=None):
        self.idempotency = idempotency_service or IdempotencyService()

    @staticmethod
    def available_quantity(product_id):
        return InventoryUnit.objects.filter(
            product_id=product_id, status=InventoryUnit.Status.AVAILABLE
        ).count()

    @staticmethod
    def _get_or_create_open_cart(customer):
        cart = Cart.objects.filter(customer=customer, status=Cart.Status.OPEN).first()
        if cart:
            return cart, False
        try:
            return Cart.objects.create(customer=customer), True
        except IntegrityError:
            return Cart.objects.get(customer=customer, status=Cart.Status.OPEN), False

    def get_active(self, customer_id):
        if not Customer.objects.filter(id=customer_id).exists():
            raise ServiceError("customer_not_found", "Customer not found.", status=404)
        cart = Cart.objects.filter(customer_id=customer_id, status=Cart.Status.OPEN).first()
        if not cart:
            raise ServiceError("cart_not_found", "No open cart exists.", status=404)
        return cart_dto(cart)

    def create_active(self, customer_id):
        with transaction.atomic():
            customer = Customer.objects.select_for_update().filter(id=customer_id).first()
            if not customer:
                raise ServiceError("customer_not_found", "Customer not found.", status=404)
            cart, created = self._get_or_create_open_cart(customer)
            return cart_dto(cart), created

    def get(self, cart_id):
        cart = Cart.objects.select_related("customer").filter(id=cart_id).first()
        if not cart:
            raise ServiceError("cart_not_found", "Cart not found.", status=404)
        return cart_dto(cart)

    def add_item(self, *, customer_id, product_id, quantity, idempotency_key):
        scope = str(customer_id)
        request_fingerprint = fingerprint({"product_id": str(product_id), "quantity": quantity})
        with transaction.atomic():
            customer = Customer.objects.select_for_update().filter(id=customer_id).first()
            if not customer:
                raise ServiceError("customer_not_found", "Customer not found.", status=404)
            replay = self.idempotency.find_replay(
                "cart.add", scope, idempotency_key, request_fingerprint
            )
            if replay:
                if replay.response_snapshot:
                    return replay.response_snapshot, True
                raise ServiceError(
                    "idempotency_result_unavailable",
                    "The committed result for this legacy idempotency key is unavailable.",
                    status=409,
                )
            product = Product.objects.filter(id=product_id).first()
            if not product:
                raise ServiceError("product_not_found", "Product not found.", status=404)
            cart, _ = self._get_or_create_open_cart(customer)
            item = CartItem.objects.filter(cart=cart, product=product).first()
            resulting = (item.quantity if item else 0) + quantity
            available = self.available_quantity(product.id)
            if resulting > available:
                raise ServiceError(
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
            snapshot = cart_dto(cart)
            self.idempotency.record(
                operation="cart.add",
                scope=scope,
                key=idempotency_key,
                request_fingerprint=request_fingerprint,
                resource_type="cart_item",
                resource_id=item.id,
                response_status=201,
                response_snapshot=snapshot,
            )
            return snapshot, False

    def set_item(self, *, cart_id, product_id, quantity):
        with transaction.atomic():
            cart = Cart.objects.select_for_update().filter(id=cart_id).first()
            if not cart:
                raise ServiceError("cart_not_found", "Cart not found.", status=404)
            if cart.status != Cart.Status.OPEN:
                raise ServiceError(
                    "cart_frozen", "Checked-out carts cannot be changed.", status=409
                )
            product = Product.objects.filter(id=product_id).first()
            if not product:
                raise ServiceError("product_not_found", "Product not found.", status=404)
            available = self.available_quantity(product.id)
            if quantity > available:
                raise ServiceError(
                    "insufficient_inventory",
                    "Reduce the quantity to the available quantity.",
                    status=409,
                    details={
                        "product_id": str(product.id),
                        "requested": quantity,
                        "available": available,
                    },
                )
            CartItem.objects.update_or_create(
                cart=cart, product=product, defaults={"quantity": quantity}
            )
            return cart_dto(cart)

    def remove_item(self, *, cart_id, product_id):
        with transaction.atomic():
            cart = Cart.objects.select_for_update().filter(id=cart_id).first()
            if not cart:
                raise ServiceError("cart_not_found", "Cart not found.", status=404)
            if cart.status != Cart.Status.OPEN:
                raise ServiceError(
                    "cart_frozen", "Checked-out carts cannot be changed.", status=409
                )
            CartItem.objects.filter(cart=cart, product_id=product_id).delete()
