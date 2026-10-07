from django.urls import path, re_path

from store.api import views
from store.api.health import ApiNotFoundView, LiveHealthView, ReadyHealthView

urlpatterns = [
    path("health/live", LiveHealthView.as_view()),
    path("health/ready", ReadyHealthView.as_view()),
    path("api/v1/customers", views.CustomerCollectionView.as_view()),
    path("api/v1/customers/<uuid:customer_id>", views.CustomerDetailView.as_view()),
    path("api/v1/products", views.ProductListView.as_view()),
    path("api/v1/products/<uuid:product_id>", views.ProductDetailView.as_view()),
    path("api/v1/customers/<uuid:customer_id>/cart", views.ActiveCartView.as_view()),
    path("api/v1/customers/<uuid:customer_id>/cart/items", views.CartItemCreateView.as_view()),
    path("api/v1/carts/<uuid:cart_id>", views.CartDetailView.as_view()),
    path(
        "api/v1/carts/<uuid:cart_id>/items/<uuid:product_id>",
        views.CartItemDetailView.as_view(),
    ),
    path("api/v1/carts/<uuid:cart_id>/checkout", views.CheckoutView.as_view()),
    path("api/v1/orders/<uuid:order_id>", views.OrderDetailView.as_view()),
    path("api/v1/admin/coupons/generate", views.CouponGenerationView.as_view()),
    path("api/v1/admin/coupons", views.CouponListView.as_view()),
    path("api/v1/admin/reports/summary", views.ReportSummaryView.as_view()),
    re_path(r"^api/v1(?:/.*)?$", ApiNotFoundView.as_view()),
]
