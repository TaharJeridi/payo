from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import DbDep, GatewayDep, SettingsDep, enforce_checkout_rate_limit
from app.payments.catalog import list_products
from app.payments.repository import PaymentRepository
from app.payments.schemas import (
    CheckoutRequest,
    CheckoutResponse,
    PaymentPage,
    PaymentRead,
    ProductRead,
)
from app.payments.service import CheckoutService
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
)
def create_checkout(body: CheckoutRequest, db: DbDep, gateway: GatewayDep, settings: SettingsDep) -> CheckoutResponse:
    result = CheckoutService(db, gateway, settings).create_checkout(
        product_id=body.product_id,
        quantity=body.quantity,
        customer_email=body.customer_email,
    )
    payment = result.payment
    return CheckoutResponse(
        payment_id=payment.id,
        checkout_session_id=payment.stripe_checkout_session_id or "",
        checkout_url=result.checkout_url,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
    )


@router.get("", response_model=PaymentPage)
def list_payments(
    db: DbDep,
    settings: SettingsDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status_filter: PaymentStatus | None = Query(default=None, alias="status"),
) -> PaymentPage:
    if not settings.demo_endpoints_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    items, total = PaymentRepository(db).list(limit=limit, offset=offset, status=status_filter)
    return PaymentPage(
        items=[PaymentRead.model_validate(p) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/demo/success", include_in_schema=False)
def demo_success(session_id: str | None = None) -> dict[str, str | None]:
    return {
        "message": "Checkout completed on Stripe. Final status is set only by the verified webhook.",
        "session_id": session_id,
    }


@router.get("/demo/cancel", include_in_schema=False)
def demo_cancel() -> dict[str, str]:
    return {"message": "Checkout cancelled. The payment stays pending until the session expires."}
