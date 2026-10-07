from django.db.models import Count, Q

from store.exceptions import ServiceError
from store.models import InventoryUnit, Product
from store.services.presenters import page_dto, product_dto


class ProductService:
    def list(self, *, page, page_size):
        query = Product.objects.annotate(
            available_quantity=Count(
                "inventory_units",
                filter=Q(inventory_units__status=InventoryUnit.Status.AVAILABLE),
            )
        ).order_by("id")
        count = query.count()
        rows = query[(page - 1) * page_size : page * page_size]
        return page_dto(rows, count=count, page=page, page_size=page_size, encode=product_dto)

    def get(self, product_id):
        product = Product.objects.filter(id=product_id).first()
        if not product:
            raise ServiceError("product_not_found", "Product not found.", status=404)
        return product_dto(product)
