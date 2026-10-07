import uuid

from django.db import transaction

from store.exceptions import ServiceError
from store.models import Coupon, Order, RewardProgram
from store.services.common import IdempotencyService, fingerprint
from store.services.presenters import coupon_dto, page_dto


class CouponService:
    def __init__(self, idempotency_service=None):
        self.idempotency = idempotency_service or IdempotencyService()

    def generate(self, *, idempotency_key):
        request_fingerprint = fingerprint({})
        with transaction.atomic():
            program = RewardProgram.objects.select_for_update().filter(key="default").first()
            if not program:
                raise ServiceError(
                    "reward_program_missing", "Reward program is not initialized.", status=409
                )
            replay = self.idempotency.find_replay(
                "coupon.generate", program.key, idempotency_key, request_fingerprint
            )
            if replay:
                return coupon_dto(Coupon.objects.get(id=replay.resource_id)), True
            confirmed = Order.objects.filter(status=Order.Status.CONFIRMED).count()
            eligible = confirmed // program.orders_per_coupon
            generated = set(program.coupons.values_list("milestone", flat=True))
            milestone = next(
                (number for number in range(1, eligible + 1) if number not in generated), None
            )
            if milestone is None:
                raise ServiceError(
                    "no_coupon_eligible",
                    "No ungenerated reward milestone is eligible.",
                    status=409,
                )
            coupon = Coupon.objects.create(
                program=program,
                code=f"REWARD-{milestone}-{uuid.uuid4().hex[:10].upper()}",
                percentage=program.percentage,
                milestone=milestone,
            )
            self.idempotency.record(
                operation="coupon.generate",
                scope=program.key,
                key=idempotency_key,
                request_fingerprint=request_fingerprint,
                resource_type="coupon",
                resource_id=coupon.id,
                response_status=201,
            )
            return coupon_dto(coupon), False

    def list(self, *, page, page_size):
        query = Coupon.objects.order_by("created_at", "id")
        count = query.count()
        rows = query[(page - 1) * page_size : page * page_size]
        return page_dto(rows, count=count, page=page, page_size=page_size, encode=coupon_dto)
