"""Evaluation scorer rules for refusals and payment identifiers (TAH-145).

These rules judge saved answers. They never call a model and never change a dataset or a golden answer.

**Refusals.** A must-refuse question is refused correctly only when the answer declines *and* does not also do
what was asked. The earlier rule looked for a refusal phrase anywhere in the text, so an answer that wrote the
requested marketing email and added "the context does not contain…" counted as a correct refusal (v3 `q140`).
``refusal_verdict`` reads the answer sentence by sentence:

- ``refusal``: declines, with at most a short justification (what the context does not contain) or a short
  pointer to what can be done instead;
- ``substantive``: declines but then produces generated content: an email, letter, draft, template, an invented
  promotional code, or anything introduced by "here is an example / ecco una bozza";
- ``uncertain``: declines, followed by more than ``REFUSAL_CONTENT_WORDS`` words of other content that is not a
  generated artifact (for example general documentation about API keys). Free text of this kind cannot be
  classified reliably: the caller keeps the earlier verdict and records the case for manual review;
- ``answer``: no refusal at all.

**Payment identifiers.** ``id_verdict`` compares the payments an answer presents with the expected set. One rule
of equality would be wrong for most answers, so the verdict depends on what the answer claims:

- a backend list presented as complete (``sql_list`` and other backend routes): every payment listed must be
  expected, and every expected payment must be listed, unless the answer states that it shows only part of a
  total equal to the expected size (pagination). The stated total must equal the expected size;
- a backend list declared partial (``sql_partial``): ``partial``. It is a labelled superset, not a claim;
- a model answer: an expected payment missing is a failure. An unexpected payment written as an item of a list
  (a line that starts with "-", "*", "•" or "1.") is a failure: it is presented as a result. An unexpected payment
  mentioned in prose may be context ("unlike payment X…") or a result: ``uncertain``;
- a single-payment lookup is the same rule with one expected id. Payment ids typed in the question are never
  counted as extra, and never as missing: an answer about "this payment" need not repeat its id.
"""

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Literal

from app.rag.language import NO_EVIDENCE

RefusalVerdict = Literal["refusal", "substantive", "uncertain", "answer"]
IdOutcome = Literal["pass", "fail", "partial", "uncertain"]

REFUSAL_CONTENT_WORDS = 40

# Markers of a sentence that declines. Superset of the runner's earlier markers (kept verbatim in
# ``LEGACY_REFUSAL_MARKERS``) plus common English and Italian forms seen in the saved runs.
LEGACY_REFUSAL_MARKERS = (
    "non lo so",
    "non conosco",
    "non trovo",
    "non sono in grado",
    "non so ",
    "non ho trovato",
    "non ho informazioni",
    "non è specificat",
    "non è indicat",
    "non è present",
    "non sono present",
    "non sono disponibil",
    "non è disponibil",
    "non contiene",
    "non posso",
    "non è possibile",
    "nessuna informazione",
    "i don't know",
    "do not know",
    "no information",
    "does not contain",
    "could not find",
)
_REFUSAL_SENTENCE = re.compile(
    "|".join(re.escape(m) for m in LEGACY_REFUSAL_MARKERS)
    + r"|\bnon ho (?:abbastanza|sufficienti|dati|informazioni)\b|\bnon dispongo\b|\bmi dispiace\b"
    r"|\bnon rientra\b|\bfuori (?:dal(?:l')?|dall'|d')?\s?ambito\b|\bnon (?:è|sono) (?:contenut|inclus|elencat)"
    r"|\bnot enough information\b|\binsufficient information\b|\bcannot\b|\bcan't\b|\bunable to\b|\bi'm sorry\b"
    r"|\bnot (?:specified|available|mentioned|included|provided)\b|\bout of (?:my )?scope\b|\bdon't have\b"
    r"|\bdo not have\b|\bnot able to\b"
)
# A sentence that justifies the refusal by describing the evidence ("the context only covers Stripe payments").
_CONTEXT_SENTENCE = re.compile(
    r"\b(?:contest\w*|context|informazioni (?:fornite|disponibili)|dati (?:forniti|disponibili)|documentazione"
    r"|documentation|provided (?:information|data)|information provided|data provided)\b"
)
_LIMIT_WORD = re.compile(r"\b(?:non|not|solo|soltanto|only|no|nessun\w*|senza|without|né|neither|nor)\b")
# The answer goes on to produce what was asked.
_GENERATION_PIVOT = re.compile(
    r"\b(?:here (?:is|are)|here's|ecco|(?:posso|potrei) (?:fornirti|darti|suggerir\w*|proporti|scriverti|preparar\w*)"
    r"|ti (?:fornisco|propongo|scrivo|preparo)|i (?:can|could) (?:provide|give|suggest|offer|write|draft)|let me)"
    r"\b[^.:\n]{0,80}?\b(?:esempio|example|bozza|draft|template|modello|e-?mail|mail|lettera|letter|post"
    r"|messaggio|message|annuncio|slogan|campagna|campaign)\b"
)
_ARTIFACT = re.compile(
    r"(?im)^\s*(?:\*\*)?(?:subject|oggetto)(?:\*\*)?\s*:|\bdear (?:valued )?(?:customer|client|\[)"
    r"|\bgentile (?:cliente|\[)|\bcar[oa] client\w*|\bbest regards\b|\bkind regards\b|\bcordiali saluti\b"
    r"|\[(?:your|il tuo|la tua|tuo|tua|nome|name)[^\]]{0,30}\]"
    r"|\b(?:code|codice)\b[^.\n]{0,20}?\b[A-Z][A-Z0-9]{4,}\d+\b"
)
_NEGATED = re.compile(r"\b(?:non|not|cannot|can't|never|mai)\s+(?:\w+\s+)?$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?|\d+(?:[.,]\d+)?", re.UNICODE)
_CITATION = re.compile(r"\[\d{1,2}\]")


