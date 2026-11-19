import os
import uuid

import httpx
import pytest
from qdrant_client import QdrantClient

from app.core.config import get_settings
from app.rag.ollama import OllamaClient
from app.rag.ports import PUBLIC_SCOPE, Chunk
from app.rag.qdrant_store import QdrantVectorStore
from app.rag.service import RagService

pytestmark = pytest.mark.rag_live


def _blocked_reason() -> str | None:
    if os.environ.get("RAG_INTEGRATION") != "1":
        return "BLOCKED: set RAG_INTEGRATION=1 with Ollama and Qdrant running"
    settings = get_settings()
    for name, url in (("Ollama", f"{settings.ollama_base_url}/api/version"), ("Qdrant", f"{settings.qdrant_url}/")):
        try:
            httpx.get(url, timeout=3).raise_for_status()
        except httpx.HTTPError as exc:
            return f"BLOCKED: {name} unreachable at {url} ({type(exc).__name__})"
    return None


@pytest.fixture(scope="module")
def live():
    reason = _blocked_reason()
    if reason:
        pytest.skip(reason)
    settings = get_settings()
    ollama = OllamaClient(
        settings.ollama_base_url,
        chat_model=settings.ollama_chat_model,
        embed_model=settings.ollama_embed_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )
    collection = f"payflow_it_{uuid.uuid4().hex[:8]}"
    client = QdrantClient(url=settings.qdrant_url)
    yield ollama, QdrantVectorStore(client, collection)
    client.delete_collection(collection)
    client.close()


def test_real_ollama_and_qdrant_answer_from_context(live):
    ollama, store = live
    merchant_scope = str(uuid.uuid4())
    texts = [
        ("payment:it-1", merchant_scope, "Payment it-1: status expired, the Stripe Checkout Session expired."),
        ("payment:it-2", str(uuid.uuid4()), "Payment it-2: status paid, belongs to another merchant."),
        ("doc:it", PUBLIC_SCOPE, "Reconciliation retries Stripe errors with exponential backoff."),
    ]
    vectors = ollama.embed([t for _, _, t in texts])
    store.ensure_collection(len(vectors[0]))
    chunks = [Chunk(uuid.uuid4(), key, key.split(":")[0], key, body, scope, 0) for key, scope, body in texts]
    store.upsert(chunks, vectors)

    service = RagService(ollama, ollama, store, top_k=3, min_score=0.0, max_question_chars=500)
    answer = service.ask(uuid.UUID(merchant_scope), "What is the status of payment it-1?")

    assert answer.used_llm is True
    assert "payment:it-2" not in {s.source_key for s in answer.sources}
    assert "expired" in answer.answer.lower()
