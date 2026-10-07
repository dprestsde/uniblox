import ast
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.urls import resolve
from rest_framework.test import APIClient
from rest_framework.views import APIView

from store.api.serializers import (
    CartItemAddSerializer,
    CheckoutSerializer,
    CustomerCreateSerializer,
    PaginationSerializer,
)
from store.exceptions import ServiceError


class SerializerTests(SimpleTestCase):
    def test_customer_input_normalizes_and_rejects_read_only_fields(self):
        service = Mock()
        service.create.return_value = {"id": str(uuid.uuid4())}
        serializer = CustomerCreateSerializer(
            data={"name": " Grace ", "email": " GRACE@EXAMPLE.TEST "},
            context={"service": service},
        )
        self.assertTrue(serializer.is_valid())
        serializer.save()
        service.create.assert_called_once_with(name="Grace", email="grace@example.test")

        read_only = CustomerCreateSerializer(
            data={"name": "Grace", "email": "grace@example.test", "orders_count": 9}
        )
        self.assertFalse(read_only.is_valid())
        self.assertIn("orders_count", read_only.errors)

    def test_cart_input_rejects_invalid_quantities_and_delegates(self):
        base = {
            "customer_id": str(uuid.uuid4()),
            "product_id": str(uuid.uuid4()),
            "idempotency_key": "add-1",
        }
        for quantity in (True, 0, -1, 1.5):
            serializer = CartItemAddSerializer(data={**base, "quantity": quantity})
            self.assertFalse(serializer.is_valid(), quantity)
            self.assertIn("quantity", serializer.errors)

        service = Mock()
        service.add_item.return_value = ({"id": "snapshot"}, False)
        serializer = CartItemAddSerializer(
            data={**base, "quantity": 2}, context={"service": service}
        )
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.save(), ({"id": "snapshot"}, False))
        service.add_item.assert_called_once()

    def test_checkout_normalizes_coupon_and_requires_idempotency_key(self):
        serializer = CheckoutSerializer(
            data={
                "cart_id": str(uuid.uuid4()),
                "coupon_code": " SAVE10 ",
                "idempotency_key": "checkout-1",
            },
            context={"service": Mock()},
        )
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["coupon_code"], "SAVE10")

        missing = CheckoutSerializer(data={"cart_id": str(uuid.uuid4()), "idempotency_key": None})
        with self.assertRaises(ServiceError) as raised:
            missing.is_valid(raise_exception=True)
        self.assertEqual(raised.exception.code, "idempotency_key_required")

    def test_pagination_preserves_clamping_and_rejects_nonintegers(self):
        serializer = PaginationSerializer(data={"page": -2, "page_size": 500})
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data, {"page": 1, "page_size": 100})

        invalid = PaginationSerializer(data={"page": "many"})
        self.assertFalse(invalid.is_valid())
        self.assertIn("page", invalid.errors)


class ApiArchitectureTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    def test_urls_resolve_to_api_view_classes(self):
        for path in (
            "/health/live",
            "/health/ready",
            "/api/v1/customers",
            "/api/v1/products",
            "/api/v1/admin/coupons",
            "/api/v1/admin/reports/summary",
        ):
            callback = resolve(path).func
            self.assertTrue(issubclass(callback.view_class, APIView), path)

    def test_runtime_entrypoints_do_not_use_orm_or_transactions(self):
        root = Path(__file__).resolve().parents[1]
        entrypoints = [
            root / "src/store/api/views.py",
            root / "src/store/api/health.py",
            *sorted((root / "src/store/management/commands").glob("*.py")),
        ]
        for path in entrypoints:
            tree = ast.parse(path.read_text())
            imports = {
                node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            }
            self.assertNotIn("store.models", imports, path)
            source = path.read_text()
            self.assertNotIn(".objects", source, path)
            self.assertNotIn("transaction.atomic", source, path)
        self.assertFalse((root / "src/store/services/commerce.py").exists())
        self.assertFalse((root / "src/store/services/worker.py").exists())

    def test_validation_errors_include_field_details(self):
        response = self.client.post(
            f"/api/v1/customers/{uuid.uuid4()}/cart/items",
            {"product_id": str(uuid.uuid4()), "quantity": 0},
            format="json",
            headers={"Idempotency-Key": "invalid-quantity"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertIn("quantity", response.data["error"]["details"])

    @patch("store.api.views.ProductService.list")
    def test_invalid_service_output_returns_safe_internal_error(self, list_products):
        list_products.return_value = {"unexpected": "shape"}
        with self.assertLogs("store.api.views", level="ERROR"):
            response = self.client.get("/api/v1/products")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["error"]["code"], "internal_error")
        self.assertNotIn("shape", str(response.data))

    def test_service_exceptions_are_framework_independent(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src/store/exceptions.py").read_text()
        self.assertNotIn("rest_framework", source)