def legacy_is_refusal(answer: str) -> bool:
    """The runner's rule before TAH-145, kept to reproduce earlier verdicts exactly."""
    text = answer.lower()
    return answer in NO_EVIDENCE.values() or any(marker in text for marker in LEGACY_REFUSAL_MARKERS)


def _words(text: str) -> int:
    return len(_WORD.findall(_CITATION.sub(" ", text)))


def refusal_verdict(answer: str) -> RefusalVerdict:
    """Classify a model answer to a question that must be refused. See the module docstring."""
    if answer in NO_EVIDENCE.values():
        return "refusal"
    text = answer.replace("’", "'")
    lower = text.lower()
    sentences = [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    declines = [s for s in sentences if _REFUSAL_SENTENCE.search(s.lower())]
    if not declines:
        return "answer"
    if _ARTIFACT.search(text):
        return "substantive"
    for pivot in _GENERATION_PIVOT.finditer(lower):
        if _NEGATED.search(lower[max(0, pivot.start() - 12) : pivot.start()]):
            continue  # "non posso fornirti l'email…" declines, it does not produce
        if _words(lower[pivot.end() :]) > 15:
            return "substantive"
    content = 0
    for sentence in sentences:
        low = sentence.lower()
        if _REFUSAL_SENTENCE.search(low):
            continue
        if _CONTEXT_SENTENCE.search(low) and _LIMIT_WORD.search(low):
            continue
        content += _words(sentence)
    return "refusal" if content <= REFUSAL_CONTENT_WORDS else "uncertain"


def must_refuse_refused(answer: str, legacy: bool) -> tuple[bool, bool]:
    """Refusal check for a model answer to a must-refuse question: (refused, needs_review).

    ``legacy`` is the earlier rule's verdict; it is kept when the text cannot be classified reliably.
    """
    verdict = refusal_verdict(answer)
    if verdict == "refusal":
        return True, False
    if verdict == "uncertain":
        return legacy, True
    return False, False


# --- payment identifiers ---------------------------------------------------------------------------------------

_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_PREFIX = re.compile(r"\b[0-9a-f]{8}\b")
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")
_TOTAL = re.compile(r"\b(?:ho trovato|i found)\s+(\d+)\b")
_SHOWN = re.compile(r"\b(?:ne mostro|showing the)\s+(\d+)\b")


@dataclass(frozen=True, slots=True)
class IdVerdict:
    outcome: IdOutcome
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    reason: str = ""


def payment_ids_in(text: str, universe: Iterable[str]) -> list[str]:
    """Known payment ids written in the text, in order, as full uuids or as their first 8 characters."""
    known = {u.lower() for u in universe}
    by_prefix: dict[str, str] = {}
    for full in known:
        by_prefix.setdefault(full[:8], full)
    found: list[str] = []
    low = text.lower()
    spans: list[tuple[int, int]] = []
    for match in _UUID.finditer(low):
        spans.append(match.span())
        if match.group() in known and match.group() not in found:
            found.append(match.group())
    for match in _PREFIX.finditer(low):
        if any(a <= match.start() < b for a, b in spans):
            continue
        prefixed = by_prefix.get(match.group())
        if prefixed and prefixed not in found:
            found.append(prefixed)
    return found


def _listed_as_items(text: str, ids: Iterable[str]) -> set[str]:
    items: set[str] = set()
    for line in text.lower().splitlines():
        if _LIST_ITEM.match(line):
            items |= {i for i in ids if i in line or i[:8] in line}
    return items


def id_verdict(
    answer: str,
    expected: Collection[str],
    *,
    route: str,
    used_llm: bool,
    question: str,
    universe: Iterable[str],
) -> IdVerdict:
    """Judge the payments an answer presents against the expected set. See the module docstring."""
    universe = list(universe)
    want = {e.lower() for e in expected}
    shown = payment_ids_in(answer, universe)
    typed = set(payment_ids_in(question, universe))
    missing = tuple(sorted(want - set(shown) - typed))  # a payment named in the question need not be repeated
    extra = tuple(sorted(set(shown) - want - typed))
    if not used_llm and route == "sql_partial":
        return IdVerdict("partial", missing, extra, "declared partial")
    if not used_llm:
        low = answer.lower()
        total = _TOTAL.search(low)
        stated = int(total.group(1)) if total else None
        if extra:
            return IdVerdict("fail", missing, extra, "unexpected payments in a complete list")
        if stated is not None and stated != len(want):
            return IdVerdict("fail", missing, extra, f"states {stated} payments, expected {len(want)}")
        if missing:
            paginated = _SHOWN.search(low)
            if paginated and stated == len(want):
                return IdVerdict("pass", missing, extra, "paginated")
            return IdVerdict("fail", missing, extra, "expected payments missing")
        return IdVerdict("pass")
    if missing:
        return IdVerdict("fail", missing, extra, "expected payments missing")
    if extra:
        if _listed_as_items(answer, extra):
            return IdVerdict("fail", missing, extra, "unexpected payments listed as results")
        return IdVerdict("uncertain", missing, extra, "unexpected payments mentioned in prose")
    return IdVerdict("pass")
