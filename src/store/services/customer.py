from django.db import IntegrityError

from store.exceptions import ServiceError
from store.models import Customer
from store.services.presenters import customer_dto


class CustomerService:
    def create(self, *, name, email):
        try:
            customer = Customer.objects.create(name=name, email=email)
        except IntegrityError as exc:
            raise ServiceError(
                "duplicate_email", "A customer with this email exists.", status=409
            ) from exc
        return customer_dto(customer)

    def get(self, customer_id):
        customer = Customer.objects.filter(id=customer_id).first()
        if not customer:
            raise ServiceError("customer_not_found", "Customer not found.", status=404)
        return customer_dto(customer)
