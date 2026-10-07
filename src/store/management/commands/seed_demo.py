import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from store.models import Customer, InventoryUnit, Product, RewardProgram

NAMESPACE = uuid.UUID("7ed6db86-c7b1-4f1d-b4d0-7c66a45bf81b")


def stable_id(name):
    return uuid.uuid5(NAMESPACE, name)


class Command(BaseCommand):
    help = "Seed stable demo customers, products, inventory, and the reward program."

    def handle(self, *args, **options):
        with transaction.atomic():
            program, created = RewardProgram.objects.get_or_create(
                key="default", defaults={"orders_per_coupon": 5, "percentage": 10}
            )
            if not created and (program.orders_per_coupon, program.percentage) != (5, 10):
                raise CommandError("existing reward program conflicts with n=5, x=10")
            for name, email in (
                ("Ada Demo", "ada@example.test"),
                ("Grace Demo", "grace@example.test"),
            ):
                Customer.objects.get_or_create(
                    id=stable_id(f"customer:{email}"), defaults={"name": name, "email": email}
                )
            products = (
                ("Mechanical Keyboard", 8900, 3),
                ("Wireless Mouse", 4500, 5),
                ("USB-C Hub", 6200, 2),
                ("Laptop Stand", 5100, 4),
                ("Scarce Monitor", 25900, 1),
            )
            for name, price, quantity in products:
                product, product_created = Product.objects.get_or_create(
                    id=stable_id(f"product:{name}"),
                    defaults={"name": name, "price_cents": price},
                )
                if product_created:
                    InventoryUnit.objects.bulk_create(
                        [InventoryUnit(product=product) for _ in range(quantity)]
                    )
        self.stdout.write(
            self.style.SUCCESS("Demo seed is ready; existing commerce state was preserved.")
        )
