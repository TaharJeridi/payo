"""RAG evaluation on synthetic data.

python -m app.rag.evaluation seed                       # insert the eval merchant and its payments
python -m app.rag.index                                 # index them, then:
python -m app.rag.evaluation run                        # retrieval + Qwen answers
python -m app.rag.evaluation run --retrieval-only       # no LLM calls, seconds instead of minutes
python -m app.rag.evaluation run --min-score 0.5 --format json > report.json
python -m app.rag.evaluation run --dataset generalization   # independent, frozen dataset
python -m app.rag.evaluation report --dataset historical --runs a.json b.json c.json --baseline x.json > report.md
"""

import argparse
import hashlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db import models  # noqa: F401
from app.db.session import get_session_factory
from app.payments.models import Payment
from app.payments.repository import PaymentRepository
from app.rag.dependencies import build_components, build_service, embedding_signature
from app.rag.documents import payment_source_key
from app.rag.evaluation.report import load_run, render_report
from app.rag.evaluation.runner import (
    CATEGORIES,
    DATASETS,
    Evaluator,
    GroundTruth,
    compute_case_truth,
    load_dataset,
    render_json,
    render_text,
    summarize,
)
from app.rag.evaluation.seed import OTHER_MERCHANT_ID, SEED_MERCHANT_ID, SEED_PAYMENTS, seed
from app.rag.models import RagSource

JSON_MARKER = "=====JSON====="


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.rag.evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="insert the synthetic eval merchant and payments (idempotent)")
    run = sub.add_parser("run", help="evaluate the assistant on the seeded merchant")
    run.add_argument("--retrieval-only", action="store_true", help="skip the LLM")
    run.add_argument("--format", choices=("text", "json", "both"), default="text", help="both: text, marker, JSON")
    run.add_argument("--category", action="append", choices=CATEGORIES)
    run.add_argument("--min-score", type=float)
    run.add_argument("--top-k", type=int)
    run.add_argument("--max-doc-chunks", type=int)
    run.add_argument("--dataset", default="historical", help=f"{' | '.join(DATASETS)} or a path")
    report = sub.add_parser("report", help="Markdown report over saved JSON runs")
    report.add_argument("--dataset", default="historical", help=f"{' | '.join(DATASETS)} or a path")
    report.add_argument("--runs", nargs="+", type=Path, required=True)
    report.add_argument("--baseline", nargs="*", type=Path, default=[])
    report.add_argument("--code-version")
    return parser


def code_fingerprint(root: Path = Path(__file__).resolve().parents[2]) -> str:
    """sha256 of the application's Python sources: identifies the code when git is not available."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def index_problems(session: Session, embed_model: str) -> list[str]:
    problems = []
    sources = {
        s.source_key: s
        for s in session.execute(
            select(RagSource).where(RagSource.scope.in_([str(SEED_MERCHANT_ID), str(OTHER_MERCHANT_ID), "public"]))
        )
        .scalars()
        .all()
    }
    payments = session.execute(select(Payment).where(Payment.id.in_([p.id for p in SEED_PAYMENTS]))).scalars().all()
    if len(payments) < len(SEED_PAYMENTS):
        problems.append(f"{len(SEED_PAYMENTS) - len(payments)} seed payments missing: run the seed command")
    stale = [
        p.id
        for p in payments
        if (source := sources.get(payment_source_key(p.id))) is None
        or source.embed_model != embed_model
        or source.source_version is None
        or source.source_version < p.updated_at
    ]
    if stale:
        problems.append(f"{len(stale)} seed payments not indexed: run python -m app.rag.index")
    if not any(s.source_type == "doc" and s.embed_model == embed_model for s in sources.values()):
        problems.append("no documentation indexed with the current embedding model: run python -m app.rag.index")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging("WARNING")
    if args.command == "report":
        dataset = load_dataset(args.dataset)
        runs = [load_run(path) for path in args.runs]
        baseline = [load_run(path) for path in args.baseline]
        print(render_report(dataset, runs, baseline, code_version=args.code_version), end="")
        return 0

    settings = get_settings()
    factory = get_session_factory()

    if args.command == "seed":
        with factory() as session, session.begin():
            report = seed(session)
        print(f"seed {report.summary()} merchant_id={SEED_MERCHANT_ID}")
        return 0

    overrides = {
        name: value
        for name, value in (
            ("rag_min_score", args.min_score),
            ("rag_top_k", args.top_k),
            ("rag_max_doc_chunks", args.max_doc_chunks),
        )
        if value is not None
    }
    settings = settings.model_copy(update=overrides)
    dataset = load_dataset(args.dataset)
    cases = dataset.cases
    with factory() as session:
        problems = index_problems(session, embedding_signature(settings))
        counts, paid = PaymentRepository(session).stats_for_merchant(SEED_MERCHANT_ID)
        case_values = compute_case_truth(session, SEED_MERCHANT_ID, cases)
    if problems:
        for problem in problems:
            print(f"not ready: {problem}", file=sys.stderr)
        return 2

    if args.category:
        cases = [c for c in cases if c.category in args.category]
    if args.retrieval_only:
        cases = [
            c
            for c in cases
            if c.expect_sources or c.expect_refusal or c.expect_payments is not None or c.forbid_payments
        ]

    service = build_service(build_components(settings), settings)
    evaluator = Evaluator(
        service, SEED_MERCHANT_ID, GroundTruth(counts, paid, case_values), generate=not args.retrieval_only
    )
    results = evaluator.run(cases)
    summary = summarize(results, cases, min_score=settings.rag_min_score)
    shown = {
        "chat_model": "-" if args.retrieval_only else settings.ollama_chat_model,
        "embed_model": settings.ollama_embed_model,
        "min_score": settings.rag_min_score,
        "top_k": settings.rag_top_k,
        "max_doc_chunks": settings.rag_max_doc_chunks,
        "document_facts": settings.rag_document_facts,
        "sql_lookup": settings.rag_sql_lookup,
        "sql_aggregates": settings.rag_sql_aggregates,
        "dataset": dataset.name,
        "dataset_version": dataset.version,
        "dataset_sha256": dataset.sha256,
        "code_fingerprint": code_fingerprint(),
        "code_version": os.environ.get("PAYFLOW_CODE_VERSION") or None,
    }
    if args.format in ("text", "both"):
        print(render_text(results, summary, shown))
    if args.format == "both":
        print(JSON_MARKER)
    if args.format in ("json", "both"):
        print(render_json(results, summary, shown, cases))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
