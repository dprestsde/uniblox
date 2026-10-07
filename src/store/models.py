import uuid

from django.db import models
from django.db.models import Q


class Customer(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    email = models.EmailField(max_length=254, unique=True)
    orders_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(orders_count__gte=0), name="customer_orders_nonnegative"
            )
        ]


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    price_cents = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class Cart(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN"
        CHECKOUT_STARTED = "CHECKOUT_STARTED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="carts")
    status = models.CharField(max_length=20, choices=Status, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["customer"], condition=Q(status="OPEN"), name="one_open_cart_per_customer"
            )
        ]


class CartItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["cart", "product"], name="unique_cart_product"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="cart_item_quantity_positive"),
        ]


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        CONFIRMED = "CONFIRMED"
        FAILED = "FAILED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cart = models.OneToOneField(Cart, on_delete=models.PROTECT, related_name="order")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=12, choices=Status, default=Status.PENDING)
    currency = models.CharField(max_length=3, default="USD")
    gross_cents = models.PositiveBigIntegerField()
    discount_cents = models.PositiveBigIntegerField(default=0)
    net_cents = models.PositiveBigIntegerField()
    coupon_code = models.CharField(max_length=64, blank=True)
    coupon_percentage = models.PositiveSmallIntegerField(default=0)
    failure_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(gross_cents__gte=0), name="order_gross_nonnegative"),
            models.CheckConstraint(
                condition=Q(discount_cents__gte=0), name="order_discount_nonnegative"
            ),
            models.CheckConstraint(condition=Q(net_cents__gte=0), name="order_net_nonnegative"),
            models.CheckConstraint(
                condition=Q(gross_cents=models.F("discount_cents") + models.F("net_cents")),
                name="order_totals_balance",
            ),
        ]


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    product_name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField()
    unit_price_cents = models.PositiveBigIntegerField()
    line_total_cents = models.PositiveBigIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "product"], name="unique_order_product"),
            models.CheckConstraint(
                condition=Q(quantity__gt=0), name="order_item_quantity_positive"
            ),
        ]


class Reservation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        CONFIRMED = "CONFIRMED"
        RELEASED = "RELEASED"

    order = models.OneToOneField(
        Order, primary_key=True, on_delete=models.PROTECT, related_name="reservation"
    )
    status = models.CharField(max_length=12, choices=Status, default=Status.ACTIVE)
    expires_at = models.DateTimeField()
    release_reason = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class InventoryUnit(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE"
        RESERVED = "RESERVED"
        SOLD = "SOLD"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="inventory_units")
    status = models.CharField(max_length=12, choices=Status, default=Status.AVAILABLE)
    reservation = models.ForeignKey(
        Reservation, null=True, blank=True, on_delete=models.PROTECT, related_name="units"
    )

    class Meta:
        indexes = [models.Index(fields=["product", "status", "id"], name="inventory_alloc_idx")]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(status="AVAILABLE", reservation__isnull=True)
                    | Q(status="RESERVED", reservation__isnull=False)
                    | Q(status="SOLD", reservation__isnull=False)
                ),
                name="inventory_status_owner_valid",
            )
        ]


class PaymentAttempt(models.Model):
    class Outcome(models.TextChoices):
        UNKNOWN = "UNKNOWN"
        SUCCEEDED = "SUCCEEDED"
        FAILED = "FAILED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name="payment_attempt")
    provider_key = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    outcome = models.CharField(max_length=12, choices=Outcome, default=Outcome.UNKNOWN)
    provider_reference = models.CharField(max_length=100, blank=True)
    initiated_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField()
    attempts = models.PositiveIntegerField(default=0)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_deadline = models.DateTimeField(null=True, blank=True)
    flagged_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)
    local_fault = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class RewardProgram(models.Model):
    key = models.CharField(primary_key=True, max_length=32, default="default", editable=False)
    orders_per_coupon = models.PositiveIntegerField(default=5)
    percentage = models.PositiveSmallIntegerField(default=10)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(orders_per_coupon__gte=1), name="reward_n_positive"),
            models.CheckConstraint(
                condition=Q(percentage__gte=1, percentage__lte=100), name="reward_x_valid"
            ),
        ]


class Coupon(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE"
        RESERVED = "RESERVED"
        REDEEMED = "REDEEMED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(RewardProgram, on_delete=models.PROTECT, related_name="coupons")
    code = models.CharField(max_length=64, unique=True)
    percentage = models.PositiveSmallIntegerField()
    milestone = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=Status, default=Status.AVAILABLE)
    owner_order = models.ForeignKey(
        Order, null=True, blank=True, on_delete=models.PROTECT, related_name="owned_coupons"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    redeemed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["program", "milestone"], name="unique_program_milestone"
            ),
            models.CheckConstraint(
                condition=Q(percentage__gte=1, percentage__lte=100),
                name="coupon_percentage_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="AVAILABLE", owner_order__isnull=True)
                    | Q(status="RESERVED", owner_order__isnull=False)
                    | Q(status="REDEEMED", owner_order__isnull=False)
                ),
                name="coupon_status_owner_valid",
            ),
        ]


class IdempotencyRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.CharField(max_length=40)
    scope = models.CharField(max_length=100)
    key = models.CharField(max_length=200)
    request_fingerprint = models.CharField(max_length=64)
    resource_type = models.CharField(max_length=40)
    resource_id = models.UUIDField()
    response_status = models.PositiveSmallIntegerField()
    response_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["operation", "scope", "key"], name="unique_idempotency_key"
            )
        ]
