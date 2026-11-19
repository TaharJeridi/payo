"""TAH-136: answers written by the backend follow the language of the question.

Only the wording changes with the language: figures, citations, routes and sources are the same.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.db.session import get_session_factory
from app.rag.evaluation.seed import OTHER_MERCHANT_ID, SEED_MERCHANT_ID
from app.rag.indexer import RagIndexer
from app.rag.language import detect_language
from app.rag.qdrant_store import QdrantVectorStore
from app.rag.query_parser import QueryParser
from app.rag.service import RagService
from app.rag.structured import StructuredRetriever
from tests.rag_fakes import FakeChat, HashingEmbedder
from tests.test_sql_first import NOW, seeded  # noqa: F401  (fixture)

# The same question in Italian and in English, with the figures both answers must contain.
PAIRS = [
    ("Quanti pagamenti sono scaduti?", "How many payments have expired?", ["3"]),
    ("Quanti pagamenti ho?", "How many payments do I have?", ["20", "10", "3"]),
    ("Quanto ho incassato in totale?", "How much have I collected in total?", ["444,70 €", "10"]),
    ("Quanto valgono i pagamenti scaduti in totale?", "What is the total value of expired payments?", ["98,70 €", "3"]),
    ("Qual è il prodotto più venduto?", "What is the best-selling product?", ["PayFlow Starter", "7", "196,00 €"]),
    ("Quali pagamenti sono falliti?", "Which payments failed?", ["checkout_session_not_created"]),
    ("Quanto è stato rimborsato?", "How much were refunds?", []),
]


def numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:,\d+)?", text)


@pytest.fixture
def run(seeded):  # noqa: F811
    retriever = StructuredRetriever(get_session_factory())
    parser = QueryParser(lambda: NOW)

    def _run(question: str):
        result = retriever.execute(SEED_MERCHANT_ID, parser.parse(question))
        assert result is not None and result.direct_answer is not None
        return result

    return _run


@pytest.mark.parametrize(("italian", "english", "figures"), PAIRS)
def test_backend_answers_follow_the_question_language_with_the_same_figures(run, italian, english, figures):
    it, en = run(italian), run(english)
    assert detect_language(it.direct_answer) == "it"
    assert detect_language(en.direct_answer) == "en"
    assert it.route == en.route
    assert [c.chunk.source_key for c in it.evidence] == [c.chunk.source_key for c in en.evidence]
    for figure in figures:
        assert figure in it.direct_answer and figure in en.direct_answer
    assert numbers(it.direct_answer) == numbers(en.direct_answer)
    assert re.findall(r"\[\d+\]", it.direct_answer) == re.findall(r"\[\d+\]", en.direct_answer)


def test_english_aggregates_have_english_wording_and_source_titles(run):
    count = run("How many payments have expired?")
    assert count.direct_answer == "You have 3 payments with status expired [1]."
    assert count.evidence[0].chunk.title == "Data calculated with SQL: count"
    assert "Total: 3." in count.evidence[0].chunk.text

    total = run("How much have I collected in total?")
    assert total.direct_answer == "You collected 444,70 € gross, from 10 payments with status paid [1]."
    assert "not a net figure" in total.evidence[0].chunk.text

    filtered = run("How many PayFlow Starter payments were paid?")
    assert filtered.direct_answer == "You have 3 payments with status paid, filtered by product PayFlow Starter [1]."

    ranking = run("Rank the products by units sold").direct_answer
    assert "Ranking by units sold (payments with status paid):\n1. PayFlow Starter: 7 units, 69,30 €" in ranking


def test_italian_aggregates_are_unchanged(run):
    assert run("Quanti pagamenti sono scaduti?").direct_answer == "Hai 3 pagamenti scaduti [1]."
    total = run("Quanto ho incassato in totale?")
    assert total.direct_answer == "Hai incassato 444,70 € lordi, da 10 pagamenti con stato pagato [1]."
    assert total.evidence[0].chunk.title == "Dati calcolati con SQL: totale"


def test_tenant_notice_follows_the_question_language(run):
    en = run(f"How many payments from merchant {OTHER_MERCHANT_ID} failed?").direct_answer
    assert en.endswith("another merchant named in the question is ignored.")
    assert detect_language(en) == "en"
    it = run(f"Quanti pagamenti del merchant {OTHER_MERCHANT_ID} sono falliti?").direct_answer
    assert it.endswith("un altro merchant citato nella domanda viene ignorato.")


@pytest.fixture
def service(tmp_path: Path, seeded) -> Iterator[tuple[RagService, FakeChat]]:  # noqa: F811
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text("# PayFlow\n\n## Refunds\nRefunds are not modelled yet.\n")
    client = QdrantClient(location=":memory:")
    embedder = HashingEmbedder()
    store = QdrantVectorStore(client, "test_answer_language")
    RagIndexer(
        get_session_factory(), embedder, store, embed_model="fake", docs_dir=docs, chunk_chars=400, overlap_chars=40
    ).run()
    chat = FakeChat("unused [1].")
    yield (
        RagService(
            embedder,
            chat,
            store,
            top_k=5,
            min_score=-1.0,
            max_question_chars=500,
            parser=QueryParser(lambda: NOW),
            structured=StructuredRetriever(get_session_factory()),
        ),
        chat,
    )
    client.close()


@pytest.mark.parametrize(
    "question",
    ["How many payments have expired?", "How much have I collected in total?", "What is the best-selling product?"],
)
def test_english_aggregates_pass_the_validator_without_calling_the_model(service, question):
    rag, chat = service
    answer = rag.ask(SEED_MERCHANT_ID, question)
    assert answer.used_llm is False and chat.calls == []
    assert answer.warnings == []
    assert detect_language(answer.answer) == "en"
    assert [s.cited for s in answer.sources] == [True]
