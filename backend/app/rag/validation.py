"""Deterministic checks on an answer written by the model.

The checks never change the text: they return warning codes that the API and the
evaluation expose, so an answer that cannot be verified is not presented as certified.

- ``uncited``: the answer has content but no valid ``[n]`` citation.
- ``invalid_citation``: a citation points outside the numbered sources.
- ``unknown_payment_id``: a payment id in the answer is neither among the sources nor in the
  question.
- ``personal_data``: the answer contains an email address.

Amounts written with a currency (``89,70 €``, ``EUR 89.70``, ``$12``) are classified by their
role in the sentence and verified against different evidence:

- threshold or range (``supera i 90 €``, ``between €10 and €50``): must equal the bound of the
  amount filter the backend applied with SQL, with the same direction, the same currency and,
  when the wording is unambiguous, the same strictness. Without an applied filter it cannot be
  verified: ``threshold_unverified``. An amount typed in the question is never evidence by
  itself.
- aggregate (``totale``, ``incassato``, ``in total``) not tied to a single payment: must equal an
  amount computed by the backend (``sql:*`` sources): ``aggregate_unverified``.
- transactional (any other amount): must be the amount or unit price of the payment it is
  attributed to (payment id or citation nearest to it), or appear in the cited documentation.
  Found only in another payment: ``amount_wrong_payment``; found nowhere: ``amount_not_in_sources``.

Not verifiable here: whether a cited source actually supports the sentence that cites it,
whether a status word in free text matches the database, and an amount written without a
currency.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.payments.filters import PaymentFilters
from app.rag.ports import ScoredChunk
from app.rag.query_parser import parse_amount

_CITATION = re.compile(r"\[(\d{1,2})\]")
_CITATION_RUN = re.compile(r"(?:\[\d{1,2}\]\s*)+")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_NUMBER = r"\d[\d.,]*\d|\d"
_CURRENCY = r"€|\$|£|\beur\b|\beuro\b|\busd\b|\bdollari\b|\bdollars?\b|\bgbp\b|\bsterline\b|\bpounds?\b"
_MONEY = re.compile(
    rf"(?:(?P<pre>{_CURRENCY})\s*(?P<a>{_NUMBER}))|(?:(?P<b>{_NUMBER})\s*(?P<post>{_CURRENCY}))",
    re.IGNORECASE,
)
_CURRENCY_CODES = {
    "€": "eur",
    "eur": "eur",
    "euro": "eur",
    "$": "usd",
    "usd": "usd",
    "dollari": "usd",
    "dollar": "usd",
    "dollars": "usd",
    "£": "gbp",
    "gbp": "gbp",
    "sterline": "gbp",
    "pound": "gbp",
    "pounds": "gbp",
}
_SEGMENT = re.compile(r"\n+|(?<=[.!?;:])\s+")
_IMPORTO = re.compile(r"^(?:importo|amount):.*$", re.IGNORECASE | re.MULTILINE)

_FILLER = (
    r"(?:\s+(?:il|i|la|le|lo|gli|l'|a|ad|ai|al|alla|agli|di|dei|del|degli|da|the|of|than|to|"
    r"limite|soglia|valore|importo|importi|limit|threshold|value|amount|amounts)){0,3}\s*$"
)
_COMPARATORS: tuple[tuple[re.Pattern[str], str, bool | None], ...] = tuple(
    (re.compile(rf"(?<![\w])(?:{words}){_FILLER}"), side, inclusive)
    for words, side, inclusive in (
        (
            r"pari o superiore|superior[ei] o ugual[ei]|maggior[ei] o ugual[ei]|non inferior[ei]|almeno|minimo|"
            r"at least|no less|greater than or equal|≥|>=",
            "min",
            True,
        ),
        (
            r"pari o inferiore|inferior[ei] o ugual[ei]|minor[ei] o ugual[ei]|non superior[ei]|non oltre|"
            r"al massimo|massimo|"
            r"at most|no more|less than or equal|≤|<=",
            "max",
            True,
        ),
        (r"oltre|sopra|over", "min", None),
        (r"fino|up", "max", None),
        (r"superior[ei]|maggior[ei]|più|piu|supera\w*|eccede\w*|above|more|greater|higher|exceed\w*|>", "min", False),
        (r"inferior[ei]|minor[ei]|meno|sotto|below|under|less|lower|<", "max", False),
    )
)
_RANGE_LOW = re.compile(r"\b(?:tra|fra|between|compres[io] tra)\s*$")
_RANGE_FROM = re.compile(r"\b(?:da|from)\s*$")
_BARE_RANGE = re.compile(rf"\b(?:tra|fra|between|da|from)\s+(?P<low>{_NUMBER})\s*(?:e|ed|and|a|to|-|–)\s*$")
_RANGE_JOIN = re.compile(r"^\s*(?:e|ed|and|a|to|-|–)\s*$")
_RANGE_JOIN_FROM = re.compile(r"^\s*(?:a|to|-|–)\s*$")
_AGGREGATE = re.compile(
    r"\b(?:totale|totali|total|totals|complessiv\w*|somma|sum|incassat\w*|incasso|collected|revenue|"
    r"fatturat\w*|in tutto|altogether|overall|cumulativ\w*)\b"
)


@dataclass(frozen=True, slots=True)
class Money:
    cents: int
    currency: str


@dataclass(frozen=True, slots=True)
class AmountMention:
    money: Money
    start: int
    end: int
    role: str
    side: str | None = None
    inclusive: bool | None = None


def _currency(marker: str) -> str:
    return _CURRENCY_CODES[marker.lower()]


def money_in(text: str) -> list[tuple[Money, int, int]]:
    found = []
    for match in _MONEY.finditer(_UUID.sub(lambda m: " " * len(m.group(0)), text)):
        cents = parse_amount(match.group("a") or match.group("b"))
        if cents is not None:
            found.append((Money(cents, _currency(match.group("pre") or match.group("post"))), *match.span()))
    return found


def amounts_in(text: str) -> set[int]:
    return {money.cents for money, _, _ in money_in(text)}


def classify(segment: str) -> list[AmountMention]:
    """Role of each currency-marked amount in one sentence: threshold, range bound, aggregate or value."""
    lowered = segment.lower()
    found = money_in(segment)
    mentions: list[AmountMention] = []
    for money, start, end in found:
        before = lowered[max(0, start - 60) : start]
        if mentions:
            previous = mentions[-1]
            gap = lowered[previous.end : start]
            joined = (previous.role == "range_low" and _RANGE_JOIN.match(gap)) or (
                previous.role == "range_from" and _RANGE_JOIN_FROM.match(gap)
            )
            if joined:
                mentions[-1] = replace(previous, role="range_low", side="min")
                mentions.append(AmountMention(money, start, end, "range_high", "max"))
                continue
        bare = _BARE_RANGE.search(before)
        low = parse_amount(bare.group("low")) if bare else None
        if bare is not None and low is not None:
            offset = max(0, start - 60)
            mentions.append(
                AmountMention(
                    Money(low, money.currency), offset + bare.start("low"), offset + bare.end("low"), "range_low", "min"
                )
            )
            mentions.append(AmountMention(money, start, end, "range_high", "max"))
            continue
        if _RANGE_LOW.search(before):
            mentions.append(AmountMention(money, start, end, "range_low", "min"))
            continue
        comparator = next(((side, inclusive) for p, side, inclusive in _COMPARATORS if p.search(before)), None)
        if comparator is not None:
            mentions.append(AmountMention(money, start, end, "threshold", *comparator))
            continue
        if _RANGE_FROM.search(before):
            mentions.append(AmountMention(money, start, end, "range_from"))
            continue
        role = "aggregate" if _AGGREGATE.search(lowered[max(0, start - 40) : start]) else "value"
        mentions.append(AmountMention(money, start, end, role))
    return [replace(m, role="value") if m.role == "range_from" else m for m in mentions]


def _segments(answer: str) -> list[str]:
    return [s for s in _SEGMENT.split(answer) if s.strip()]


def _source_amounts(source: ScoredChunk) -> set[Money]:
    text = source.chunk.text
    if source.chunk.source_type == "payment":
        line = _IMPORTO.search(text)
        if line is not None:
            return {money for money, _, _ in money_in(line.group(0))}
    return {money for money, _, _ in money_in(text)}


def _bound_matches(mention: AmountMention, filters: PaymentFilters | None) -> bool:
    if filters is None or filters.amount is None:
        return False
    if filters.currency is not None and mention.money.currency != filters.currency:
        return False
    amount = filters.amount
    if mention.side == "min":
        bound, inclusive = amount.minimum, amount.minimum_inclusive
    else:
        bound, inclusive = amount.maximum, amount.maximum_inclusive
    if bound is None or bound != mention.money.cents:
        return False
    return mention.inclusive is None or mention.role.startswith("range") or mention.inclusive == inclusive


class _Anchors:
    def __init__(self, sources: Sequence[ScoredChunk], answer: str) -> None:
        self._sources = sources
        self._by_payment = {
            s.chunk.source_key.removeprefix("payment:").lower(): n
            for n, s in enumerate(sources, start=1)
            if s.chunk.source_type == "payment"
        }
        cited = sorted({int(n) for n in _CITATION.findall(answer) if 1 <= int(n) <= len(sources)})
        self.fallback = cited or list(range(1, len(sources) + 1))

    def for_amount(self, segment: str, mention: AmountMention) -> tuple[list[int], bool]:
        """Source numbers the amount is attributed to, and whether the attribution is specific."""
        ids = [
            (m.start(), self._by_payment[m.group(0).lower()])
            for m in _UUID.finditer(segment)
            if m.group(0).lower() in self._by_payment
        ]
        if ids:
            before = [n for at, n in ids if at < mention.start]
            return [before[-1] if before else ids[0][1]], True
        citations = [
            (m.start(), int(m.group(1)))
            for m in _CITATION.finditer(segment)
            if 1 <= int(m.group(1)) <= len(self._sources)
        ]
        if citations:
            after = [at for at, _ in citations if at >= mention.end]
            if after:
                run = _CITATION_RUN.match(segment, after[0])
                found = _CITATION.findall(run.group(0)) if run is not None else []
                return [int(n) for n in found if 1 <= int(n) <= len(self._sources)], True
            return [citations[-1][1]], True
        return self.fallback, False

    def source(self, number: int) -> ScoredChunk:
        return self._sources[number - 1]


def _check_amounts(answer: str, sources: Sequence[ScoredChunk], applied: PaymentFilters | None) -> list[str]:
    anchors = _Anchors(sources, answer)
    every = set().union(*(_source_amounts(s) for s in sources)) if sources else set()
    problems: list[str] = []
    for segment in _segments(answer):
        for mention in classify(segment):
            if mention.role in ("threshold", "range_low", "range_high"):
                if not _bound_matches(mention, applied):
                    problems.append("threshold_unverified")
                continue
            numbers, specific = anchors.for_amount(segment, mention)
            attributed = [anchors.source(n) for n in numbers]
            payments = [s for s in attributed if s.chunk.source_type == "payment"]
            if mention.role == "aggregate" and not (len(attributed) == 1 and payments):
                computed = [s for s in attributed if s.chunk.source_type == "sql"]
                if not any(mention.money in _source_amounts(s) for s in computed):
                    problems.append("aggregate_unverified")
                continue
            if any(mention.money in _source_amounts(s) for s in attributed):
                continue
            problems.append("amount_wrong_payment" if specific and mention.money in every else "amount_not_in_sources")
    return list(dict.fromkeys(problems))


_DENIAL = re.compile(
    r"\b(?:non ci sono|non c'è|non c'e|non esist\w*|non (?:ho )?trov\w*|nessun\w*|there (?:are|is|were|was) no"
    r"|no explicit|could not find|did not find|cannot find|can't find|none of)"
    r"(?:\s+(?!issues?\b|problem\w*|error\w*|errori\b|anomali\w*)[\w'\"«»-]+){0,6}?"
    r"\s+(?:pagament\w*|transazion\w*|payments?|transactions?)\b"
)


def denies_existence(answer: str) -> bool:
    """True when the text states that no (other) payment exists or was found."""
    return bool(_DENIAL.search(answer.lower().replace("’", "'")))


def validate_answer(
    answer: str,
    question: str,
    sources: Sequence[ScoredChunk],
    *,
    applied_filters: PaymentFilters | None = None,
) -> list[str]:
    warnings: list[str] = []
    numbers = [int(n) for n in _CITATION.findall(answer)]
    valid = sorted({n for n in numbers if 1 <= n <= len(sources)})
    if any(not 1 <= n <= len(sources) for n in numbers):
        warnings.append("invalid_citation")
    if not valid:
        warnings.append("uncited")

    known = {s.chunk.source_key.removeprefix("payment:").lower() for s in sources}
    known |= {m.lower() for m in _UUID.findall(question)}
    if any(m.lower() not in known for m in _UUID.findall(answer)):
        warnings.append("unknown_payment_id")

    warnings += _check_amounts(answer, sources, applied_filters)

    if _EMAIL.search(answer):
        warnings.append("personal_data")
    return warnings
