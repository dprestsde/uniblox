import logging

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from store.api.serializers import (
    ActiveCartCreateSerializer,
    CartItemAddSerializer,
    CartItemRemoveSerializer,
    CartItemUpdateSerializer,
    CartPathSerializer,
    CartSerializer,
    CheckoutSerializer,
    CouponGenerateSerializer,
    CouponPageSerializer,
    CouponSerializer,
    CustomerCreateSerializer,
    CustomerPathSerializer,
    CustomerSerializer,
    IdempotencyHeaderSerializer,
    OrderPathSerializer,
    OrderSerializer,
    PaginationSerializer,
    ProductPageSerializer,
    ProductPathSerializer,
    ProductSerializer,
    ReportSerializer,
)
from store.exceptions import OutputContractError
from store.services.cart import CartService
from store.services.coupon import CouponService
from store.services.customer import CustomerService
from store.services.order import OrderService
from store.services.product import ProductService
from store.services.report import ReportService

logger = logging.getLogger(__name__)


def validated_response(serializer_class, payload, *, response_status=200):
    serializer = serializer_class(data=payload)
    try:
        serializer.is_valid(raise_exception=True)
    except ValidationError as exc:
        logger.error(
            "invalid_api_response",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"operation": serializer_class.__name__},
        )
        raise OutputContractError from exc
    return Response(serializer.data, status=response_status)


class ServiceAPIView(APIView):
    service_class = None

    def get_service(self):
        return self.service_class()

    def command(self, serializer_class, data, **trusted_values):
        serializer = serializer_class(data=data, context={"service": self.get_service()})
        serializer.is_valid(raise_exception=True)
        return serializer.save(**trusted_values)

    @staticmethod
    def validated(serializer_class, data):
        serializer = serializer_class(data=data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class CustomerCollectionView(ServiceAPIView):
    service_class = CustomerService

    def post(self, request):
        customer = self.command(CustomerCreateSerializer, request.data)
        return validated_response(
            CustomerSerializer, customer, response_status=status.HTTP_201_CREATED
        )


class CustomerDetailView(ServiceAPIView):
    service_class = CustomerService

    def get(self, request, customer_id):
        values = self.validated(CustomerPathSerializer, {"customer_id": customer_id})
        return validated_response(CustomerSerializer, self.get_service().get(**values))


class ProductListView(ServiceAPIView):
    service_class = ProductService

    def get(self, request):
        values = self.validated(PaginationSerializer, request.query_params)
        return validated_response(ProductPageSerializer, self.get_service().list(**values))


class ProductDetailView(ServiceAPIView):
    service_class = ProductService

    def get(self, request, product_id):
        values = self.validated(ProductPathSerializer, {"product_id": product_id})
        return validated_response(ProductSerializer, self.get_service().get(**values))


class ActiveCartView(ServiceAPIView):
    service_class = CartService

    def get(self, request, customer_id):
        values = self.validated(CustomerPathSerializer, {"customer_id": customer_id})
        return validated_response(CartSerializer, self.get_service().get_active(**values))

    def put(self, request, customer_id):
        path = self.validated(CustomerPathSerializer, {"customer_id": customer_id})
        cart, created = self.command(ActiveCartCreateSerializer, request.data, **path)
        return validated_response(
            CartSerializer,
            cart,
            response_status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class CartItemCreateView(ServiceAPIView):
    service_class = CartService

    def post(self, request, customer_id):
        path = self.validated(CustomerPathSerializer, {"customer_id": customer_id})
        header = self.validated(
            IdempotencyHeaderSerializer,
            {"idempotency_key": request.headers.get("Idempotency-Key")},
        )
        cart, replayed = self.command(
            CartItemAddSerializer,
            request.data,
            **path,
            **header,
        )
        return validated_response(
            CartSerializer,
            cart,
            response_status=status.HTTP_200_OK if replayed else status.HTTP_201_CREATED,
        )


class CartDetailView(ServiceAPIView):
    service_class = CartService

    def get(self, request, cart_id):
        values = self.validated(CartPathSerializer, {"cart_id": cart_id})
        return validated_response(CartSerializer, self.get_service().get(**values))


class CartItemDetailView(ServiceAPIView):
    service_class = CartService

    def patch(self, request, cart_id, product_id):
        cart_path = self.validated(CartPathSerializer, {"cart_id": cart_id})
        product_path = self.validated(ProductPathSerializer, {"product_id": product_id})
        cart = self.command(
            CartItemUpdateSerializer,
            request.data,
            **cart_path,
            **product_path,
        )
        return validated_response(CartSerializer, cart)

    def delete(self, request, cart_id, product_id):
        cart_path = self.validated(CartPathSerializer, {"cart_id": cart_id})
        product_path = self.validated(ProductPathSerializer, {"product_id": product_id})
        self.command(
            CartItemRemoveSerializer,
            request.data,
            **cart_path,
            **product_path,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class CheckoutView(ServiceAPIView):
    service_class = OrderService

    def post(self, request, cart_id):
        path = self.validated(CartPathSerializer, {"cart_id": cart_id})
        header = self.validated(
            IdempotencyHeaderSerializer,
            {"idempotency_key": request.headers.get("Idempotency-Key")},
        )
        order, _ = self.command(
            CheckoutSerializer,
            request.data,
            **path,
            **header,
        )
        response_status = (
            status.HTTP_202_ACCEPTED if order["status"] == "PENDING" else status.HTTP_200_OK
        )
        return validated_response(OrderSerializer, order, response_status=response_status)


class OrderDetailView(ServiceAPIView):
    service_class = OrderService

    def get(self, request, order_id):
        values = self.validated(OrderPathSerializer, {"order_id": order_id})
        return validated_response(OrderSerializer, self.get_service().get(**values))


class CouponGenerationView(ServiceAPIView):
    service_class = CouponService

    def post(self, request):
        header = self.validated(
            IdempotencyHeaderSerializer,
            {"idempotency_key": request.headers.get("Idempotency-Key")},
        )
        coupon, replayed = self.command(
            CouponGenerateSerializer,
            request.data,
            **header,
        )
        return validated_response(
            CouponSerializer,
            coupon,
            response_status=status.HTTP_200_OK if replayed else status.HTTP_201_CREATED,
        )


class CouponListView(ServiceAPIView):
    service_class = CouponService

    def get(self, request):
        values = self.validated(PaginationSerializer, request.query_params)
        return validated_response(CouponPageSerializer, self.get_service().list(**values))


class ReportSummaryView(ServiceAPIView):
    service_class = ReportService

    def get(self, request):
        return validated_response(ReportSerializer, self.get_service().summary())
