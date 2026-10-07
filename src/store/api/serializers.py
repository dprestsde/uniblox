from collections.abc import Mapping

from rest_framework import serializers

from store.exceptions import ServiceError


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            return super().to_internal_value(data)
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {field: ["Unknown or read-only field."] for field in sorted(unknown)}
            )
        return super().to_internal_value(data)


class IdempotencyHeaderSerializer(StrictSerializer):
    idempotency_key = serializers.CharField(
        max_length=200,
        trim_whitespace=True,
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    def validate_idempotency_key(self, value):
        if not value:
            raise ServiceError(
                "idempotency_key_required", "Provide a valid Idempotency-Key header."
            )
        return value


class CustomerCreateSerializer(StrictSerializer):
    name = serializers.CharField(max_length=200, trim_whitespace=True)
    email = serializers.EmailField(max_length=254)

    def validate_email(self, value):
        return value.strip().lower()

    def create(self, validated_data):
        return self.context["service"].create(**validated_data)


class CustomerSerializer(StrictSerializer):
    id = serializers.UUIDField()
    name = serializers.CharField(max_length=200)
    email = serializers.EmailField(max_length=254)
    orders_count = serializers.IntegerField(min_value=0)


class ProductSerializer(StrictSerializer):
    id = serializers.UUIDField()
    name = serializers.CharField(max_length=200)
    price_cents = serializers.IntegerField(min_value=0)
    currency = serializers.ChoiceField(choices=["USD"])
    available_quantity = serializers.IntegerField(min_value=0)


class CartLineSerializer(StrictSerializer):
    product_id = serializers.UUIDField()
    name = serializers.CharField(max_length=200)
    quantity = serializers.IntegerField(min_value=1)
    unit_price_cents = serializers.IntegerField(min_value=0)
    line_total_cents = serializers.IntegerField(min_value=0)


class CartSerializer(StrictSerializer):
    id = serializers.UUIDField()
    customer_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=["OPEN", "CHECKOUT_STARTED"])
    currency = serializers.ChoiceField(choices=["USD"])
    subtotal_cents = serializers.IntegerField(min_value=0)
    items = CartLineSerializer(many=True)


class CustomerPathSerializer(StrictSerializer):
    customer_id = serializers.UUIDField()


class CartPathSerializer(StrictSerializer):
    cart_id = serializers.UUIDField()


class OrderPathSerializer(StrictSerializer):
    order_id = serializers.UUIDField()


class ProductPathSerializer(StrictSerializer):
    product_id = serializers.UUIDField()


class ActiveCartCreateSerializer(StrictSerializer):
    def create(self, validated_data):
        return self.context["service"].create_active(validated_data.pop("customer_id"))


class CartItemAddSerializer(StrictSerializer):
    product_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)

    def create(self, validated_data):
        return self.context["service"].add_item(**validated_data)


class CartItemUpdateSerializer(StrictSerializer):
    quantity = serializers.IntegerField(min_value=1)

    def create(self, validated_data):
        return self.context["service"].set_item(**validated_data)


class CartItemRemoveSerializer(StrictSerializer):
    def create(self, validated_data):
        self.context["service"].remove_item(**validated_data)
        return {"removed": True}


class CheckoutSerializer(StrictSerializer):
    coupon_code = serializers.CharField(
        max_length=64, trim_whitespace=True, required=False, allow_blank=True, allow_null=True
    )

    def validate_coupon_code(self, value):
        return value or None

    def create(self, validated_data):
        validated_data.setdefault("coupon_code", None)
        return self.context["service"].checkout(**validated_data)


class OrderLineSerializer(CartLineSerializer):
    pass


class OrderCouponSerializer(StrictSerializer):
    code = serializers.CharField(max_length=64)
    percentage = serializers.IntegerField(min_value=1, max_value=100)


class OrderSerializer(StrictSerializer):
    id = serializers.UUIDField()
    customer_id = serializers.UUIDField()
    cart_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=["PENDING", "CONFIRMED", "FAILED"])
    currency = serializers.ChoiceField(choices=["USD"])
    gross_cents = serializers.IntegerField(min_value=0)
    discount_cents = serializers.IntegerField(min_value=0)
    net_cents = serializers.IntegerField(min_value=0)
    coupon = OrderCouponSerializer(allow_null=True)
    failure_code = serializers.CharField(max_length=64, allow_null=True)
    items = OrderLineSerializer(many=True)
    created_at = serializers.DateTimeField()
    finalized_at = serializers.DateTimeField(allow_null=True)

    def validate(self, attrs):
        if attrs["gross_cents"] != attrs["discount_cents"] + attrs["net_cents"]:
            raise serializers.ValidationError("Order totals do not balance.")
        return attrs


class CouponSerializer(StrictSerializer):
    id = serializers.UUIDField()
    code = serializers.CharField(max_length=64)
    percentage = serializers.IntegerField(min_value=1, max_value=100)
    milestone = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(choices=["AVAILABLE", "RESERVED", "REDEEMED"])
    owner_order_id = serializers.UUIDField(allow_null=True)


class CouponGenerateSerializer(StrictSerializer):
    def create(self, validated_data):
        return self.context["service"].generate(**validated_data)


class PaginationSerializer(StrictSerializer):
    page = serializers.IntegerField(default=1, min_value=1)
    page_size = serializers.IntegerField(default=20, min_value=1, max_value=100)


class ProductPageSerializer(StrictSerializer):
    count = serializers.IntegerField(min_value=0)
    page = serializers.IntegerField(min_value=1)
    page_size = serializers.IntegerField(min_value=1, max_value=100)
    results = ProductSerializer(many=True)


class CouponPageSerializer(StrictSerializer):
    count = serializers.IntegerField(min_value=0)
    page = serializers.IntegerField(min_value=1)
    page_size = serializers.IntegerField(min_value=1, max_value=100)
    results = CouponSerializer(many=True)


class PurchasedQuantitySerializer(StrictSerializer):
    product_id = serializers.UUIDField()
    name = serializers.CharField(max_length=200)
    quantity = serializers.IntegerField(min_value=1)


class CouponCountsSerializer(StrictSerializer):
    generated = serializers.IntegerField(min_value=0)
    available = serializers.IntegerField(min_value=0)
    reserved = serializers.IntegerField(min_value=0)
    redeemed = serializers.IntegerField(min_value=0)

    def validate(self, attrs):
        if attrs["generated"] != attrs["available"] + attrs["reserved"] + attrs["redeemed"]:
            raise serializers.ValidationError("Coupon counts do not balance.")
        return attrs


class ReportSerializer(StrictSerializer):
    confirmed_orders = serializers.IntegerField(min_value=0)
    gross_revenue_cents = serializers.IntegerField(min_value=0)
    discounts_cents = serializers.IntegerField(min_value=0)
    net_revenue_cents = serializers.IntegerField(min_value=0)
    purchased_quantities = PurchasedQuantitySerializer(many=True)
    coupons = CouponCountsSerializer()

    def validate(self, attrs):
        if attrs["gross_revenue_cents"] != (attrs["discounts_cents"] + attrs["net_revenue_cents"]):
            raise serializers.ValidationError("Report revenue does not balance.")
        return attrs


class HealthSerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=["live", "ready"])
