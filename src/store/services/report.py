from django.db import connection, transaction
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce

from store.models import Coupon, Order


class ReportService:
    def summary(self):
        with transaction.atomic():
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
                for status, count in Coupon.objects.values_list("status").annotate(
                    count=Count("id")
                )
            }
            coupon_counts = {
                "available": coupon_counts.get("available", 0),
                "reserved": coupon_counts.get("reserved", 0),
                "redeemed": coupon_counts.get("redeemed", 0),
            }
            coupon_counts["generated"] = sum(coupon_counts.values())
            return {
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
