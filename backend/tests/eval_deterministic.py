"""Deterministic part of an evaluation: parser, SQL route and backend-written answers, without models.

The embedder is a hashing fake and the chat model is never trusted: semantic retrieval and model
answers are not measured, only the checks that do not depend on them. Runs on the test database:

    docker compose --profile test run --rm tests python -m tests.eval_deterministic generalization out.json
"""

import json
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from qdrant_client import QdrantClient

from app.db.session import get_session_factory
from app.payments.repository import PaymentRepository
from app.rag.evaluation.runner import Evaluator, GroundTruth, compute_case_truth, load_dataset
from app.rag.evaluation.seed import SEED_MERCHANT_ID, seed
from app.rag.indexer import RagIndexer
from app.rag.qdrant_store import QdrantVectorStore
from app.rag.query_parser import QueryParser
from app.rag.service import RagService
from app.rag.structured import StructuredRetriever
from tests.rag_fakes import FakeChat, HashingEmbedder

ANSWER_CHECKS = ("answer", "refused", "not_refused", "refusal_language", "answer_language")


def measure(dataset_name: str) -> dict:
    dataset = load_dataset(dataset_name)
    factory = get_session_factory()
    with factory() as session, session.begin():
        seed(session)
    with factory() as session:
        counts, paid = PaymentRepository(session).stats_for_merchant(SEED_MERCHANT_ID)
        values = compute_case_truth(session, SEED_MERCHANT_ID, dataset.cases)
    docs = Path(tempfile.mkdtemp())
    (docs / "architecture.md").write_text("# PayFlow\n\n## Placeholder\nNo documentation in this run.\n")
    client = QdrantClient(location=":memory:")
    store = QdrantVectorStore(client, "deterministic")
    embedder = HashingEmbedder()
    model = f"fake-{datetime.now(UTC).timestamp()}"
    RagIndexer(factory, embedder, store, embed_model=model, docs_dir=docs, chunk_chars=400, overlap_chars=40).run()
    service = RagService(
        embedder,
        FakeChat(),
        store,
        top_k=5,
        min_score=2.0,
        max_question_chars=500,
        parser=QueryParser(lambda: datetime.now(UTC)),
        structured=StructuredRetriever(factory),
    )
    evaluator = Evaluator(service, SEED_MERCHANT_ID, GroundTruth(counts, paid, values), generate=True)
    rows = []
    for case in dataset.cases:
        result = evaluator.evaluate(case)
        structured = result.route != "semantic"
        deterministic = result.used_llm is False and result.route != "semantic"
        measured = {k: v for k, v in result.diagnostics.items()}
        measured |= {k: result.checks[k] for k in ("payments", "isolated") if structured and k in result.checks}
        if deterministic:
            measured |= {k: result.checks[k] for k in ANSWER_CHECKS if k in result.checks}
        rows.append(
            {
                "id": result.id,
                "group": result.group or result.category,
                "language": result.language,
                "route": result.route,
                "intent": result.intent,
                "measured": measured,
                "deterministic_answer": deterministic,
                "answer": result.answer if deterministic else None,
                "pending_on_mac": sorted(set(result.checks) - set(measured)),
            }
        )
    client.close()
    return {
        "dataset": dataset.name,
        "version": dataset.version,
        "sha256": dataset.sha256,
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": rows,
    }


def summary(payload: dict) -> str:
    groups: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for row in payload["rows"]:
        stats = groups[row["group"]]
        for key in ("intent", "filters", "payments"):
            if key in row["measured"]:
                stats[key][0] += row["measured"][key]
                stats[key][1] += 1
        if row["deterministic_answer"]:
            answer = [v for k, v in row["measured"].items() if k in (*ANSWER_CHECKS, "isolated")]
            stats["answers"][0] += all(answer)
            stats["answers"][1] += 1
    lines = [f"{payload['dataset']} {payload['version']} sha256={payload['sha256'][:12]} cases={len(payload['rows'])}"]
    lines.append(f"{'group':24}{'intent':>10}{'filters':>10}{'payments':>10}{'answers':>10}")
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for name, stats in sorted(groups.items()):
        cells = []
        for key in ("intent", "filters", "payments", "answers"):
            ok, n = stats[key]
            totals[key][0] += ok
            totals[key][1] += n
            cells.append(f"{ok}/{n}" if n else "-")
        lines.append(f"{name:24}" + "".join(f"{c:>10}" for c in cells))
    lines.append(f"{'total':24}" + "".join(f"{f'{ok}/{n}':>10}" for ok, n in totals.values()))
    return "\n".join(lines)


if __name__ == "__main__":
    result = measure(sys.argv[1] if len(sys.argv) > 1 else "generalization")
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(summary(result))
