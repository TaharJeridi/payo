"""Deterministic checks of the financial claims in a model answer (ADR 0014, TAH-143).

The backend gave the model numbered evidence. Payment evidence is text the backend generated from PostgreSQL
(`documents.payment_document`), so its values can be read back exactly. This module recognises a small set of
claims in the model's text, attributes each to one payment (entity → attribute → value → evidence) and compares it
with that payment's evidence. A value is never "verified" only because it appears somewhere in the context.

| Claim | Verdict when it does not hold | Warning |
|---|---|---|
| status of a payment | contradicted | ``contradicted_status`` |
| duration between two moments of a payment | contradicted | ``contradicted_duration`` |
| "there is no such payment", semantic route (top-k, never exhaustive) | unverifiable | ``unverified_absence`` |
| "the payment", while several evidence payments have the asked amount | unverifiable | ``unjustified_selection`` |
| a discount, promotion or coupon code that no source contains | unverifiable | ``unsupported_offer`` |

Recognition is deliberately narrow: a sentence that the patterns do not recognise is not checked (the answer is
``unchecked``, as before this module). Sentences about history ("era open, poi…") or with a negation before the
value are skipped, and durations about configuration (session lifetime, backoff) are not payment durations.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from app.payments.state import PaymentStatus
from app.rag.documents import format_duration
from app.rag.ports import ScoredChunk
from app.rag.validation import denies_existence

ClaimKind = Literal["status", "duration", "absence", "selection", "offer"]
ClaimVerdict = Literal["verified", "contradicted", "unverifiable"]
ReportVerdict = Literal["verified", "unchecked", "unverifiable", "contradicted"]

WARNING = {
    ("status", "contradicted"): "contradicted_status",
    ("duration", "contradicted"): "contradicted_duration",
    ("absence", "unverifiable"): "unverified_absence",
    ("selection", "unverifiable"): "unjustified_selection",
    ("offer", "unverifiable"): "unsupported_offer",
}

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?")
_STATUS_LINE = re.compile(r"^Stato: (\w+)", re.MULTILINE)
_PRODUCT_LINE = re.compile(r"^Prodotto: (.+?) \(", re.MULTILINE)
_AMOUNT_LINE = re.compile(r"^Importo: (?:.*?\()?(\d+)\.(\d{2}) ([A-Z]{3})\)?", re.MULTILINE)
_CREATED_LINE = re.compile(r"^Creato il: (\S+)", re.MULTILINE)
_PAID_LINE = re.compile(r"^Pagato il: (\S+)", re.MULTILINE)
_CITATION = re.compile(r"\[(\d{1,2})\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý\-*•\d\[])|\n+")

STATUS_WORDS: dict[str, PaymentStatus] = {
    "open": PaymentStatus.OPEN,
    "aperto": PaymentStatus.OPEN,
    "aperta": PaymentStatus.OPEN,
    "in attesa": PaymentStatus.OPEN,
    "in attesa di pagamento": PaymentStatus.OPEN,
    "awaiting payment": PaymentStatus.OPEN,
    "paid": PaymentStatus.PAID,
    "pagato": PaymentStatus.PAID,
    "pagata": PaymentStatus.PAID,
    "expired": PaymentStatus.EXPIRED,
    "scaduto": PaymentStatus.EXPIRED,
    "scaduta": PaymentStatus.EXPIRED,
    "failed": PaymentStatus.FAILED,
    "fallito": PaymentStatus.FAILED,
    "fallita": PaymentStatus.FAILED,
    "creation_unknown": PaymentStatus.CREATION_UNKNOWN,
    "creating": PaymentStatus.CREATING,
}
_STATUS_VALUE = "|".join(sorted((re.escape(w) for w in STATUS_WORDS), key=len, reverse=True))
# "lo stato è `open`", "its status is paid", "si trova nello stato «open»", "è in stato expired".
_STATUS_CLAIM = re.compile(
    r"(?:\b(?:stato|status|state)\b(?:\s+(?:attuale|corrente|current|finale|final|del pagamento"
    r"|of (?:the |this )?payment))?\s*(?:è|e'|is|risulta|:|=)\s*"
    r"|\b(?:si trova|è|is|risulta|currently|attualmente)\s+(?:attualmente\s+|currently\s+)?(?:in|nel|nello|nella)\s+"
    r"(?:stato|status|state)\s*"
    r"|\b(?:ha|has)\s+(?:un|uno|lo|il|a|the|its)?\s*(?:stato|status|state)\s*(?:di\s+|of\s+)?)"
    rf"[`\"'«“]?\s*(?P<value>{_STATUS_VALUE})\b",
    re.IGNORECASE,
)
_HISTORY = re.compile(
    r"\b(?:era|erano|prima|poi|inizialmente|precedentemente|passat[oa]|diventat[oa]|storico|cronologia|history"
    r"|before|then|initially|previously|became|was|were)\b|->|→",
    re.IGNORECASE,
)
_DURATION = re.compile(
    r"(?P<h>\d+)\s*(?:ore|ora|hours?|h)\b(?:\s*(?:,|e|and)?\s*(?P<hm>\d+)\s*(?:minut[oi]|minutes?|min)\b)?"
    r"|(?P<m>\d+)\s*(?:minut[oi]|minutes?|min)\b\.?(?:\s*(?:,|e|and)?\s*(?P<ms>\d+)\s*(?:second[oi]|seconds?|sec|s)\b)?"
    r"|(?P<s>\d+)\s*(?:second[oi]|seconds?|sec)\b",
    re.IGNORECASE,
)
_TIMING = re.compile(
    r"\b(?:creazion\w*|creat\w*|pagat\w*|paid|completat\w*|complet\w*|ci ha messo|ci ha impiegato|impiegat\w*"
    r"|took|elapsed|trascors\w*)\b",
    re.IGNORECASE,
)
_CONFIGURATION = re.compile(
    r"\b(?:dur[ae]\w*|lasts?|lifetime|scade dopo|expires? after|default|predefinit\w*|ogni|every|backoff|ttl"
    r"|timeout|fino a|up to|massim\w*|maximum|configur\w*|intervall\w*|interval)\b",
    re.IGNORECASE,
)
_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,6})[.,](\d{2})(?![\d])")
_SINGULAR = re.compile(r"\b(?:the|this|that) payment\b|\b(?:il|questo|quel) pagamento\b", re.IGNORECASE)
# The question is about one payment ("quel pagamento da 59,80", "the payment of Mario Rossi"): "there was no payment
# for it" is then a statement about that payment, not about the set of payments.
_PLURAL = re.compile(r"\bpayments\b|\bpagamenti\b", re.IGNORECASE)
_OFFER = re.compile(
    r"\b(?:codice (?:sconto|promo\w*|coupon)|discount code|promo(?:tional)? code|coupon|voucher)\b"
    r"|\b\d{1,3}\s?%\s?(?:di sconto|off\b|discount)"
    r"|\b(?:sconto|discount) (?:del|of) \d{1,3}\s?%"
    r"|\b(?:code|codice)\b[^.\n]{0,20}?\b[A-Z][A-Z0-9]{4,}\d+\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PaymentEvidence:
    number: int
    payment_id: str
    status: PaymentStatus | None
    product: str | None
    amount_cents: int | None
    currency: str | None
    created_at: datetime | None
    paid_at: datetime | None
    timestamps: tuple[datetime, ...]


@dataclass(frozen=True, slots=True)
class Claim:
    kind: ClaimKind
    text: str
    verdict: ClaimVerdict
    payment_id: str | None = None
    stated: str | None = None
    expected: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimReport:
    claims: tuple[Claim, ...] = ()
    payments: tuple[PaymentEvidence, ...] = field(default=())

    @property
    def verdict(self) -> ReportVerdict:
        verdicts = {c.verdict for c in self.claims}
        if "contradicted" in verdicts:
            return "contradicted"
        if "unverifiable" in verdicts:
            return "unverifiable"
        return "verified" if verdicts else "unchecked"

    @property
    def blocking(self) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if c.verdict != "verified")

    @property
    def warnings(self) -> list[str]:
        codes = (WARNING[(c.kind, c.verdict)] for c in self.blocking)
        return list(dict.fromkeys(codes))


def _time(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def payment_evidence(sources: Sequence[ScoredChunk]) -> list[PaymentEvidence]:
    """Read back the values of the payment sources the backend generated (numbered as given to the model)."""
    found = []
    for number, hit in enumerate(sources, start=1):
        chunk = hit.chunk
        if chunk.source_type != "payment" or not chunk.source_key.startswith("payment:"):
            continue
        text = chunk.text
        status = _STATUS_LINE.search(text)
        product = _PRODUCT_LINE.search(text)
        amount = _AMOUNT_LINE.search(text)
        created = _CREATED_LINE.search(text)
        paid = _PAID_LINE.search(text)
        try:
            state = PaymentStatus(status.group(1)) if status else None
        except ValueError:
            state = None
        moments = sorted({t for t in (_time(m) for m in _TIMESTAMP.findall(text)) if t is not None})
        found.append(
            PaymentEvidence(
                number=number,
                payment_id=chunk.source_key.removeprefix("payment:").lower(),
                status=state,
                product=product.group(1) if product else None,
                amount_cents=int(amount.group(1)) * 100 + int(amount.group(2)) if amount else None,
                currency=amount.group(3) if amount else None,
                created_at=_time(created.group(1)) if created else None,
                paid_at=_time(paid.group(1)) if paid else None,
                timestamps=tuple(moments),
            )
        )
    return found


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s and s.strip()]


def _subject(sentence: str, payments: Sequence[PaymentEvidence]) -> PaymentEvidence | None:
    """The one payment a sentence is about: an id in it, else a citation, else the only payment in the evidence."""
    low = sentence.lower()
    named = [p for p in payments if p.payment_id in low or re.search(rf"\b{p.payment_id[:8]}\b", low)]
    if len(named) == 1:
        return named[0]
    if named:
        return None
    cited = {int(n) for n in _CITATION.findall(sentence)}
    by_citation = [p for p in payments if p.number in cited]
    if len(by_citation) == 1:
        return by_citation[0]
    if by_citation:
        return None
    return payments[0] if len(payments) == 1 else None


def _status_claims(sentence: str, payments: Sequence[PaymentEvidence]) -> list[Claim]:
    if _HISTORY.search(sentence):
        return []
    claims = []
    for match in _STATUS_CLAIM.finditer(sentence):
        subject = _subject(sentence, payments)
        if subject is None or subject.status is None:
            continue
        stated = STATUS_WORDS[match.group("value").lower()]
        verdict: ClaimVerdict = "verified" if stated == subject.status else "contradicted"
        claims.append(Claim("status", sentence, verdict, subject.payment_id, stated.value, subject.status.value))
    return claims


def _seconds(match: re.Match[str]) -> tuple[int, int]:
    """Stated duration in seconds and its precision: "3 minuti" accepts 2:30–3:59, "3 minuti e 1 secondo" only 3:01."""
    if match.group("h"):
        hours, minutes = int(match.group("h")), int(match.group("hm") or 0)
        return hours * 3600 + minutes * 60, 60 if match.group("hm") else 3600
    if match.group("m"):
        minutes, seconds = int(match.group("m")), int(match.group("ms") or 0)
        return minutes * 60 + seconds, 1 if match.group("ms") else 60
    return int(match.group("s")), 1


def _duration_claims(sentence: str, payments: Sequence[PaymentEvidence]) -> list[Claim]:
    if not _TIMING.search(sentence) or _CONFIGURATION.search(sentence):
        return []
    subject = _subject(sentence, payments)
    if subject is None or len(subject.timestamps) < 2:
        return []
    moments = subject.timestamps
    intervals = {int(abs((b - a).total_seconds())) for i, a in enumerate(moments) for b in moments[i + 1 :]}
    claims = []
    for match in _DURATION.finditer(sentence):
        stated, tolerance = _seconds(match)
        verdict: ClaimVerdict = (
            "verified"
            if any(stated - tolerance // 2 <= interval <= stated + tolerance - 1 for interval in intervals)
            else "contradicted"
        )
        expected = None
        if subject.created_at and subject.paid_at:
            expected = format_duration(int((subject.paid_at - subject.created_at).total_seconds()))
        claims.append(Claim("duration", sentence, verdict, subject.payment_id, match.group(0), expected))
    return claims


def question_amounts(question: str) -> set[int]:
    return {int(a) * 100 + int(b) for a, b in _AMOUNT.findall(question)}


def _selection_claims(answer: str, question: str, payments: Sequence[PaymentEvidence]) -> list[Claim]:
    if _UUID.search(question.lower()) or not _SINGULAR.search(answer) or _PLURAL.search(answer):
        return []
    claims = []
    for cents in question_amounts(question):
        matching = [p for p in payments if p.amount_cents == cents]
        if len(matching) < 2:
            continue
        cited = {int(n) for n in _CITATION.findall(answer)}
        low = answer.lower()
        referred = [p for p in matching if p.number in cited or p.payment_id[:8] in low]
        if len(referred) <= 1:
            claims.append(Claim("selection", answer, "unverifiable", stated=f"{cents} cents, {len(matching)} payments"))
    return claims


def _offer_claims(answer: str, sources: Sequence[ScoredChunk]) -> list[Claim]:
    corpus = "\n".join(hit.chunk.text for hit in sources).lower()
    return [
        Claim("offer", match.group(0), "unverifiable", stated=match.group(0))
        for match in _OFFER.finditer(answer)
        if match.group(0).lower() not in corpus
    ]


def check_claims(
    answer: str, question: str, sources: Sequence[ScoredChunk], route: str, *, refusal: bool = False
) -> ClaimReport:
    """Recognise and check the financial claims of a model answer against the evidence it was given.

    ``refusal``: the answer declines (``looks_like_refusal``). Only invented offers are checked then, because a
    refusal can still be followed by generated content ("I can't…, but here is an example: use code X").
    """
    payments = payment_evidence(sources)
    if refusal:
        return ClaimReport(tuple(_offer_claims(answer, sources)), tuple(payments))
    claims: list[Claim] = []
    for sentence in _sentences(answer):
        claims += _status_claims(sentence, payments)
        claims += _duration_claims(sentence, payments)
    one_payment = bool(_UUID.search(question.lower()) or _SINGULAR.search(question))
    if route == "semantic" and not one_payment and denies_existence(answer):
        claims.append(Claim("absence", answer, "unverifiable"))
    claims += _selection_claims(answer, question, payments)
    claims += _offer_claims(answer, sources)
    return ClaimReport(tuple(claims), tuple(payments))
