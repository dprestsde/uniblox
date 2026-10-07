import logging
import os
import signal
import threading

from django.core.management.base import BaseCommand
from django.db import connections

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the recovery worker (foundation skeleton; no handlers registered)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument(
            "--poll-seconds", type=float, default=float(os.getenv("WORKER_POLL_SECONDS", "5"))
        )

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
        try:
            while not stopping.is_set():
                logger.info("worker_idle_no_handlers_registered")
                self.stdout.write("No handlers registered; processed 0 jobs.")
                connections.close_all()
                if options["once"]:
                    break
                stopping.wait(options["poll_seconds"])
        finally:
            connections.close_all()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
            logger.info("worker_stopped")
