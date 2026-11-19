import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.core.errors import PaymentNotFound
from app.dependencies import (
    CurrentMerchant,
    DbDep,
    GatewayDep,
    SettingsDep,
    enforce_checkout_rate_limit,
    require_demo_endpoints,
)
from app.payments.catalog import list_products
from app.payments.repository import PaymentRepository
from app.payments.schemas import (
    CheckoutRequest,
    CheckoutResponse,
    CurrencyAmount,
    PaymentDetail,
    PaymentEventRead,
    PaymentPage,
    PaymentRead,
    PaymentStats,
    ProductRead,
)
from app.payments.service import CheckoutService, validate_idempotency_key
from app.payments.state import PaymentStatus

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/products", response_model=list[ProductRead])
def get_products() -> list[ProductRead]:
    return [ProductRead.model_validate(p) for p in list_products()]


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_checkout_rate_limit)],
    responses={
        200: {"description": "Replay of an earlier request with the same Idempotency-Key"},
        409: {"description": "Idempotency-Key reused with different parameters"},
        503: {"description": "Outcome at Stripe unknown: retry with the same Idempotency-Key"},
    },
)
def create_checkout(
    body: CheckoutRequest,
    merchant: CurrentMerchant,
    response: Response,
    db: DbDep,
    gateway: GatewayDep,
    settings: SettingsDep,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="Unique per purchase (e.g. a UUIDv4). Reuse it only to retry."),
    ] = None,
) -> CheckoutResponse:
    key = validate_idempotency_key(idempotency_key)
    result = CheckoutService(db, gateway, settings).create_checkout(
        merchant_id=merchant.id,
        idempotency_key=key,
        product_id=body.product_id,
        quantity=body.quantity,
        customer_email=body.customer_email,
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    payment = result.payment
    return CheckoutResponse(
        payment_id=payment.id,
        checkout_session_id=payment.stripe_checkout_session_id,
        checkout_url=result.checkout_url,
        checkout_expires_at=payment.checkout_expires_at,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
    )


@router.get("", response_model=PaymentPage)
def list_payments(
    merchant: CurrentMerchant,
    db: DbDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: PaymentStatus | None = Query(default=None, alias="status"),
) -> PaymentPage:
    items, total = PaymentRepository(db).list_for_merchant(
        merchant.id, limit=limit, offset=offset, status=status_filter
    )
    return PaymentPage(
        items=[PaymentRead.model_validate(p) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/demo/success", include_in_schema=False, dependencies=[Depends(require_demo_endpoints)])
def demo_success(session_id: str | None = None) -> dict[str, str | None]:
    return {
        "message": "Checkout completed on Stripe. Final status is set only by the verified webhook.",
        "session_id": session_id,
    }


@router.get("/demo/cancel", include_in_schema=False, dependencies=[Depends(require_demo_endpoints)])
def demo_cancel() -> dict[str, str]:
    return {"message": "Checkout cancelled. The payment stays pending until the session expires."}


@router.get("/stats", response_model=PaymentStats)
def payment_stats(merchant: CurrentMerchant, db: DbDep) -> PaymentStats:
    counts, paid = PaymentRepository(db).stats_for_merchant(merchant.id)
    return PaymentStats(
        total=sum(counts.values()),
        by_status=counts,
        paid_totals=[CurrencyAmount(currency=c, amount=a) for c, a in sorted(paid.items())],
    )


@router.get("/{payment_id}", response_model=PaymentDetail)
def get_payment(payment_id: uuid.UUID, merchant: CurrentMerchant, db: DbDep) -> PaymentDetail:
    repository = PaymentRepository(db)
    payment = repository.get_for_merchant(merchant.id, payment_id)
    if payment is None:
        # 404, not 403: another merchant's payment must be indistinguishable from a missing one.
        raise PaymentNotFound("Payment not found")
    return PaymentDetail(
        **PaymentRead.model_validate(payment).model_dump(),
        history=[PaymentEventRead.model_validate(e) for e in repository.history(payment.id)],
    )
