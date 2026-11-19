"""Language of a question, for the messages the backend writes itself.

Only Italian and English are recognised; Italian is the default because PayFlow's merchants
write in Italian. Text in another script (e.g. CJK) is reported as "other".
"""

import re
from collections.abc import Mapping
from typing import Literal

Language = Literal["it", "en", "other"]

_WORD = re.compile(r"[a-zàèéìòù']+")
_ITALIAN = frozenset(
    (
        "il",
        "lo",
        "la",
        "i",
        "gli",
        "le",
        "di",
        "del",
        "della",
        "dei",
        "delle",
        "che",
        "è",
        "e",
        "sono",
        "quali",
        "quale",
        "quanti",
        "quanto",
        "come",
        "perché",
        "cosa",
        "non",
        "un",
        "una",
        "per",
        "con",
        "nel",
        "nella",
        "pagamento",
        "pagamenti",
        "trova",
        "mostrami",
        "ho",
        "hai",
        "mio",
        "miei",
        "sul",
        "dopo",
    )
)
_ENGLISH = frozenset(
    (
        "the",
        "an",
        "of",
        "is",
        "are",
        "was",
        "were",
        "which",
        "what",
        "how",
        "why",
        "who",
        "many",
        "much",
        "does",
        "do",
        "did",
        "not",
        "for",
        "with",
        "in",
        "on",
        "payment",
        "payments",
        "find",
        "show",
        "my",
        "me",
        "you",
        "there",
        "this",
        "that",
        "could",
        "found",
        "these",
        "matching",
        "status",
        "amount",
        "reason",
        "yes",
        # Function words common in short English questions with no other marker (TAH-144: v3 q036, r017).
        "has",
        "have",
        "had",
        "any",
        "been",
        "since",
        "where",
        "someone",
        "anyone",
        "to",
        "from",
        "and",
        "or",
        "by",
        "at",
        "it",
        "can",
        "will",
        "would",
        "orders",
        "units",
        "bought",
        "between",
    )
)
_IDENTIFIER = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\b\w*_\w*\b|`[^`]*`")
_LATIN = re.compile(r"[A-Za-zÀ-ÿ]")
_LETTER = re.compile(r"[^\W\d_]")


def detect_language(text: str) -> Language:
    letters = _LETTER.findall(text)
    if letters and sum(not _LATIN.match(c) for c in letters) / len(letters) > 0.3:
        return "other"
    words = _WORD.findall(_IDENTIFIER.sub(" ", text.lower()))
    italian = sum(w in _ITALIAN for w in words)
    english = sum(w in _ENGLISH for w in words)
    return "en" if english > italian else "it"


def reply_language(question: str) -> Literal["it", "en"]:
    return "en" if detect_language(question) == "en" else "it"


NO_EVIDENCE: Mapping[str, str] = {
    "it": "Non ho trovato informazioni sufficienti nei dati PayFlow disponibili per rispondere a questa domanda.",
    "en": "I could not find enough information in the available PayFlow data to answer this question.",
}


_REFUSAL = re.compile(
    r"\bnon (?:lo )?so\b|\bnon conosco\b|\bnon (?:ho|trovo) (?:trovato )?(?:informazioni|dati)\b"
    r"|\bnon (?:è|sono) (?:specificat|indicat|present|disponibil|elencat)|\bnon (?:sono in grado|posso)\b"
    r"|\bdo not know\b|\bdon't know\b|\bnot (?:specified|available|mentioned)\b|\bno information\b"
    r"|\bcould not find\b|\bcannot (?:find|answer|determine)\b|不知道|没有找到|无法|未找到"
)


def looks_like_refusal(text: str) -> bool:
    """The model's answer declines to answer (Italian, English, or common Chinese wordings)."""
    return bool(_REFUSAL.search(text.lower()))


FOUND_WITHOUT_ANSWER: Mapping[str, str] = {
    "it": "Nei dati dei pagamenti trovati non c'è l'informazione richiesta.",
    "en": "The payments found do not contain the requested information.",
}
