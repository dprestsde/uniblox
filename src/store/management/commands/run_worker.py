import logging
import os
import signal
import threading

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections

from store.services.payment import PaymentService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process payment attempts and recover pending orders."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument(
            "--poll-seconds", type=float, default=float(os.getenv("WORKER_POLL_SECONDS", "5"))
        )
        parser.add_argument("--attempt-id")

    def handle(self, *args, **options):
        if options["poll_seconds"] <= 0:
            raise ValueError("poll interval must be positive")
        stopping = threading.Event()

        def stop(signum, frame):
            stopping.set()

        previous = {}
        if not options["once"]:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, stop)
        logger.info("worker_started")
        payment_service = PaymentService()
        try:
            while not stopping.is_set():
                try:
                    outcomes = payment_service.run_cycle(attempt_id=options["attempt_id"])
                except DatabaseError as exc:
                    logger.exception(
                        "worker_cycle_database_error", extra={"operation": "payment_cycle"}
                    )
                    connections.close_all()
                    if options["once"]:
                        raise CommandError("Payment worker database operation failed.") from exc
                    stopping.wait(options["poll_seconds"])
                    continue
                self.stdout.write(f"Processed {len(outcomes)} payment attempt(s): {outcomes}")
                connections.close_all()
                if options["once"]:
                    break
                stopping.wait(options["poll_seconds"])
        finally:
            connections.close_all()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
            logger.info("worker_stopped")
