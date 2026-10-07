from django.core.management.base import BaseCommand, CommandError

from store.exceptions import ServiceError
from store.services.demo import DemoDataService


class Command(BaseCommand):
    help = "Seed stable demo customers, products, inventory, and the reward program."

    def handle(self, *args, **options):
        try:
            DemoDataService().seed()
        except ServiceError as exc:
            raise CommandError(exc.message) from exc
        self.stdout.write(
            self.style.SUCCESS("Demo seed is ready; existing commerce state was preserved.")
        )
