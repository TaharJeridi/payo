"""Turn PayFlow data into text documents for retrieval.

Only operational facts are indexed. Customer email, checkout URL and the client's
Idempotency-Key are deliberately left out: they are personal or bearer data and are never
needed to explain what happened to a payment.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.payments.catalog import CATALOG
from app.payments.facts import PaymentFacts, derive_facts
from app.payments.models import Payment, PaymentEvent
from app.payments.state import PaymentStatus
from app.rag.ports import PUBLIC_SCOPE

STATUS_MEANING: Mapping[PaymentStatus, str] = {
    PaymentStatus.CREATING: "checkout in preparazione: la richiesta a Stripe è in corso",
    PaymentStatus.OPEN: "checkout creato su Stripe, in attesa che l'acquirente paghi",
    PaymentStatus.CREATION_UNKNOWN: (
        "la risposta di Stripe alla creazione del checkout è andata persa; l'esito è sconosciuto "
        "e la riconciliazione lo stabilirà dai dati di Stripe"
    ),
    PaymentStatus.PAID: "pagato, confermato con dati verificati su Stripe",
    PaymentStatus.FAILED: "fallito",
    PaymentStatus.EXPIRED: "la sessione di Checkout Stripe è scaduta senza pagamento",
}

SOURCE_LABEL: Mapping[str, str] = {
    "api": "API",
    "webhook": "webhook Stripe",
    "reconciliation": "riconciliazione",
    "migration": "migrazione",
}


@dataclass(frozen=True, slots=True)
class SourceDocument:
    source_key: str
    source_type: str
    scope: str
    title: str
    text: str
    is_markdown: bool = False
    version: datetime | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def content_hash(self, embed_model: str) -> str:
        digest = hashlib.sha256()
        for part in (embed_model, self.scope, self.title, self.text):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()


def _fmt_time(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else "-"


def format_amount(amount: int, currency: str) -> str:
    whole, cents = divmod(amount, 100)
    italian = f"{whole:,}".replace(",", ".") + f",{cents:02d}"
    symbol = "€" if currency.lower() == "eur" else currency.upper()
    return f"{italian} {symbol} ({whole}.{cents:02d} {currency.upper()})"


def payment_source_key(payment_id: object) -> str:
    return f"payment:{payment_id}"


def _amount(amount: int, currency: str, enriched: bool) -> str:
    return format_amount(amount, currency) if enriched else f"{amount / 100:.2f} {currency.upper()}"


def _yes_no(value: bool) -> str:
    return "sì" if value else "no"


def format_duration(seconds: int) -> str:
    """'3 min 1 s (181 s)': an exact elapsed time, so the model never has to subtract timestamps (ADR 0014)."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{hours} h"] if hours else []
    if minutes or hours:
        parts.append(f"{minutes} min")
    parts.append(f"{secs} s")
    label = " ".join(parts)
    return label if seconds < 60 else f"{label} ({seconds} s)"


def _fact_lines(facts: PaymentFacts, payment: Payment) -> list[str]:
    recovered = (
        f"sì, da {SOURCE_LABEL.get(facts.recovery_source, facts.recovery_source)}"
        if facts.recovered and facts.recovery_source
        else "no"
    )
    confirmed = SOURCE_LABEL.get(facts.confirmed_by, facts.confirmed_by) if facts.confirmed_by else "-"
    lines = [
        "Fatti verificati (calcolati dallo storico degli eventi):",
        f"- esito della creazione perso e poi recuperato: {recovered}",
        f"- pagato dopo la scadenza della sessione di checkout: {_yes_no(facts.paid_after_expiry)}",
        f"- pagamento confermato da: {confirmed}",
    ]
    if payment.paid_at is not None and payment.created_at is not None:
        elapsed = int((payment.paid_at - payment.created_at).total_seconds())
        lines.append(f"- tempo dalla creazione al pagamento: {format_duration(elapsed)}")
    return lines


def payment_document(payment: Payment, history: Sequence[PaymentEvent], *, enriched: bool = True) -> SourceDocument:
    product = CATALOG.get(payment.product_id)
    product_name = product.name if product else payment.product_id
    lines = [
        f"Pagamento {payment.id}",
        f"Prodotto: {product_name} ({payment.product_id}), quantità {payment.quantity}",
        f"Importo: {_amount(payment.amount, payment.currency, enriched)} "
        f"(prezzo unitario {_amount(payment.unit_amount, payment.currency, enriched)})",
        f"Stato: {payment.status.value} - {STATUS_MEANING[payment.status]}",
        f"Motivo del fallimento: {payment.failure_reason or '-'}",
        f"Creato il: {_fmt_time(payment.created_at)}",
        f"Pagato il: {_fmt_time(payment.paid_at)}",
        f"Scadenza della sessione di checkout: {_fmt_time(payment.checkout_expires_at)}",
        f"Stripe Checkout Session: {payment.stripe_checkout_session_id or '-'}",
        f"Stripe PaymentIntent: {payment.stripe_payment_intent_id or '-'}",
        f"Tentativi di creazione: {payment.creation_attempts}",
        *(_fact_lines(derive_facts(history), payment) if enriched else []),
        f"Tentativi di riconciliazione: {payment.reconcile_attempts}, "
        f"ultimo errore: {payment.last_reconcile_error or '-'}",
        "Storico:",
    ]
    for event in history:
        origin = event.from_status.value if event.from_status else "(nuovo)"
        reason = f", motivo: {event.reason}" if event.reason else ""
        source = SOURCE_LABEL.get(event.source, event.source)
        lines.append(f"- {_fmt_time(event.created_at)} {origin} -> {event.to_status.value} da {source}{reason}")
    return SourceDocument(
        source_key=payment_source_key(payment.id),
        source_type="payment",
        scope=str(payment.merchant_id),
        title=f"Pagamento {payment.id} ({payment.status.value})",
        text="\n".join(lines),
        version=payment.updated_at,
        metadata={"payment_id": str(payment.id), "status": payment.status.value},
    )


def markdown_documents(directory: Path) -> list[SourceDocument]:
    if not directory.is_dir():
        return []
    documents = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if text.strip():
            documents.append(
                SourceDocument(
                    source_key=f"doc:{path.name}",
                    source_type="doc",
                    scope=PUBLIC_SCOPE,
                    title=path.stem.replace("_", " ").replace("-", " ").title(),
                    text=text,
                    is_markdown=True,
                    metadata={"file": path.name},
                )
            )
    return documents
