import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from qdrant_client import QdrantClient

from app.core.config import Settings, get_settings
from app.db.session import get_session_factory
from app.rag.indexer import RagIndexer
from app.rag.ollama import OllamaClient
from app.rag.qdrant_store import QdrantVectorStore
from app.rag.query_parser import QueryParser
from app.rag.service import RagService
from app.rag.structured import StructuredRetriever

TASK_PREFIXES: Mapping[str, tuple[str, str]] = {
    "nomic-embed-text": ("search_query: ", "search_document: "),
}


def embed_model_name(settings: Settings) -> str:
    return settings.ollama_embed_model.removesuffix(":latest")


def embed_prefixes(settings: Settings) -> tuple[str, str]:
    query, document = TASK_PREFIXES.get(embed_model_name(settings).split(":")[0], ("", ""))
    return (
        query if settings.ollama_embed_query_prefix is None else settings.ollama_embed_query_prefix,
        document if settings.ollama_embed_document_prefix is None else settings.ollama_embed_document_prefix,
    )


def collection_name(settings: Settings) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", embed_model_name(settings).lower()).strip("_")
    return f"{settings.qdrant_collection}__{slug}"[:255]


@dataclass(frozen=True, slots=True)
class RagComponents:
    ollama: OllamaClient
    store: QdrantVectorStore


def build_components(settings: Settings) -> RagComponents:
    query_prefix, document_prefix = embed_prefixes(settings)
    ollama = OllamaClient(
        settings.ollama_base_url,
        chat_model=settings.ollama_chat_model,
        embed_model=settings.ollama_embed_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        query_prefix=query_prefix,
        document_prefix=document_prefix,
    )
    client = QdrantClient(url=settings.qdrant_url, timeout=10)
    return RagComponents(ollama=ollama, store=QdrantVectorStore(client, collection_name(settings)))


@lru_cache
def get_rag_components() -> RagComponents:
    return build_components(get_settings())


def build_service(components: RagComponents, settings: Settings) -> RagService:
    return RagService(
        components.ollama,
        components.ollama,
        components.store,
        top_k=settings.rag_top_k,
        min_score=settings.rag_min_score,
        max_doc_chunks=settings.rag_max_doc_chunks,
        max_question_chars=settings.rag_max_question_chars,
        parser=QueryParser(),
        structured=StructuredRetriever(
            get_session_factory(),
            lookups=settings.rag_sql_lookup,
            aggregates=settings.rag_sql_aggregates,
            enriched_documents=settings.rag_document_facts,
        ),
    )


def get_rag_service() -> RagService:
    return build_service(get_rag_components(), get_settings())


def embedding_signature(settings: Settings) -> str:
    """Identifies the vector space: changing model, document prefix or collection re-indexes everything."""
    return f"{embed_model_name(settings)}|{embed_prefixes(settings)[1]}|{collection_name(settings)}"


def build_indexer(components: RagComponents, settings: Settings) -> RagIndexer:
    return RagIndexer(
        get_session_factory(),
        components.ollama,
        components.store,
        embed_model=embedding_signature(settings),
        docs_dir=Path(settings.rag_docs_dir),
        chunk_chars=settings.rag_chunk_chars,
        overlap_chars=settings.rag_chunk_overlap_chars,
        enriched_documents=settings.rag_document_facts,
    )
