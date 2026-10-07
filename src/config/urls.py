from django.urls import path, re_path

from config.errors import api_not_found
from store.api import views
from store.api.health import live, ready

urlpatterns = [
    path("health/live", live),
    path("health/ready", ready),
    path("api/v1/customers", views.customers),
    path("api/v1/customers/<uuid:customer_id>", views.customer_detail),
    path("api/v1/products", views.products),
    path("api/v1/products/<uuid:product_id>", views.product_detail),
    path("api/v1/customers/<uuid:customer_id>/cart", views.active_cart),
    path("api/v1/customers/<uuid:customer_id>/cart/items", views.cart_items),
    path("api/v1/carts/<uuid:cart_id>", views.cart_detail),
    path("api/v1/carts/<uuid:cart_id>/items/<uuid:product_id>", views.cart_item_detail),
    path("api/v1/carts/<uuid:cart_id>/checkout", views.checkout_cart),
    path("api/v1/orders/<uuid:order_id>", views.order_detail),
    path("api/v1/admin/coupons/generate", views.coupon_generate),
    path("api/v1/admin/coupons", views.coupon_list),
    path("api/v1/admin/reports/summary", views.report_summary),
    re_path(r"^api/v1(?:/.*)?$", api_not_found),
]
