import uuid

from django.db import models


class FakePayment(models.Model):
    class Outcome(models.TextChoices):
        SUCCEEDED = "SUCCEEDED"
        FAILED = "FAILED"

    attempt_id = models.UUIDField(primary_key=True)
    amount_cents = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3)
    outcome = models.CharField(max_length=12, choices=Outcome)
    provider_reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    lose_response_once = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
