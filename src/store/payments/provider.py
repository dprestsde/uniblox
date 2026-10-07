from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class PaymentStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class PaymentResult:
    status: PaymentStatus
    provider_reference: str | None = None


class PaymentProvider(Protocol):
    def pay(self, attempt_id: str, amount_minor: int, currency: str) -> PaymentResult: ...

    def get_status(self, attempt_id: str) -> PaymentResult: ...
