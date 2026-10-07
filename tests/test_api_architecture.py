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
    IdempotencyHeaderSerializer,
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
            "product_id": str(uuid.uuid4()),
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
        customer_id = uuid.uuid4()
        self.assertEqual(
            serializer.save(customer_id=customer_id, idempotency_key="add-1"),
            ({"id": "snapshot"}, False),
        )
        service.add_item.assert_called_once_with(
            customer_id=customer_id,
            product_id=serializer.validated_data["product_id"],
            quantity=2,
            idempotency_key="add-1",
        )

    def test_checkout_normalizes_coupon_and_requires_idempotency_key(self):
        serializer = CheckoutSerializer(
            data={"coupon_code": " SAVE10 "}, context={"service": Mock()}
        )
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["coupon_code"], "SAVE10")

        missing = IdempotencyHeaderSerializer(data={"idempotency_key": None})
        with self.assertRaises(ServiceError) as raised:
            missing.is_valid(raise_exception=True)
        self.assertEqual(raised.exception.code, "idempotency_key_required")

    def test_pagination_accepts_boundaries_and_rejects_out_of_range_values(self):
        for data in ({"page": 1, "page_size": 1}, {"page": 1, "page_size": 100}):
            serializer = PaginationSerializer(data=data)
            self.assertTrue(serializer.is_valid(), serializer.errors)

        for data, field in (
            ({"page": 0}, "page"),
            ({"page": -1}, "page"),
            ({"page_size": 0}, "page_size"),
            ({"page_size": 101}, "page_size"),
            ({"page": "many"}, "page"),
        ):
            serializer = PaginationSerializer(data=data)
            self.assertFalse(serializer.is_valid(), data)
            self.assertIn(field, serializer.errors)


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

    def test_non_object_json_bodies_return_validation_errors(self):
        for payload in ("[{}]", "42", '"text"', "null"):
            response = self.client.generic(
                "POST", "/api/v1/customers", payload, content_type="application/json"
            )
            self.assertEqual(response.status_code, 400, payload)
            self.assertEqual(response.data["error"]["code"], "validation_error")

        customer_id = uuid.uuid4()
        cart_id = uuid.uuid4()
        endpoints = (
            (f"/api/v1/customers/{customer_id}/cart/items", {"Idempotency-Key": "body-1"}),
            (f"/api/v1/carts/{cart_id}/checkout", {"Idempotency-Key": "body-2"}),
            ("/api/v1/admin/coupons/generate", {"Idempotency-Key": "body-3"}),
        )
        for path, headers in endpoints:
            response = self.client.generic(
                "POST", path, "[{}]", content_type="application/json", headers=headers
            )
            self.assertEqual(response.status_code, 400, path)
            self.assertEqual(response.data["error"]["code"], "validation_error")

    def test_body_cannot_override_path_or_idempotency_header(self):
        customer_id = uuid.uuid4()
        response = self.client.post(
            f"/api/v1/customers/{customer_id}/cart/items",
            {
                "customer_id": str(uuid.uuid4()),
                "idempotency_key": "body-key",
                "product_id": str(uuid.uuid4()),
                "quantity": 1,
            },
            format="json",
            headers={"Idempotency-Key": "header-key"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertCountEqual(response.data["error"]["details"], ["customer_id", "idempotency_key"])

        cart_id = uuid.uuid4()
        checkout = self.client.post(
            f"/api/v1/carts/{cart_id}/checkout",
            {"cart_id": str(uuid.uuid4())},
            format="json",
            headers={"Idempotency-Key": "checkout-key"},
        )
        self.assertEqual(checkout.status_code, 400)
        self.assertIn("cart_id", checkout.data["error"]["details"])

    def test_invalid_pagination_returns_field_errors(self):
        for query, field in (
            ("page=0", "page"),
            ("page=-1", "page"),
            ("page_size=0", "page_size"),
            ("page_size=101", "page_size"),
        ):
            response = self.client.get(f"/api/v1/products?{query}")
            self.assertEqual(response.status_code, 400, query)
            self.assertIn(field, response.data["error"]["details"])

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
