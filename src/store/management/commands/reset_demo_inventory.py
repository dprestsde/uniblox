from django.core.management.base import BaseCommand, CommandError

from store.exceptions import ServiceError
from store.services.demo import DemoDataService


class Command(BaseCommand):
    help = "Reset available inventory for the five stable demo products."

    def handle(self, *args, **options):
        try:
            products = DemoDataService().reset_inventory()
        except ServiceError as exc:
            raise CommandError(exc.message) from exc
        for product in products:
            self.stdout.write(f"{product['name']}: {product['available_quantity']} available")
        self.stdout.write(
            self.style.SUCCESS("Demo inventory reset; sold and reserved units were preserved.")
        )
