import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]
EmbedKind = Literal["document", "query"]
PUBLIC_SCOPE = "public"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Chunk:
    point_id: uuid.UUID
    source_key: str
    source_type: str
    title: str
    text: str
    scope: str
    position: int
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: Chunk
    score: float


class EmbeddingModel(Protocol):
    def embed(self, texts: Sequence[str], *, kind: EmbedKind = "document") -> list[list[float]]: ...


class ChatModel(Protocol):
    def complete(self, messages: Sequence[ChatMessage]) -> str: ...


class VectorStore(Protocol):
    def ensure_collection(self, dimension: int) -> None: ...

    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None: ...

    def delete_source(self, source_key: str, keep_positions: int = 0) -> None: ...

    def search(self, vector: Sequence[float], *, scopes: Sequence[str], limit: int) -> list[ScoredChunk]: ...

    def fetch_sources(self, source_keys: Sequence[str], *, scopes: Sequence[str]) -> list[Chunk]: ...

    def ping(self) -> None: ...
