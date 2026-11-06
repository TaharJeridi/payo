from typing import Annotated

from fastapi import APIRouter, Header, Request
from starlette.concurrency import run_in_threadpool

from app.dependencies import SettingsDep, WebhookProcessorDep
from app.webhooks.signature import verify_and_parse_event

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    settings: SettingsDep,
    processor: WebhookProcessorDep,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> dict[str, object]:
    payload = await request.body()
    event = verify_and_parse_event(
        payload,
        stripe_signature,
        settings.stripe_webhook_secret.get_secret_value(),
        settings.stripe_webhook_tolerance_seconds,
    )
    outcome = await run_in_threadpool(processor.process, event)
    return {"received": True, "outcome": outcome.value}
