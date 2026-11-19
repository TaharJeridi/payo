import hashlib
import math
import re
from collections.abc import Callable, Sequence

from app.core.errors import RagUnavailable
from app.rag.ports import ChatMessage, EmbedKind

_TOKEN = re.compile(r"[a-z0-9_]+")


class HashingEmbedder:
    """Deterministic bag-of-words embedding: texts sharing words get similar vectors."""

    def __init__(self, dimension: int = 256) -> None:
        self.dimension = dimension
        self.calls: list[list[str]] = []
        self.kinds: list[EmbedKind] = []
        self.fail = False

    def embed(self, texts: Sequence[str], *, kind: EmbedKind = "document") -> list[list[float]]:
        self.calls.append(list(texts))
        self.kinds.append(kind)
        if self.fail:
            raise RagUnavailable("Ollama is not reachable")
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in _TOKEN.findall(text.lower()):
            index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % self.dimension
            vector[index] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    @property
    def embedded_texts(self) -> int:
        return sum(len(c) for c in self.calls)


class FakeChat:
    def __init__(self, reply: str | Callable[[Sequence[ChatMessage]], str] = "Secondo i dati [1].") -> None:
        self.reply = reply
        self.calls: list[list[ChatMessage]] = []
        self.fail = False

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        self.calls.append(list(messages))
        if self.fail:
            raise RagUnavailable("Ollama is not reachable")
        return self.reply(messages) if callable(self.reply) else self.reply

    def ping(self) -> None:
        if self.fail:
            raise RagUnavailable("Ollama is not reachable")
