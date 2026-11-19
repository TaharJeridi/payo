"""TAH-143 follow-up: the reconciliation section of docs/architecture.md must not blur a state with a
reason, nor detach a condition from the case it applies to. These chunks are what Qwen reads for
questions such as "How does reconciliation with Stripe work?".
"""

from pathlib import Path

from app.core.config import Settings
from app.payments.state import PaymentStatus
from app.rag.chunking import split_markdown

DOC = Path(__file__).resolve().parents[2] / "docs" / "architecture.md"
SECTION = "5. Reconciliation"


def reconciliation_chunks() -> list[str]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    chunks = split_markdown(
        DOC.read_text(encoding="utf-8"),
        max_chars=settings.rag_chunk_chars,
        overlap_chars=settings.rag_chunk_overlap_chars,
    )
    texts = [c.text for c in chunks if c.heading.endswith(SECTION)]
    assert texts, "the reconciliation section is no longer indexed under its heading"
    return texts


def test_checkout_session_not_created_is_a_failure_reason_not_a_state():
    assert "checkout_session_not_created" not in {s.value for s in PaymentStatus}
    complete = [t for t in reconciliation_chunks() if "failure reason `checkout_session_not_created`" in t]
    assert complete, "no chunk states that checkout_session_not_created is the failure reason"
    for text in complete:
        assert "State `failed`" in text


def test_the_missing_session_case_keeps_its_time_condition():
    rows = [t for t in reconciliation_chunks() if "| No session found" in t]
    assert rows and all("`expires_at` (plus clock skew) has passed" in t for t in rows)


def test_expiry_storage_is_described_for_every_verified_session_and_the_exception_is_scoped():
    text = "\n".join(reconciliation_chunks())
    assert "the verified session's `expires_at` replaces the stored one" in text
    assert "Only for these rows the expiry is missing locally" in text
    assert "| Stripe unreachable | Nothing changes; retried with exponential backoff |" in text
