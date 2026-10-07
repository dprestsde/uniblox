import json
import logging
import sys
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connections
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from config.db_router import DatabaseRouter
from config.errors import json_exception_handler
from config.logging import JsonFormatter


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

    def test_api_fallbacks_return_json_envelopes(self):
        malformed = self.client.get("/api/v1/customers/not-a-uuid")
        missing = self.client.get("/api/v1/not-a-real-route")
        for response in (malformed, missing):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response["Content-Type"], "application/json")
            self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_unexpected_exception_is_logged_and_hidden(self):
        with self.assertLogs("config.errors", level="ERROR"):
            response = json_exception_handler(RuntimeError("private diagnostic"), {})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["error"]["code"], "internal_error")
        self.assertNotIn("private diagnostic", str(response.data))

    def test_json_logs_include_safe_context_and_exception(self):
        try:
            raise ValueError("diagnostic detail")
        except ValueError:
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            "store.services.worker",
            logging.ERROR,
            __file__,
            1,
            "payment_attempt_processing_failed",
            (),
            exc_info,
        )
        record.attempt_id = "attempt-123"
        record.operation = "finalize"
        payload = json.loads(JsonFormatter().format(record))
        self.assertEqual(payload["attempt_id"], "attempt-123")
        self.assertEqual(payload["operation"], "finalize")
        self.assertIn("ValueError: diagnostic detail", payload["exception"])


class WorkerCommandTests(SimpleTestCase):
    class TwoCycleEvent:
        def __init__(self):
            self.checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks > 2

        def set(self):
            self.checks = 3

        def wait(self, timeout):
            return False

    @patch("store.management.commands.run_worker.signal.signal")
    @patch("store.management.commands.run_worker.threading.Event", new=TwoCycleEvent)
    @patch("store.management.commands.run_worker.run_cycle")
    def test_continuous_worker_recovers_after_database_failure(self, run_cycle, signal_mock):
        run_cycle.side_effect = [DatabaseError("database unavailable"), []]
        call_command("run_worker", poll_seconds=0.01)
        self.assertEqual(run_cycle.call_count, 2)

    @patch("store.management.commands.run_worker.run_cycle")
    def test_once_worker_reports_database_failure(self, run_cycle):
        run_cycle.side_effect = DatabaseError("database unavailable")
        with self.assertRaises(CommandError):
            call_command("run_worker", once=True)


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
