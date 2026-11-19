"""Incremental indexing of PayFlow data into Qdrant.

``rag_sources`` remembers, per source, the hash of what was embedded (content + embedding
signature: model and document prefix). A source is re-embedded only when that hash
changes; chunks that no longer exist are deleted. Point ids are derived from ``source_key``
and chunk position, so re-running is idempotent. A PostgreSQL advisory lock keeps a single
indexer active at a time.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import String, cast, delete, literal, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.payments.models import Payment
from app.payments.repository import PaymentRepository
from app.rag.chunking import split_markdown, split_text
from app.rag.documents import SourceDocument, markdown_documents, payment_document
from app.rag.models import RagSource
from app.rag.ports import Chunk, EmbeddingModel, VectorStore

logger = logging.getLogger(__name__)

POINT_NAMESPACE = uuid.UUID("2f1d5f63-0b7c-4c55-9b0a-6d1f2f6a0c11")
ADVISORY_LOCK_KEY = 7_316_042_001
EMBED_BATCH = 32


@dataclass
class IndexReport:
    indexed: int = 0
    unchanged: int = 0
    deleted: int = 0
    chunks: int = 0
    skipped_locked: bool = False
    sources: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"indexed={self.indexed} unchanged={self.unchanged} deleted={self.deleted} "
            f"chunks={self.chunks} skipped_locked={self.skipped_locked}"
        )


def point_id(source_key: str, position: int) -> uuid.UUID:
    return uuid.uuid5(POINT_NAMESPACE, f"{source_key}#{position}")


class RagIndexer:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        embedder: EmbeddingModel,
        store: VectorStore,
        *,
        embed_model: str,
        docs_dir: Path,
        chunk_chars: int,
        overlap_chars: int,
        batch_size: int = 200,
        enriched_documents: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._store = store
        self._embed_model = embed_model
        self._docs_dir = docs_dir
        self._chunk_chars = chunk_chars
        self._overlap_chars = overlap_chars
        self._batch_size = batch_size
        self._collection_ready = False
        self._enriched = enriched_documents

    def run(self) -> IndexReport:
        report = IndexReport()
        with self._session_factory() as lock_session:
            locked = lock_session.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
            ).scalar_one()
            if not locked:
                report.skipped_locked = True
                return report
            try:
                self._index_documents(markdown_documents(self._docs_dir), "doc", report)
                self._index_payments(report)
            finally:
                lock_session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
                lock_session.commit()
        logger.info("rag_index_run %s", report.summary())
        return report

    def _index_documents(self, documents: Sequence[SourceDocument], source_type: str, report: IndexReport) -> None:
        present = {d.source_key for d in documents}
        for document in documents:
            self._index_one(document, report)
        with self._session_factory() as session, session.begin():
            stale = session.execute(
                select(RagSource.source_key).where(
                    RagSource.source_type == source_type, RagSource.source_key.not_in(present or {""})
                )
            ).scalars()
            for source_key in list(stale):
                self._store.delete_source(source_key)
                session.execute(delete(RagSource).where(RagSource.source_key == source_key))
                report.deleted += 1

    def _index_payments(self, report: IndexReport) -> None:
        while True:
            with self._session_factory() as session:
                payments = (
                    session.execute(
                        select(Payment)
                        .outerjoin(RagSource, RagSource.source_key == literal("payment:") + cast(Payment.id, String))
                        .where(
                            or_(
                                RagSource.source_key.is_(None),
                                RagSource.source_version.is_(None),
                                RagSource.embed_model != self._embed_model,
                                Payment.updated_at > RagSource.source_version,
                            )
                        )
                        .order_by(Payment.updated_at, Payment.id)
                        .limit(self._batch_size)
                    )
                    .scalars()
                    .all()
                )
                documents = [
                    payment_document(p, PaymentRepository(session).history(p.id), enriched=self._enriched)
                    for p in payments
                ]
            if not documents:
                return
            for document in documents:
                self._index_one(document, report)
            if len(documents) < self._batch_size:
                return

    def _index_one(self, document: SourceDocument, report: IndexReport) -> None:
        content_hash = document.content_hash(self._embed_model)
        with self._session_factory() as session, session.begin():
            existing = session.get(RagSource, document.source_key)
            if existing is not None and existing.content_hash == content_hash:
                existing.source_version = document.version
                report.unchanged += 1
                return

        chunks = self._chunk(document)
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), EMBED_BATCH):
            vectors += self._embedder.embed([c.text for c in chunks[start : start + EMBED_BATCH]], kind="document")
        if chunks and not self._collection_ready:
            self._store.ensure_collection(len(vectors[0]))
            self._collection_ready = True
        self._store.upsert(chunks, vectors)
        self._store.delete_source(document.source_key, keep_positions=len(chunks))

        with self._session_factory() as session, session.begin():
            row = session.get(RagSource, document.source_key)
            if row is None:
                row = RagSource(source_key=document.source_key)
                session.add(row)
            row.source_type = document.source_type
            row.scope = document.scope
            row.content_hash = content_hash
            row.embed_model = self._embed_model
            row.chunk_count = len(chunks)
            row.source_version = document.version
            row.indexed_at = datetime.now(UTC)
        report.indexed += 1
        report.chunks += len(chunks)
        report.sources.append(document.source_key)

    def _chunk(self, document: SourceDocument) -> list[Chunk]:
        if document.is_markdown:
            pieces = [
                (f"{document.title} > {c.heading}" if c.heading else document.title, c.text)
                for c in split_markdown(document.text, max_chars=self._chunk_chars, overlap_chars=self._overlap_chars)
            ]
        else:
            pieces = [(document.title, t) for t in split_text(document.text, self._chunk_chars, self._overlap_chars)]
        return [
            Chunk(
                point_id=point_id(document.source_key, position),
                source_key=document.source_key,
                source_type=document.source_type,
                title=title,
                text=f"{title}\n{body}",
                scope=document.scope,
                position=position,
                metadata=document.metadata,
            )
            for position, (title, body) in enumerate(pieces)
        ]
