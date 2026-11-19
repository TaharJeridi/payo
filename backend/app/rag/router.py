from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.core.errors import RagUnavailable
from app.dependencies import CurrentMerchant, enforce_rag_rate_limit
from app.rag.dependencies import RagComponents, get_rag_components, get_rag_service
from app.rag.service import RagService

router = APIRouter(prefix="/rag", tags=["rag"])


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=8000)
    top_k: int | None = Field(default=None, ge=1, le=20)


class SourceRead(BaseModel):
    number: int
    title: str
    source_type: str
    source_key: str
    score: float
    cited: bool


class AskResponse(BaseModel):
    answer: str
    sources: list[SourceRead]
    used_llm: bool
    warnings: list[str] = Field(
        default_factory=list,
        description="Checks the answer did not pass (uncited, invalid_citation, unknown_payment_id, "
        "amount_not_in_sources, personal_data); empty when nothing was flagged or the backend wrote the answer.",
    )


@router.post("/ask", response_model=AskResponse, dependencies=[Depends(enforce_rag_rate_limit)])
async def ask(
    body: AskRequest,
    merchant: CurrentMerchant,
    service: Annotated[RagService, Depends(get_rag_service)],
) -> AskResponse:
    result = await run_in_threadpool(service.ask, merchant.id, body.question, body.top_k)
    return AskResponse(
        answer=result.answer,
        sources=[SourceRead.model_validate(s, from_attributes=True) for s in result.sources],
        used_llm=result.used_llm,
        warnings=result.warnings,
    )


@router.get("/health")
def health(response: Response, components: Annotated[RagComponents, Depends(get_rag_components)]) -> dict[str, str]:
    checks: dict[str, str] = {}
    for name, ping in (("ollama", components.ollama.ping), ("qdrant", components.store.ping)):
        try:
            ping()
            checks[name] = "ok"
        except RagUnavailable:
            checks[name] = "unavailable"
    checks["status"] = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    if checks["status"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return checks
