"""Measure retrieval and answers of the RAG assistant on the seeded merchant.

Retrieval is judged on the ranked candidates before the score threshold (ranking quality)
and on what survives it (what the model actually sees). Answer checks are substring
heuristics: the report keeps every answer so a human can review the verdicts.
"""

import hashlib
import json
import re
import statistics
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.payments.filters import PaymentFilters
from app.payments.repository import PaymentRepository
from app.payments.state import PaymentStatus
from app.rag.evaluation.metrics import case_errors, case_metrics, reliability_summary
from app.rag.evaluation.scoring import id_verdict, must_refuse_refused
from app.rag.evaluation.seed import SEED_MERCHANTS, SEED_PAYMENTS, seed_payment_id
from app.rag.language import NO_EVIDENCE, detect_language, reply_language
from app.rag.ports import Chunk
from app.rag.service import RagService, Retrieval

CASES_PATH = Path(__file__).with_name("cases.json")
GENERALIZATION_PATH = Path(__file__).with_name("generalization.json")
GENERALIZATION_V2_PATH = Path(__file__).with_name("generalization_v2.json")
DATASETS = {
    "historical": CASES_PATH,
    "generalization": GENERALIZATION_PATH,
    "generalization-v2": GENERALIZATION_V2_PATH,
}
CATEGORIES = (
    "payment_by_id",
    "payment_lookup",
    "docs_it",
    "docs_en",
    "aggregate",
    "sql_lookup",
    "no_result",
    "security",
    "out_of_domain",
    "unanswerable",
)
REFUSAL_MARKERS = (
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
NUMBER_WORDS = [
    "zero",
    "uno",
    "due",
    "tre",
    "quattro",
    "cinque",
    "sei",
    "sette",
    "otto",
    "nove",
    "dieci",
    "undici",
    "dodici",
    "tredici",
    "quattordici",
    "quindici",
    "sedici",
    "diciassette",
    "diciotto",
    "diciannove",
    "venti",
]
DETERMINISTIC_REFUSALS = frozenset(
    {
        "no_match",
        "unsupported",
        "attribute_unavailable",
        "unrecognized_payment_set",
        "clarify",
        "unsupported_constraint",
        "out_of_scope",
    }
)
# Backend abstentions written in place of a model answer whose claims could not be verified (ADR 0014).
CLAIM_ABSTENTIONS = frozenset({"unverified_absence", "unjustified_selection", "unsupported_offer"})
_PLACEHOLDER = re.compile(r"\{(payment|merchant):([a-z0-9_]+)\}")


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    category: str
    question: str
    expect_sources: tuple[str, ...] = ()
    expect_answer: tuple[str, ...] = ()
    expect_refusal: bool = False
    expect_count: PaymentStatus | None = None
    expect_paid_total: str | None = None
    expect_intent: str | None = None
    expect_filters: dict[str, Any] | None = None
    expect_payments: tuple[str, ...] | None = None
    forbid_payments: tuple[str, ...] = ()
    expect_count_filters: dict[str, Any] | None = None
    expect_total_filters: dict[str, Any] | None = None
    group: str | None = None
    language: str = "it"
    allow_refusal: bool = False

    @property
    def answerable(self) -> bool:
        return not self.expect_refusal


@dataclass(frozen=True, slots=True)
class GroundTruth:
    counts: dict[PaymentStatus, int]
    paid_totals: dict[str, int]
    case_values: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class Candidate:
    title: str
    source_key: str
    score: float
    relevant: bool


@dataclass(slots=True)
class CaseResult:
    id: str
    category: str
    question: str
    candidates: list[Candidate]
    selected: list[Candidate]
    first_relevant_rank: int | None
    answer: str | None = None
    used_llm: bool | None = None
    cited_keys: list[str] = field(default_factory=list)
    expected_answer: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    diagnostics: dict[str, bool] = field(default_factory=dict)
    route: str = "semantic"
    intent: str | None = None
    group: str | None = None
    language: str = "it"
    warnings: list[str] = field(default_factory=list)
    latency_ms: int = 0
    review: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(self.checks.values())


def _known_refs() -> set[str]:
    return {p.ref for p in SEED_PAYMENTS}


@dataclass(frozen=True, slots=True)
class Dataset:
    name: str
    version: str
    sha256: str
    cases: list["Case"]


def load_dataset(name_or_path: str | Path) -> Dataset:
    path = DATASETS.get(str(name_or_path), Path(name_or_path))
    raw = path.read_bytes()
    document = json.loads(raw)
    meta = document if isinstance(document, dict) else {}
    return Dataset(
        name=meta.get("name", "historical" if path == CASES_PATH else path.stem),
        version=meta.get("version", "unversioned"),
        sha256=hashlib.sha256(raw).hexdigest(),
        cases=load_cases(path),
    )


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    refs = _known_refs()
    cases: list[Case] = []
    document = json.loads(path.read_text(encoding="utf-8"))
    for raw in document["cases"] if isinstance(document, dict) else document:
        category = raw["category"]
        if category not in CATEGORIES:
            raise ValueError(f"{raw['id']}: unknown category {category}")

        def resolve(match: re.Match[str], case_id: str = raw["id"]) -> str:
            kind, name = match.group(1), match.group(2)
            if kind == "merchant":
                if name not in SEED_MERCHANTS:
                    raise ValueError(f"{case_id}: unknown seed merchant {name}")
                return str(SEED_MERCHANTS[name][0])
            if name not in refs:
                raise ValueError(f"{case_id}: unknown seed payment {name}")
            return str(seed_payment_id(name))

        for ref in (*raw.get("expect_payments", []), *raw.get("forbid_payments", [])):
            if ref not in refs:
                raise ValueError(f"{raw['id']}: unknown seed payment {ref}")
        for key in ("expect_filters", "expect_count_filters", "expect_total_filters"):
            if key in raw and key != "expect_filters":
                PaymentFilters.model_validate(raw[key])

        for expected in raw.get("expect_sources", []):
            if expected.startswith("payment:") and expected.removeprefix("payment:") not in refs:
                raise ValueError(f"{raw['id']}: unknown seed payment in {expected}")
            if not expected.startswith(("payment:", "doc:")):
                raise ValueError(f"{raw['id']}: unsupported source {expected}")
        count = raw.get("expect_count")
        question = _PLACEHOLDER.sub(resolve, raw["question"])
        cases.append(
            Case(
                id=raw["id"],
                category=category,
                question=question,
                expect_sources=tuple(raw.get("expect_sources", [])),
                expect_answer=tuple(a.lower() for a in raw.get("expect_answer", [])),
                expect_refusal=bool(raw.get("expect_refusal", False)),
                expect_count=PaymentStatus(count) if count else None,
                expect_paid_total=raw.get("expect_paid_total"),
                expect_intent=raw.get("expect_intent"),
                expect_filters=raw.get("expect_filters"),
                expect_payments=tuple(raw["expect_payments"]) if "expect_payments" in raw else None,
                forbid_payments=tuple(raw.get("forbid_payments", [])),
                expect_count_filters=raw.get("expect_count_filters"),
                expect_total_filters=raw.get("expect_total_filters"),
                group=raw.get("group"),
                allow_refusal=bool(raw.get("allow_refusal", False)),
                language=raw.get("language") or reply_language(question),
            )
        )
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case ids")
    return cases


def matches(chunk: Chunk, expected: str) -> bool:
    if expected.startswith("payment:"):
        return chunk.source_key == f"payment:{seed_payment_id(expected.removeprefix('payment:'))}"
    file, _, section = expected.removeprefix("doc:").partition("#")
    if chunk.source_key != f"doc:{file}":
        return False
    return not section or section in chunk.title.split(" > ")


def filters_match(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Every expected key must equal the parsed value; lists compare as sets."""
    for key, want in expected.items():
        got = actual.get(key)
        if isinstance(want, dict):
            if not isinstance(got, dict) or any(got.get(k) != v for k, v in want.items()):
                return False
        elif isinstance(want, list):
            if not isinstance(got, list | tuple) or sorted(map(str, got)) != sorted(map(str, want)):
                return False
        elif got != want:
            return False
    return True


def compute_case_truth(session: Session, merchant_id: uuid.UUID, cases: Sequence[Case]) -> dict[str, list[str]]:
    """Expected answers computed with SQL from the seeded data, independently of the assistant."""
    repo = PaymentRepository(session)
    values: dict[str, list[str]] = {}
    for case in cases:
        if case.expect_count_filters is not None:
            counts = repo.count_by_status(merchant_id, PaymentFilters.model_validate(case.expect_count_filters))
            values[case.id] = count_forms(sum(counts.values()))
        elif case.expect_total_filters is not None:
            totals = repo.amount_totals(merchant_id, PaymentFilters.model_validate(case.expect_total_filters))
            values[case.id] = amount_forms(totals.get("eur", (0, 0))[1])
    return values


def is_refusal(answer: str) -> bool:
    text = answer.lower()
    return answer in NO_EVIDENCE.values() or any(marker in text for marker in REFUSAL_MARKERS)


ZERO_FORMS = (
    "nessun",
    "non ci sono",
    "non c'è",
    "non risulta",
    "non risultano",
    "there are no",
    "there were no",
    "none",
)


def count_forms(value: int) -> list[str]:
    forms = [str(value)]
    if value < len(NUMBER_WORDS):
        forms.append(NUMBER_WORDS[value])
    if value == 0:
        forms.extend(ZERO_FORMS)
    return forms


def amount_forms(cents: int) -> list[str]:
    whole, rest = divmod(cents, 100)
    grouped = f"{whole:,}"
    return [
        f"{whole}.{rest:02d}",
        f"{whole},{rest:02d}",
        f"{grouped.replace(',', '.')},{rest:02d}",
        f"{grouped}.{rest:02d}",
    ]


_NEGATION = re.compile(r"\b(?:non|not|never|nessun[oa]?|no)\b[^.!?\n]{0,15}$")


def contains_any(answer: str, needles: Iterable[str]) -> bool:
    """True when one expected term occurs and is not negated just before it ("non è stato pagato")."""
    text = answer.lower()
    for needle in needles:
        if needle.isdigit():
            if re.search(rf"(?<![\d.,]){re.escape(needle)}(?![\d]|[.,]\d)", text):
                return True
            continue
        for match in re.finditer(re.escape(needle), text):
            if not _NEGATION.search(text[max(0, match.start() - 25) : match.start()]):
                return True
    return False


def expected_answer(case: Case, truth: GroundTruth) -> list[str]:
    if case.id in truth.case_values:
        return truth.case_values[case.id]
    if case.expect_count is not None:
        return count_forms(truth.counts.get(case.expect_count, 0))
    if case.expect_paid_total is not None:
        return amount_forms(truth.paid_totals.get(case.expect_paid_total, 0))
    return list(case.expect_answer)


class Evaluator:
    def __init__(
        self,
        service: RagService,
        merchant_id: uuid.UUID,
        truth: GroundTruth,
        *,
        generate: bool,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._merchant_id = merchant_id
        self._truth = truth
        self._generate = generate
        self._clock = clock

    def run(self, cases: Sequence[Case]) -> list[CaseResult]:
        return [self.evaluate(case) for case in cases]

    def evaluate(self, case: Case) -> CaseResult:
        started = self._clock()
        retrieval = self._service.retrieve(self._merchant_id, case.question)
        result = self._retrieval_result(case, retrieval)
        result.route = retrieval.route
        result.intent = retrieval.plan.intent if retrieval.plan else None
        if case.expect_intent is not None:
            result.diagnostics["intent"] = result.intent == case.expect_intent
        if case.expect_filters is not None:
            parsed = retrieval.plan.filters.model_dump(mode="json") if retrieval.plan else {}
            result.diagnostics["filters"] = filters_match(parsed, case.expect_filters)
        if case.expect_sources:
            result.checks["retrieved"] = any(c.relevant for c in result.selected)
        if case.expect_payments is not None:
            expected = {str(seed_payment_id(ref)) for ref in case.expect_payments}
            shown = {
                c.source_key.removeprefix("payment:") for c in result.selected if c.source_key.startswith("payment:")
            }
            result.checks["payments"] = shown == expected
        forbidden = {str(seed_payment_id(ref)) for ref in case.forbid_payments}
        if forbidden:
            result.checks["isolated"] = not any(
                c.source_key.removeprefix("payment:") in forbidden for c in result.candidates
            )
        if case.expect_refusal:
            result.checks["no_context"] = not result.selected
        if self._generate:
            self._judge_answer(case, retrieval, result)
            leaked = {f for f in forbidden if f not in case.question}
            if leaked and result.answer is not None:
                result.checks["isolated"] = result.checks["isolated"] and not any(f in result.answer for f in leaked)
        result.latency_ms = int((self._clock() - started) * 1000)
        return result

    def _retrieval_result(self, case: Case, retrieval: Retrieval) -> CaseResult:
        def convert(hits: Sequence[Any]) -> list[Candidate]:
            return [
                Candidate(
                    title=h.chunk.title,
                    source_key=h.chunk.source_key,
                    score=round(h.score, 4),
                    relevant=any(matches(h.chunk, e) for e in case.expect_sources),
                )
                for h in hits
            ]

        candidates = convert(retrieval.candidates)
        rank = next((i for i, c in enumerate(candidates, start=1) if c.relevant), None)
        return CaseResult(
            id=case.id,
            category=case.category,
            group=case.group,
            language=case.language,
            question=case.question,
            candidates=candidates,
            selected=convert(retrieval.selected),
            first_relevant_rank=rank,
            expected_answer=expected_answer(case, self._truth),
        )

    def _judge_answer(self, case: Case, retrieval: Retrieval, result: CaseResult) -> None:
        answer = self._service.answer(retrieval)
        result.answer = answer.answer
        result.used_llm = answer.used_llm
        result.cited_keys = [s.source_key for s in answer.sources if s.cited]
        result.warnings = list(answer.warnings)
        inconclusive = retrieval.route == "sql_partial" and not retrieval.selected
        backend_abstention = bool(CLAIM_ABSTENTIONS.intersection(answer.warnings))
        refused = (
            is_refusal(answer.answer)
            or (not answer.used_llm and retrieval.route in DETERMINISTIC_REFUSALS)
            or inconclusive
            or backend_abstention
        )
        if case.expect_refusal:
            result.checks.pop("no_context", None)
            if answer.used_llm and not backend_abstention:
                # A refusal phrase next to the requested content is not a refusal (TAH-145).
                refused, review = must_refuse_refused(answer.answer, refused)
                if review:
                    result.review.append("refusal_uncertain")
            result.checks["refused"] = refused
            if refused:
                result.checks["refusal_language"] = detect_language(answer.answer) == reply_language(case.question)
            return
        if refused and case.allow_refusal:
            return
        result.checks["not_refused"] = not refused
        if not refused:
            result.checks["answer_language"] = detect_language(answer.answer) == reply_language(case.question)
        if result.expected_answer:
            result.checks["answer"] = contains_any(answer.answer, result.expected_answer)
        if case.expect_payments is not None and not refused and retrieval.route != "sql_aggregate":
            ids = id_verdict(
                answer.answer,
                [str(seed_payment_id(ref)) for ref in case.expect_payments],
                route=retrieval.route,
                used_llm=answer.used_llm,
                question=case.question,
                universe=[str(p.id) for p in SEED_PAYMENTS],
            )
            if ids.outcome in ("pass", "fail"):
                result.checks["answer_ids"] = ids.outcome == "pass"
            else:
                result.review.append(f"answer_ids_{ids.outcome}")
        if case.expect_sources:
            cited = [retrieval.selected[s.number - 1].chunk for s in answer.sources if s.cited]
            result.checks["cited_expected"] = any(matches(c, e) for c in cited for e in case.expect_sources)


@dataclass(frozen=True, slots=True)
class ThresholdAnalysis:
    current: float
    positives: list[float]
    negatives: list[float]
    not_ranked: int
    best_threshold: float | None
    best_balanced_accuracy: float | None
    current_balanced_accuracy: float | None


def balanced_accuracy(threshold: float, positives: Sequence[float], negatives: Sequence[float]) -> float:
    kept = sum(p >= threshold for p in positives) / len(positives)
    refused = sum(n < threshold for n in negatives) / len(negatives)
    return (kept + refused) / 2


def threshold_analysis(results: Sequence[CaseResult], cases: Sequence[Case], current: float) -> ThresholdAnalysis:
    """Separate "relevant chunk found" from "nothing relevant exists" with one cosine threshold.

    Positives are the score of the first relevant candidate for answerable questions; exact id
    lookups are excluded (fixed score 1.0) and so are questions whose relevant chunk was not
    ranked at all, which no threshold can fix. Negatives are the top score of questions that
    must be refused. Balanced accuracy keeps the two classes equally weighted.
    """
    by_id = {c.id: c for c in cases}
    positives: list[float] = []
    negatives: list[float] = []
    not_ranked = 0
    for result in results:
        case = by_id[result.id]
        if result.route != "semantic":
            continue
        if case.expect_refusal:
            negatives.append(result.candidates[0].score if result.candidates else 0.0)
        elif case.expect_sources and case.category != "payment_by_id":
            relevant = [c.score for c in result.candidates if c.relevant]
            if relevant:
                positives.append(relevant[0])
            else:
                not_ranked += 1
    if not positives or not negatives:
        return ThresholdAnalysis(current, sorted(positives), sorted(negatives), not_ranked, None, None, None)
    scores = sorted({*positives, *negatives})
    thresholds = [scores[0] - 1e-3] + [(a + b) / 2 for a, b in zip(scores, scores[1:], strict=False)]
    thresholds.append(scores[-1] + 1e-3)
    best = max(thresholds, key=lambda t: (balanced_accuracy(t, positives, negatives), -t))
    return ThresholdAnalysis(
        current=current,
        positives=sorted(positives),
        negatives=sorted(negatives),
        not_ranked=not_ranked,
        best_threshold=round(best, 4),
        best_balanced_accuracy=round(balanced_accuracy(best, positives, negatives), 3),
        current_balanced_accuracy=round(balanced_accuracy(current, positives, negatives), 3),
    )


def _rate(values: Sequence[bool]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


CHECKS = (
    "retrieved",
    "payments",
    "isolated",
    "no_context",
    "refused",
    "refusal_language",
    "not_refused",
    "answer_language",
    "answer",
    "answer_ids",
    "cited_expected",
)


def _warning_summary(results: Sequence[CaseResult]) -> dict[str, Any]:
    codes: dict[str, int] = defaultdict(int)
    for result in results:
        for code in result.warnings:
            codes[code] += 1
    uncited_by_eval = [r for r in results if r.checks.get("cited_expected") is False]
    flagged = [r for r in uncited_by_eval if r.warnings]
    clean_but_flagged = [r.id for r in results if r.passed and r.warnings]
    return {
        "by_code": dict(codes),
        "answers_with_warnings": sum(1 for r in results if r.warnings),
        "eval_citation_failures_flagged": f"{len(flagged)}/{len(uncited_by_eval)}",
        "passing_answers_with_warnings": clean_but_flagged,
    }


def summarize(results: Sequence[CaseResult], cases: Sequence[Case], *, min_score: float) -> dict[str, Any]:
    by_id = {c.id: c for c in cases}
    categories: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        grouped[result.category].append(result)
    for category in CATEGORIES:
        items = grouped.get(category, [])
        if not items:
            continue
        ranked = [r for r in items if by_id[r.id].expect_sources]
        ranks = [r.first_relevant_rank for r in ranked]
        stats: dict[str, Any] = {"cases": len(items), "passed": sum(r.passed for r in items)}
        if ranked:
            stats["hit@1"] = _rate([rank == 1 for rank in ranks])
            stats["hit@3"] = _rate([rank is not None and rank <= 3 for rank in ranks])
            stats["hit@k"] = _rate([rank is not None for rank in ranks])
            stats["mrr"] = round(statistics.fmean(1 / rank if rank else 0.0 for rank in ranks), 3)
        for check in CHECKS:
            values = [r.checks[check] for r in items if check in r.checks]
            if values:
                stats[check] = _rate(values)
        categories[category] = stats
    analysis = threshold_analysis(results, cases, min_score)
    routes: dict[str, int] = defaultdict(int)
    for result in results:
        routes[result.route] += 1
    parser = {
        name: _rate([r.diagnostics[name] for r in results if name in r.diagnostics]) for name in ("intent", "filters")
    }
    reliability = reliability_summary([_as_dict(r) for r in results], by_id)
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "categories": categories,
        "routes": dict(routes),
        "warnings": _warning_summary(results),
        "parser_accuracy": parser,
        "threshold": asdict(analysis),
        "latency_ms": reliability["latency_ms"],
        "reliability": {k: v for k, v in reliability.items() if k != "latency_ms"},
    }


def _as_dict(result: CaseResult) -> dict[str, Any]:
    return asdict(result) | {"passed": result.passed}


def _short(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_text(results: Sequence[CaseResult], summary: dict[str, Any], settings: dict[str, Any]) -> str:
    lines = ["RAG evaluation", "=" * 14]
    lines.append(" ".join(f"{k}={v}" for k, v in settings.items()))
    lines.append(f"cases={summary['cases']} passed={summary['passed']}")
    lines.append("")
    columns = ("cases", "passed", "hit@1", "hit@3", "mrr", *CHECKS)
    lines.append(f"{'category':<15}" + "".join(f"{c:>15}" for c in columns))
    for category, stats in summary["categories"].items():
        cells = "".join(f"{'' if stats.get(c) is None else stats[c]!s:>15}" for c in columns)
        lines.append(f"{category:<15}{cells}")

    lines.append("")
    lines.append("routes: " + " ".join(f"{k}={v}" for k, v in sorted(summary["routes"].items())))
    parser = summary["parser_accuracy"]
    lines.append(f"parser: intent_accuracy={parser['intent']} filter_accuracy={parser['filters']}")
    warn = summary["warnings"]
    lines.append(
        f"answer warnings: {warn['answers_with_warnings']} answers {warn['by_code']}; "
        f"eval citation failures flagged {warn['eval_citation_failures_flagged']}; "
        f"passing answers with warnings: {warn['passing_answers_with_warnings']}"
    )
    th = summary["threshold"]
    lines += ["", "Score threshold (RAG_MIN_SCORE)"]
    lines.append(f"  current={th['current']} balanced_accuracy={th['current_balanced_accuracy']}")
    lines.append(f"  best={th['best_threshold']} balanced_accuracy={th['best_balanced_accuracy']}")
    lines.append(f"  answerable questions whose relevant chunk was not ranked: {th['not_ranked']}")
    lines.append(f"  relevant chunk score (answerable): {th['positives']}")
    lines.append(f"  top score (should refuse):         {th['negatives']}")
    latency = summary["latency_ms"]
    lines.append(f"latency_ms mean={latency['mean']} p95={latency['p95']} max={latency['max']}")
    reliability = summary["reliability"]
    lines += ["", f"Reliability metrics (passed/applicable; llm_calls={reliability['llm_calls']})"]
    for name, metric in reliability["metrics"].items():
        lines.append(
            f"  {name:<24}{metric['passed']}/{metric['applicable']}  not_applicable={metric['not_applicable']}"
        )
    lines.append(f"  false_refusal_rate={reliability['false_refusal_rate']}")
    errors = {k: v for k, v in reliability["errors"].items() if v}
    lines.append("errors: " + ("; ".join(f"{k}={', '.join(v)}" for k, v in errors.items()) or "none"))
    if reliability["by_group"]:
        lines += ["", "By group (passed/cases)"]
        for group, stats in reliability["by_group"].items():
            lines.append(f"  {group:<24}{stats['passed']}/{stats['cases']}")

    misparsed = [r for r in results if not all(r.diagnostics.values())]
    if misparsed:
        lines += ["", f"Parser mismatches ({len(misparsed)})"]
        for result in misparsed:
            wrong = [name for name, ok in result.diagnostics.items() if not ok]
            lines.append(f"- {result.id}: {', '.join(wrong)} (intent={result.intent}) Q: {result.question}")
    failures = [r for r in results if not r.passed]
    lines += ["", f"Failures ({len(failures)})"]
    for result in failures:
        failed = [name for name, ok in result.checks.items() if not ok]
        lines.append(f"- [{result.category}] {result.id}: {', '.join(failed)} (route={result.route})")
        lines.append(f"    Q: {result.question}")
        lines.append(f"    rank of expected source: {result.first_relevant_rank}")
        for candidate in result.candidates[:3]:
            mark = "*" if candidate.relevant else " "
            lines.append(f"    {mark} {candidate.score:.3f}  {_short(candidate.title, 90)}")
        if result.expected_answer:
            lines.append(f"    expected one of: {result.expected_answer}")
        if result.answer is not None:
            lines.append(f"    A: {_short(result.answer)}")
    return "\n".join(lines)


def render_json(
    results: Sequence[CaseResult],
    summary: dict[str, Any],
    settings: dict[str, Any],
    cases: Sequence[Case] | None = None,
) -> str:
    by_id = {c.id: c for c in cases or ()}
    rows = []
    for result in results:
        row = _as_dict(result)
        if result.id in by_id:
            row["metrics"] = case_metrics(row, by_id[result.id])
            row["errors"] = case_errors(row, by_id[result.id])
        rows.append(row)
    payload = {"settings": settings, "summary": summary, "results": rows}
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
