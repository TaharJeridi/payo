"""Retrospective check of ADR 0014 claim checks on saved Qwen answers (no model call).

Rebuilds the evidence each saved answer was generated from: payment sources are regenerated from the seed with
the current `payment_document` (the same text the backend gives Qwen), documentation sources are represented by
the full text of `docs/architecture.md` (enough for the offer check, which only asks whether a code appears in a
source). Then runs `check_claims` on every saved model answer and prints, per run and per warning, what would have
been blocked, followed by every flagged answer for manual review.

Needs a database with the seed (the test database is fine). From ``backend/``:
    PYTHONPATH=. python ../evaluation/tah143/claims_retrospective.py [run ...] > ../evaluation/tah143/retrospective.md
Without arguments it reads every saved run with `document_facts` on.
"""

import json
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from app.db.session import get_session_factory
from app.payments.models import Payment
from app.payments.repository import PaymentRepository
from app.rag.claims import check_claims
from app.rag.documents import payment_document
from app.rag.evaluation.seed import SEED_PAYMENTS, seed
from app.rag.language import looks_like_refusal
from app.rag.ports import Chunk, ScoredChunk

ROOT = Path(__file__).resolve().parents[2]


def documents() -> dict[str, str]:
    factory = get_session_factory()
    with factory() as session, session.begin():
        seed(session)
    with factory() as session:
        histories = PaymentRepository(session).histories([p.id for p in SEED_PAYMENTS])
        return {
            f"payment:{spec.id}": payment_document(session.get(Payment, spec.id), histories[spec.id]).text
            for spec in SEED_PAYMENTS
        }


def chunk(key: str, title: str, text: str, source_type: str) -> ScoredChunk:
    point = uuid.uuid5(uuid.NAMESPACE_URL, key)
    return ScoredChunk(
        chunk=Chunk(point, key, source_type, title, text, scope="x", position=0),
        score=1.0,
    )


def main(runs: list[str]) -> None:
    texts = documents()
    docs = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    paths = [ROOT / f"rag_eval_phase_{r}.json" for r in runs] if runs else sorted(ROOT.glob("rag_eval_phase_*.json"))
    totals: Counter[str] = Counter()
    by_run: dict[str, Counter[str]] = {}
    flagged: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    answers = 0
    for path in paths:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not saved["settings"].get("document_facts", True):
            continue
        name = path.stem.removeprefix("rag_eval_phase_")
        counts: Counter[str] = Counter()
        for result in saved["results"]:
            if not result.get("used_llm") or not result.get("answer"):
                continue
            answers += 1
            counts["model answers"] += 1
            sources = [
                chunk(c["source_key"], c["title"], texts.get(c["source_key"], docs), c["source_key"].split(":")[0])
                for c in result["selected"]
            ]
            refusal = looks_like_refusal(result["answer"])
            counts["model refusals (offers checked only)"] += refusal
            report = check_claims(result["answer"], result["question"], sources, result["route"], refusal=refusal)
            counts[f"verdict {report.verdict}"] += 1
            for claim in report.claims:
                counts[f"{claim.kind} {claim.verdict}"] += 1
            for code in report.warnings:
                counts[code] += 1
                totals[code] += 1
                detail = "; ".join(f"{c.kind}: stated {c.stated!r}, evidence {c.expected!r}" for c in report.blocking)
                key = (result["id"], code, " ".join(result["answer"].split())[:260] + " || " + detail)
                flagged[key].append(name)
        by_run[name] = counts
    print("# ADR 0014: claim checks on saved Qwen answers\n")
    print(f"Runs: {len(by_run)}. Model answers checked: {answers}.\n")
    keys = sorted({k for c in by_run.values() for k in c})
    print("| Run | " + " | ".join(keys) + " |")
    print("|---|" + "---|" * len(keys))
    for name, counts in by_run.items():
        print(f"| {name} | " + " | ".join(str(counts.get(k, 0)) for k in keys) + " |")
    print("\n## Answers that would be blocked\n")
    for (case_id, code, text), names in sorted(flagged.items()):
        print(f"- `{case_id}` **{code}** ({len(names)} runs: {', '.join(names[:6])}{'…' if len(names) > 6 else ''})")
        print(f"  - {text}")


if __name__ == "__main__":
    main(sys.argv[1:])
