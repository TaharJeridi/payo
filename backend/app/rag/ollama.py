import logging
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.errors import RagUnavailable
from app.rag.ports import ChatMessage, EmbedKind

logger = logging.getLogger(__name__)


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        *,
        chat_model: str,
        embed_model: str,
        timeout_seconds: float,
        query_prefix: str = "",
        document_prefix: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._chat_model = chat_model
        self._embed_model = embed_model
        self._prefixes: dict[EmbedKind, str] = {"query": query_prefix, "document": document_prefix}
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            transport=transport,
        )

    def embed(self, texts: Sequence[str], *, kind: EmbedKind = "document") -> list[list[float]]:
        if not texts:
            return []
        prefix = self._prefixes[kind]
        body = self._post("/api/embed", {"model": self._embed_model, "input": [prefix + t for t in texts]})
        embeddings = body.get("embeddings")
        if (
            not isinstance(embeddings, list)
            or len(embeddings) != len(texts)
            or not all(isinstance(v, list) and v for v in embeddings)
        ):
            raise RagUnavailable("Ollama returned malformed embeddings")
        return [[float(x) for x in vector] for vector in embeddings]

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        body = self._post(
            "/api/chat",
            {
                "model": self._chat_model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "stream": False,
                "options": {"temperature": 0},
            },
        )
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise RagUnavailable("Ollama returned a malformed chat response")
        return content.strip()

    def ping(self) -> None:
        try:
            self._client.get("/api/version").raise_for_status()
        except httpx.HTTPError as exc:
            raise RagUnavailable("Ollama is not reachable") from exc

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(path, json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("ollama_http_error path=%s status=%s", path, exc.response.status_code)
            raise RagUnavailable(f"Ollama answered {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("ollama_unreachable path=%s error=%s", path, type(exc).__name__)
            raise RagUnavailable("Ollama is not reachable") from exc
        if not isinstance(body, dict):
            raise RagUnavailable("Ollama returned an unexpected payload")
        return body
