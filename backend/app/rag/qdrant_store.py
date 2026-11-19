import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from qdrant_client import QdrantClient
from qdrant_client import models as qm
from qdrant_client.http.exceptions import ApiException, ResponseHandlingException

from app.core.errors import RagUnavailable
from app.rag.ports import Chunk, ScoredChunk

logger = logging.getLogger(__name__)

_QDRANT_ERRORS: tuple[type[Exception], ...] = (
    ApiException,
    ResponseHandlingException,
    httpx.HTTPError,
    ConnectionError,
    TimeoutError,
)


class QdrantVectorStore:
    def __init__(self, client: QdrantClient, collection: str) -> None:
        self._client = client
        self._collection = collection

    def ping(self) -> None:
        try:
            self._client.get_collections()
        except _QDRANT_ERRORS as exc:
            raise RagUnavailable("Qdrant is not reachable") from exc

    def ensure_collection(self, dimension: int) -> None:
        try:
            if self._client.collection_exists(self._collection):
                info = self._client.get_collection(self._collection)
                vectors = info.config.params.vectors
                size = vectors.size if isinstance(vectors, qm.VectorParams) else None
                if size != dimension:
                    raise RagUnavailable(
                        f"Qdrant collection '{self._collection}' has dimension {size}, embeddings have {dimension}"
                    )
                return
            self._client.create_collection(
                self._collection,
                vectors_config=qm.VectorParams(size=dimension, distance=qm.Distance.COSINE),
            )
            for key in ("scope", "source_key"):
                self._client.create_payload_index(self._collection, key, qm.PayloadSchemaType.KEYWORD)
        except _QDRANT_ERRORS as exc:
            raise RagUnavailable("Qdrant is not reachable") from exc

    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        if not chunks:
            return
        points = [
            qm.PointStruct(
                id=str(chunk.point_id),
                vector=list(vector),
                payload={
                    "source_key": chunk.source_key,
                    "source_type": chunk.source_type,
                    "title": chunk.title,
                    "text": chunk.text,
                    "scope": chunk.scope,
                    "position": chunk.position,
                    "metadata": dict(chunk.metadata),
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        try:
            self._client.upsert(self._collection, points=points, wait=True)
        except _QDRANT_ERRORS as exc:
            raise RagUnavailable("Qdrant upsert failed") from exc

    def delete_source(self, source_key: str, keep_positions: int = 0) -> None:
        must: list[qm.Condition] = [qm.FieldCondition(key="source_key", match=qm.MatchValue(value=source_key))]
        if keep_positions > 0:
            must.append(qm.FieldCondition(key="position", range=qm.Range(gte=keep_positions)))
        try:
            self._client.delete(
                self._collection,
                points_selector=qm.FilterSelector(filter=qm.Filter(must=must)),
                wait=True,
            )
        except _QDRANT_ERRORS as exc:
            raise RagUnavailable("Qdrant delete failed") from exc

    def fetch_sources(self, source_keys: Sequence[str], *, scopes: Sequence[str]) -> list[Chunk]:
        if not scopes:
            raise ValueError("A lookup must always be restricted to at least one scope")
        if not source_keys:
            return []
        query_filter = qm.Filter(
            must=[
                qm.FieldCondition(key="scope", match=qm.MatchAny(any=list(scopes))),
                qm.FieldCondition(key="source_key", match=qm.MatchAny(any=list(source_keys))),
            ]
        )
        try:
            records, _ = self._client.scroll(self._collection, scroll_filter=query_filter, limit=256, with_payload=True)
        except _QDRANT_ERRORS as exc:
            raise RagUnavailable("Qdrant lookup failed") from exc
        allowed = set(scopes)
        chunks = [self._chunk(r.id, r.payload or {}) for r in records]
        kept = [c for c in chunks if c.scope in allowed]
        if len(kept) != len(chunks):
            logger.error("qdrant_scope_violation lookup dropped=%s", len(chunks) - len(kept))
        return sorted(kept, key=lambda c: (c.source_key, c.position))

    def search(self, vector: Sequence[float], *, scopes: Sequence[str], limit: int) -> list[ScoredChunk]:
        if not scopes:
            raise ValueError("A vector search must always be restricted to at least one scope")
        query_filter = qm.Filter(must=[qm.FieldCondition(key="scope", match=qm.MatchAny(any=list(scopes)))])
        try:
            result = self._client.query_points(
                self._collection,
                query=list(vector),
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        except _QDRANT_ERRORS as exc:
            raise RagUnavailable("Qdrant search failed") from exc
        allowed = set(scopes)
        hits: list[ScoredChunk] = []
        for point in result.points:
            payload = point.payload or {}
            if payload.get("scope") not in allowed:
                logger.error("qdrant_scope_violation point_id=%s", point.id)
                continue
            hits.append(ScoredChunk(chunk=self._chunk(point.id, payload), score=float(point.score)))
        return hits

    @staticmethod
    def _chunk(point_id: object, payload: Mapping[str, Any]) -> Chunk:
        return Chunk(
            point_id=uuid.UUID(str(point_id)),
            source_key=str(payload.get("source_key", "")),
            source_type=str(payload.get("source_type", "")),
            title=str(payload.get("title", "")),
            text=str(payload.get("text", "")),
            scope=str(payload.get("scope", "")),
            position=int(payload.get("position", 0)),
            metadata={str(k): str(v) for k, v in (payload.get("metadata") or {}).items()},
        )
