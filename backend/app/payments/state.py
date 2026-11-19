import enum


class PaymentStatus(enum.StrEnum):
    CREATING = "creating"
    OPEN = "open"
    CREATION_UNKNOWN = "creation_unknown"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"


FINAL_STATUSES = frozenset({PaymentStatus.PAID, PaymentStatus.FAILED, PaymentStatus.EXPIRED})
NON_FINAL_STATUSES = frozenset(PaymentStatus) - FINAL_STATUSES

_ALLOWED: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.CREATING: frozenset(
        {
            PaymentStatus.OPEN,
            PaymentStatus.CREATION_UNKNOWN,
            PaymentStatus.PAID,
            PaymentStatus.FAILED,
            PaymentStatus.EXPIRED,
        }
    ),
    PaymentStatus.CREATION_UNKNOWN: frozenset(
        {
            PaymentStatus.CREATING,
            PaymentStatus.OPEN,
            PaymentStatus.PAID,
            PaymentStatus.FAILED,
            PaymentStatus.EXPIRED,
        }
    ),
    PaymentStatus.OPEN: frozenset({PaymentStatus.PAID, PaymentStatus.FAILED, PaymentStatus.EXPIRED}),
    PaymentStatus.FAILED: frozenset({PaymentStatus.PAID}),
    PaymentStatus.EXPIRED: frozenset({PaymentStatus.PAID}),
    PaymentStatus.PAID: frozenset(),
}


def can_transition(current: PaymentStatus, target: PaymentStatus) -> bool:
    return target in _ALLOWED[current]


def is_final(status: PaymentStatus) -> bool:
    return status in FINAL_STATUSES
