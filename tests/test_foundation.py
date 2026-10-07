from unittest.mock import patch

from django.db import connections
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from config.db_router import DatabaseRouter
from config.errors import json_exception_handler


class HealthTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    def test_liveness_does_not_require_database(self):
        with patch("store.api.health.connections") as databases:
            response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "live"})
        databases.__getitem__.assert_not_called()

    def test_readiness_hides_database_errors(self):
        with patch("store.api.health.connections") as databases:
            databases.__getitem__.side_effect = OSError("private database detail")
            response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "service_unavailable")
        self.assertNotIn("private database detail", response.content.decode())

    def test_shared_api_error_envelope(self):
        response = json_exception_handler(ValidationError("Invalid quantity."), {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertFalse(response.data["error"]["retryable"])


class DatabaseTests(TestCase):
    databases = {"default", "payments"}

    def test_aliases_are_distinct_and_queryable(self):
        names = []
        for alias in ("default", "payments"):
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT current_database()")
                names.append(cursor.fetchone()[0])
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(all(name.startswith("test_") for name in names))
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)

    def test_router_isolates_fake_provider_app(self):
        router = DatabaseRouter()
        self.assertTrue(router.allow_migrate("payments", "fake_payments"))
        self.assertFalse(router.allow_migrate("default", "fake_payments"))
        self.assertTrue(router.allow_migrate("default", "store"))
        self.assertFalse(router.allow_migrate("payments", "store"))
