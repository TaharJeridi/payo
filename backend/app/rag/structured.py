"""Execute a ``QueryPlan`` with SQL and turn the result into evidence.

Lookups return the matching payments' documents, generated from the database at query
time (not from the vector index), and a short search summary (filters, number of matches)
that is passed to the model as backend data, outside the numbered sources: in the first
measured run Qwen cited that summary instead of the payment it described. Aggregates are
answered deterministically from SQL: the model never counts or sums. Metrics PayFlow does
not record (refunds, disputes, fees, payouts, net revenue) are declared unavailable instead
of being estimated.

Financial definitions used here:
- incassato (gross paid): sum of ``amount`` of payments in status ``paid``, per currency.
- refunded / net / settled: not modelled, never computed.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from app.payments.catalog import CATALOG
from app.payments.facts import derive_facts
from app.payments.filters import PaymentFilters
from app.payments.models import Payment, PaymentEvent
from app.payments.repository import PaymentRepository
from app.payments.state import PaymentStatus
from app.rag.documents import format_amount, payment_document
from app.rag.ports import Chunk, ScoredChunk
from app.rag.query_parser import FOREIGN_TENANT_NOTE, QueryPlan

Route = Literal[
    "semantic",
    "sql_find",
    "sql_list",
    "sql_partial",
    "sql_aggregate",
    "no_match",
    "unsupported",
    "attribute_unavailable",
    "unrecognized_payment_set",
    "clarify",
    "unsupported_constraint",
    "out_of_scope",
]
EVIDENCE_NAMESPACE = uuid.UUID("0c3f8a1e-6d4b-4f0a-9a52-7e1b2c3d4e5f")

STATUS_PLURAL: dict[PaymentStatus, str] = {
    PaymentStatus.PAID: "pagati",
    PaymentStatus.OPEN: "in attesa di pagamento",
    PaymentStatus.CREATION_UNKNOWN: "con esito sconosciuto",
    PaymentStatus.CREATING: "in preparazione",
    PaymentStatus.FAILED: "falliti",
    PaymentStatus.EXPIRED: "scaduti",
}
TENANT_NOTICE = "I dati sono sempre quelli del tuo account: un altro merchant citato nella domanda viene ignorato."
TENANT_NOTICES = {
    "it": TENANT_NOTICE,
    "en": "The data always comes from your own account: another merchant named in the question is ignored.",
}
SQL_SOURCE_TITLE = {
    "it": {
        "count": "Dati calcolati con SQL: conteggio",
        "total": "Dati calcolati con SQL: totale",
        "products": "Dati calcolati con SQL: prodotti",
    },
    "en": {
        "count": "Data calculated with SQL: count",
        "total": "Data calculated with SQL: total",
        "products": "Data calculated with SQL: products",
    },
}
UNSUPPORTED_ANSWER = {
    "it": (
        "PayFlow non registra {metric}: rimborsi, dispute, commissioni e liquidazioni non sono modellati, "
        "quindi non posso calcolare questo dato senza inventarlo. Posso indicarti l'incassato lordo "
        "(somma dei pagamenti con stato pagato)."
    ),
    "en": (
        "PayFlow does not record {metric}: refunds, disputes, fees and payouts are not modelled, so I cannot "
        "compute this figure without making it up. I can tell you the gross amount collected (sum of paid payments)."
    ),
}
UNSUPPORTED_METRIC_EN = {
    "rimborsi": "refunds",
    "dispute": "disputes",
    "netto": "net revenue",
    "liquidazioni": "payouts",
    "commissioni": "fees",
}


@dataclass(frozen=True, slots=True)
class StructuredResult:
    route: Route
    evidence: list[ScoredChunk] = field(default_factory=list)
    direct_answer: str | None = None
    payment_ids: tuple[uuid.UUID, ...] = ()
    total: int = 0
    preamble: str | None = None
    answer_suffix: str | None = None
    found_listing: str | None = None


def evidence_chunk(merchant_id: uuid.UUID, source_key: str, source_type: str, title: str, text: str) -> ScoredChunk:
    chunk = Chunk(
        point_id=uuid.uuid5(EVIDENCE_NAMESPACE, f"{merchant_id}|{source_key}"),
        source_key=source_key,
        source_type=source_type,
        title=title,
        text=f"{title}\n{text}",
        scope=str(merchant_id),
        position=0,
        metadata={"origin": "sql"},
    )
    return ScoredChunk(chunk=chunk, score=1.0)


def money(cents: int, currency: str) -> str:
    return format_amount(cents, currency).split(" (")[0]


_LABELS: dict[str, dict[str, str]] = {
    "it": {
        "id": "id",
        "status": "stato",
        "or": "o",
        "product": "prodotto",
        "quantity": "quantità",
        "amount": "importo",
        "min_in": "almeno",
        "min_ex": "superiore a",
        "max_in": "al massimo",
        "max_ex": "inferiore a",
        "from": "creati dal",
        "to": "prima del",
        "recovered": "esito della creazione recuperato",
        "late": "pagati dopo la scadenza della sessione",
        "none": "nessun filtro",
    },
    "en": {
        "id": "id",
        "status": "status",
        "or": "or",
        "product": "product",
        "quantity": "quantity",
        "amount": "amount",
        "min_in": "at least",
        "min_ex": "over",
        "max_in": "at most",
        "max_ex": "under",
        "from": "created from",
        "to": "before",
        "recovered": "creation outcome recovered",
        "late": "paid after the session expired",
        "none": "no filter",
    },
}
STATUS_SINGULAR: dict[PaymentStatus, str] = {
    PaymentStatus.PAID: "pagato",
    PaymentStatus.OPEN: "in attesa di pagamento",
    PaymentStatus.CREATION_UNKNOWN: "con esito sconosciuto",
    PaymentStatus.CREATING: "in preparazione",
    PaymentStatus.FAILED: "fallito",
    PaymentStatus.EXPIRED: "scaduto",
}
STATUS_EN: dict[PaymentStatus, str] = {
    PaymentStatus.PAID: "paid",
    PaymentStatus.OPEN: "awaiting payment",
    PaymentStatus.CREATION_UNKNOWN: "with unknown outcome",
    PaymentStatus.CREATING: "being created",
    PaymentStatus.FAILED: "failed",
    PaymentStatus.EXPIRED: "expired",
}
RECOVERY_SOURCE: dict[str, dict[str, str]] = {
    "it": {"reconciliation": "riconciliazione", "api": "un nuovo tentativo del client", "webhook": "webhook Stripe"},
    "en": {"reconciliation": "reconciliation", "api": "a client retry", "webhook": "a Stripe webhook"},
}
NO_MATCH = {
    "it": "Non ho trovato pagamenti con questi criteri: {criteria}.",
    "en": "I found no payments matching these criteria: {criteria}.",
}
FACTS_HEADER = {"it": "Fatti verificati dal backend:", "en": "Facts verified by the backend:"}
FACT_RECOVERED = {
    "it": "- Pagamento {id}: recuperato tramite {source} dopo che l'esito della creazione era andato perso [{n}].",
    "en": "- Payment {id}: recovered through {source} after the creation outcome had been lost [{n}].",
}
FACT_LATE = {
    "it": "- Pagamento {id}: pagato dopo la scadenza della sessione di checkout [{n}].",
    "en": "- Payment {id}: paid after the checkout session had expired [{n}].",
}
RESTRICTED_ATTRIBUTES: dict[str, dict[str, tuple[str, bool]]] = {
    "it": {
        "customer_email": ("l'email del cliente", False),
        "customer_name": ("il nome del cliente", False),
        "phone": ("il numero di telefono del cliente", False),
        "address": ("l'indirizzo del cliente", False),
        "card": ("i dati della carta", True),
        "checkout_url": ("il link di checkout", False),
        "idempotency_key": ("l'Idempotency-Key", False),
    },
    "en": {
        "customer_email": ("the customer's email", False),
        "customer_name": ("the customer's name", False),
        "phone": ("the customer's phone number", False),
        "address": ("the customer's address", False),
        "card": ("the card details", True),
        "checkout_url": ("the checkout link", False),
        "idempotency_key": ("the Idempotency-Key", False),
    },
}
FOUND_ONE = {"it": "Ho trovato il pagamento {id} {refs}", "en": "I found payment {id} {refs}"}
FOUND_MANY = {
    "it": "Ho trovato {total} {noun} con questi criteri {refs}",
    "en": "I found {total} {noun} matching these criteria {refs}",
}
UNAVAILABLE: dict[str, dict[bool, str]] = {
    "it": {
        False: "{found}, ma {attribute} non è disponibile: l'assistente non ha accesso a questo dato.",
        True: "{found}, ma {attribute} non sono disponibili: l'assistente non ha accesso a questi dati.",
    },
    "en": {
        False: "{found}, but {attribute} is not available: the assistant has no access to it.",
        True: "{found}, but {attribute} are not available: the assistant has no access to them.",
    },
}
LISTING_HEADER = {
    "it": "Ho trovato {total} {noun} con questi criteri: {criteria}.",
    "en": "I found {total} {noun} matching these criteria: {criteria}.",
}
LISTING_LINE = {
    "it": "- Pagamento {id}: {product}, {amount}, stato {status}{reason} [{n}].",
    "en": "- Payment {id}: {product}, {amount}, status {status}{reason} [{n}].",
}
LISTING_REASON = {"it": ", motivo: {reason}", "en": ", reason: {reason}"}
LISTING_LIMIT = {
    "it": "Ne mostro {shown}, i più recenti: gli altri {rest} non sono elencati qui.",
    "en": "Showing the {shown} most recent; the other {rest} are not listed here.",
}
ASKED_YES = {"it": "Sì: il pagamento {id} risulta {status} [1].", "en": "Yes: payment {id} is {status} [1]."}
ASKED_NO = {"it": "No: il pagamento {id} risulta {status} [1].", "en": "No: payment {id} is {status} [1]."}
PARTIAL_FOUND = {
    "it": (
        "Ho interpretato solo una parte della domanda: non ho interpretato {terms}. "
        "Con i criteri che ho interpretato ({criteria}) ho trovato {total} {noun}, ma non tutti potrebbero "
        "corrispondere a ciò che chiedi:"
    ),
    "en": (
        "I understood only part of the question: I did not interpret {terms}. "
        "With the criteria I did interpret ({criteria}) I found {total} {noun}, but not all of them may match "
        "what you asked:"
    ),
}
PARTIAL_EMPTY = {
    "it": (
        "Non ho trovato pagamenti con i criteri che ho interpretato ({criteria}), ma non ho interpretato {terms}: "
        "la verifica non è conclusiva. Riformula quella parte della domanda."
    ),
    "en": (
        "I found no payments with the criteria I interpreted ({criteria}), but I did not interpret {terms}: "
        "the check is not conclusive. Please rephrase that part of the question."
    ),
}
PARTIAL_AGGREGATE = {
    "it": (
        "Non calcolo questo risultato: non ho interpretato {terms}, quindi il numero potrebbe riferirsi a criteri "
        "diversi da quelli che chiedi. Criteri interpretati: {criteria}. Riformula quella parte della domanda."
    ),
    "en": (
        "I am not computing this figure: I did not interpret {terms}, so it could refer to different criteria from "
        "the ones you asked for. Criteria interpreted: {criteria}. Please rephrase that part of the question."
    ),
}
CLARIFY = {
    "it": (
        "Non calcolo né elenco nulla: {terms} può indicare criteri diversi e il risultato cambierebbe a seconda "
        "della scelta. Indica un criterio preciso, per esempio un importo («sopra i 50 €»), uno stato o un periodo."
    ),
    "en": (
        "I am not computing or listing anything: {terms} can mean different criteria, and the result would change "
        'with the choice. Please give a precise criterion, for example an amount ("over €50"), a status or a period.'
    ),
}
UNSUPPORTED_CONSTRAINT = {
    "it": (
        "PayFlow non può filtrare i pagamenti per {terms}: registra prodotto, stato, importo, quantità, data di "
        "creazione e storico degli stati, ma non questo criterio. Non calcolo un risultato che lo ignorerebbe."
    ),
    "en": (
        "PayFlow cannot filter payments by {terms}: it records product, status, amount, quantity, creation date and "
        "status history, but not this criterion. I am not computing a result that would ignore it."
    ),
}
PARTIAL_REPHRASE = {
    "it": "Riformula la parte non interpretata per una verifica completa.",
    "en": "Rephrase the part I did not interpret for a complete check.",
}


def status_label(status: PaymentStatus, count: int, language: str = "it") -> str:
    if language == "en":
        return STATUS_EN[status]
    return STATUS_SINGULAR[status] if count == 1 else STATUS_PLURAL[status]


def payments_noun(count: int, language: str = "it") -> str:
    if language == "en":
        return "payment" if count == 1 else "payments"
    return "pagamento" if count == 1 else "pagamenti"


def describe(filters: PaymentFilters, language: str = "it") -> str:
    labels = _LABELS[language]
    parts: list[str] = []
    if filters.payment_ids:
        parts.append(f"{labels['id']} " + ", ".join(str(i) for i in filters.payment_ids))
    if filters.statuses:
        parts.append(
            f"{labels['status']} " + f" {labels['or']} ".join(status_label(s, 1, language) for s in filters.statuses)
        )
    if filters.product_ids:
        names = (CATALOG[p].name if p in CATALOG else p for p in filters.product_ids)
        parts.append(f"{labels['product']} " + f" {labels['or']} ".join(names))
    if filters.quantity is not None:
        parts.append(f"{labels['quantity']} {filters.quantity}")
    if (amount := filters.amount) is not None:
        currency = filters.currency or "eur"
        if amount.is_exact and amount.minimum is not None:
            parts.append(f"{labels['amount']} {money(amount.minimum, currency)}")
        else:
            if amount.minimum is not None:
                word = labels["min_in"] if amount.minimum_inclusive else labels["min_ex"]
                parts.append(f"{labels['amount']} {word} {money(amount.minimum, currency)}")
            if amount.maximum is not None:
                word = labels["max_in"] if amount.maximum_inclusive else labels["max_ex"]
                parts.append(f"{labels['amount']} {word} {money(amount.maximum, currency)}")
    if filters.created_from is not None:
        parts.append(f"{labels['from']} {filters.created_from.date().isoformat()}")
    if filters.created_to is not None:
        parts.append(f"{labels['to']} {filters.created_to.date().isoformat()}")
    if filters.recovered:
        source = f" ({filters.recovery_source})" if filters.recovery_source else ""
        parts.append(f"{labels['recovered']}{source}")
    if filters.paid_after_expiry:
        parts.append(labels["late"])
    return ", ".join(parts) if parts else labels["none"]


class StructuredRetriever:
    def __init__(
        self,
        session_factory: sessionmaker[Session] | Callable[[], Session],
        *,
        lookups: bool = True,
        aggregates: bool = True,
        find_limit: int = 8,
        enriched_documents: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._lookups = lookups
        self._aggregates = aggregates
        self._find_limit = find_limit
        self._enriched = enriched_documents

    def execute(self, merchant_id: uuid.UUID, plan: QueryPlan) -> StructuredResult | None:
        result = self._execute(merchant_id, plan)
        if result is None or FOREIGN_TENANT_NOTE not in plan.notes or result.direct_answer is None:
            return result
        return StructuredResult(
            route=result.route,
            evidence=result.evidence,
            direct_answer=f"{result.direct_answer} {TENANT_NOTICES[plan.language]}",
            payment_ids=result.payment_ids,
            total=result.total,
            preamble=result.preamble,
            answer_suffix=result.answer_suffix,
            found_listing=result.found_listing,
        )

    def _execute(self, merchant_id: uuid.UUID, plan: QueryPlan) -> StructuredResult | None:
        executable = (plan.intent == "find" and self._lookups) or (plan.is_aggregate and self._aggregates)
        if executable and plan.completeness in ("ambiguous", "unsupported") and plan.intent != "unsupported_metric":
            return self._not_executed(plan)
        if plan.intent == "find" and self._lookups:
            return self._find(merchant_id, plan)
        if plan.intent == "unsupported_metric" and self._aggregates:
            metric = plan.unsupported_metric or ""
            if plan.language == "en":
                metric = UNSUPPORTED_METRIC_EN.get(metric, metric)
            return StructuredResult(
                route="unsupported", direct_answer=UNSUPPORTED_ANSWER[plan.language].format(metric=metric)
            )
        if plan.is_aggregate and self._aggregates:
            if plan.unresolved:
                return StructuredResult(
                    route="sql_partial",
                    direct_answer=PARTIAL_AGGREGATE[plan.language].format(
                        terms=quoted(plan.unresolved), criteria=describe(plan.filters, plan.language)
                    ),
                )
            with self._session_factory() as session:
                repo = PaymentRepository(session)
                if plan.intent == "count":
                    return self._count(merchant_id, repo, plan.filters, plan.language)
                if plan.intent == "total":
                    return self._total(merchant_id, repo, plan.filters, plan.language)
                return self._top_product(merchant_id, repo, plan.filters, ranking=plan.ranking, language=plan.language)
        return None

    @staticmethod
    def _not_executed(plan: QueryPlan) -> StructuredResult:
        """Ambiguous or unsupported constraints: no SQL runs, so no figure, list or absence can be stated."""
        if plan.unsupported_constraints:
            return StructuredResult(
                route="unsupported_constraint",
                direct_answer=UNSUPPORTED_CONSTRAINT[plan.language].format(terms=quoted(plan.unsupported_constraints)),
            )
        return StructuredResult(
            route="clarify", direct_answer=CLARIFY[plan.language].format(terms=quoted(plan.ambiguous))
        )

    def _find(self, merchant_id: uuid.UUID, plan: QueryPlan) -> StructuredResult:
        with self._session_factory() as session:
            repo = PaymentRepository(session)
            payments, total = repo.find(merchant_id, plan.filters, limit=self._find_limit)
            histories = repo.histories([p.id for p in payments])
            documents = [payment_document(p, histories[p.id], enriched=self._enriched) for p in payments]
        language = plan.language
        if not documents:
            if plan.unresolved:
                return StructuredResult(
                    route="sql_partial",
                    direct_answer=PARTIAL_EMPTY[language].format(
                        terms=quoted(plan.unresolved), criteria=describe(plan.filters, language)
                    ),
                )
            return StructuredResult(
                route="no_match",
                direct_answer=NO_MATCH[language].format(criteria=describe(plan.filters, language)),
            )
        lines = [
            LISTING_LINE[language].format(
                id=p.id,
                product=CATALOG[p.product_id].name if p.product_id in CATALOG else p.product_id,
                amount=money(p.amount, p.currency),
                status=status_label(p.status, 1, language),
                reason=LISTING_REASON[language].format(reason=p.failure_reason) if p.failure_reason else "",
                n=n,
            )
            for n, p in enumerate(payments, start=1)
        ]
        if total > len(payments):
            lines.append(LISTING_LIMIT[language].format(shown=len(payments), rest=total - len(payments)))
        header = LISTING_HEADER[language].format(
            total=total, noun=payments_noun(total, language), criteria=describe(plan.filters, language)
        )
        listing = [header, *lines]
        evidence = [
            evidence_chunk(merchant_id, d.source_key, "payment", d.title, d.text.split("\n", 1)[1]) for d in documents
        ]
        facts = self._matching_facts(plan, payments, histories, language)
        if plan.requested_attribute is not None:
            return StructuredResult(
                route="attribute_unavailable",
                evidence=evidence,
                direct_answer=self._unavailable(plan.requested_attribute, payments, total, language),
                payment_ids=tuple(p.id for p in payments),
                total=total,
            )
        if plan.unresolved:
            partial = PARTIAL_FOUND[language].format(
                terms=quoted(plan.unresolved),
                criteria=describe(plan.filters, language),
                total=total,
                noun=payments_noun(total, language),
            )
            return StructuredResult(
                route="sql_partial",
                evidence=evidence,
                direct_answer="\n".join([partial, *lines, PARTIAL_REPHRASE[language]]),
                payment_ids=tuple(p.id for p in payments),
                total=total,
            )
        if plan.listing:
            parts = [self._asked(plan, payments, language)] if plan.asked_statuses and total == 1 else []
            parts.append("\n".join(listing))
            if facts:
                parts.append(facts)
            return StructuredResult(
                route="sql_list",
                evidence=evidence,
                direct_answer="\n\n".join(parts),
                payment_ids=tuple(p.id for p in payments),
                total=total,
            )
        notes = [
            f"Filtri applicati: {describe(plan.filters)}.",
            f"Pagamenti che corrispondono: {total}"
            + (f" (qui i {len(documents)} più recenti)." if total > len(documents) else "."),
        ]
        if FOREIGN_TENANT_NOTE in plan.notes:
            notes.append(TENANT_NOTICE)
        if plan.asked_statuses:
            asked = " o ".join(STATUS_SINGULAR[s] for s in plan.asked_statuses)
            notes.append(f"La domanda chiede se il pagamento risulta {asked}: confrontalo con lo stato indicato.")
        return StructuredResult(
            route="sql_find",
            evidence=evidence,
            payment_ids=tuple(p.id for p in payments),
            total=total,
            preamble="\n".join(notes),
            answer_suffix=facts,
            found_listing="\n".join(listing),
        )

    @staticmethod
    def _asked(plan: QueryPlan, payments: Sequence[Payment], language: str) -> str:
        payment = payments[0]
        template = ASKED_YES if payment.status in plan.asked_statuses else ASKED_NO
        return template[language].format(id=payment.id, status=status_label(payment.status, 1, language))

    @staticmethod
    def _unavailable(attribute: str, payments: Sequence[Payment], total: int, language: str) -> str:
        label, plural = RESTRICTED_ATTRIBUTES[language][attribute]
        refs = "".join(f"[{n}]" for n in range(1, len(payments) + 1))
        if total == 1:
            found = FOUND_ONE[language].format(id=payments[0].id, refs=refs)
        else:
            found = FOUND_MANY[language].format(total=total, noun=payments_noun(total, language), refs=refs)
        return UNAVAILABLE[language][plural].format(found=found, attribute=label)

    @staticmethod
    def _matching_facts(
        plan: QueryPlan,
        payments: Sequence[Payment],
        histories: dict[uuid.UUID, list[PaymentEvent]],
        language: str,
    ) -> str | None:
        """Verified facts that explain why each payment matched a derived-fact filter."""
        lines: list[str] = []
        for n, payment in enumerate(payments, start=1):
            facts = derive_facts(histories[payment.id])
            if plan.filters.recovered and facts.recovered and facts.recovery_source:
                source = RECOVERY_SOURCE[language].get(facts.recovery_source, facts.recovery_source)
                lines.append(FACT_RECOVERED[language].format(id=payment.id, source=source, n=n))
            if plan.filters.paid_after_expiry and facts.paid_after_expiry:
                lines.append(FACT_LATE[language].format(id=payment.id, n=n))
        return "\n".join([FACTS_HEADER[language], *lines]) if lines else None

    def _count(
        self, merchant_id: uuid.UUID, repo: PaymentRepository, filters: PaymentFilters, language: str = "it"
    ) -> StructuredResult:
        counts = repo.count_by_status(merchant_id, filters)
        total = sum(counts.values())
        nonzero = [(s, n) for s, n in counts.items() if n]
        breakdown = ", ".join(f"{n} {status_label(s, n, language)}" for s, n in nonzero)
        noun = payments_noun(total, language)
        extra = _extra_filters(filters, language)
        if language == "en":
            text = f"Count of payments with filters: {describe(filters, language)}.\nTotal: {total}." + (
                f"\nBy status: {breakdown}." if breakdown else ""
            )
            if len(filters.statuses) == 1:
                answer = f"You have {total} {noun} with status {status_label(filters.statuses[0], total, language)}"
            else:
                answer = f"You have {total} {noun}" + (f" ({breakdown})" if breakdown and len(nonzero) > 1 else "")
            answer += (f", filtered by {', '.join(extra)}" if extra else "") + " [1]."
        else:
            text = f"Conteggio dei pagamenti con filtri: {describe(filters)}.\nTotale: {total}." + (
                f"\nPer stato: {breakdown}." if breakdown else ""
            )
            if len(filters.statuses) == 1:
                answer = f"Hai {total} {noun} {status_label(filters.statuses[0], total)}"
            else:
                answer = f"Hai {total} {noun}" + (f" ({breakdown})" if breakdown and len(nonzero) > 1 else "")
            answer += (f" con {', '.join(extra)}" if extra else "") + " [1]."
        return StructuredResult(
            route="sql_aggregate",
            evidence=[evidence_chunk(merchant_id, "sql:count", "sql", SQL_SOURCE_TITLE[language]["count"], text)],
            direct_answer=answer,
            total=total,
        )

    def _total(
        self, merchant_id: uuid.UUID, repo: PaymentRepository, filters: PaymentFilters, language: str = "it"
    ) -> StructuredResult:
        gross = not filters.statuses or filters.statuses == (PaymentStatus.PAID,)
        if not filters.statuses:
            filters = filters.model_copy(update={"statuses": (PaymentStatus.PAID,)})
        totals = repo.amount_totals(merchant_id, filters)
        count = sum(n for n, _ in totals.values())
        amounts = " + ".join(money(total, currency) for currency, (_, total) in totals.items()) or money(0, "eur")
        noun = payments_noun(count, language)
        extra = _extra_filters(filters, language)
        if language == "en":
            definition = (
                "Gross collected = sum of the amounts of payments with status paid; refunds and fees are not "
                "recorded, so this is not a net figure."
                if gross
                else "Sum of the amounts of the filtered payments: this is not revenue."
            )
            text = f"{definition}\nFilters: {describe(filters, language)}.\nPayments: {count}.\nTotal: {amounts}."
            if gross:
                answer = f"You collected {amounts} gross, from {count} {noun} with status paid"
            else:
                answer = f"The total value of the filtered payments is {amounts} ({count} {noun}; this is not revenue)"
            answer += (f", filtered by {', '.join(extra)}" if extra else "") + " [1]."
        else:
            definition = (
                "Incassato lordo = somma degli importi dei pagamenti con stato pagato; rimborsi e commissioni non sono "
                "registrati, quindi non è un netto."
                if gross
                else "Somma degli importi dei pagamenti filtrati: non è un incasso."
            )
            text = f"{definition}\nFiltri: {describe(filters)}.\nPagamenti: {count}.\nTotale: {amounts}."
            if gross:
                answer = f"Hai incassato {amounts} lordi, da {count} {noun} con stato pagato"
            else:
                answer = f"Il valore totale dei pagamenti filtrati è {amounts} ({count} {noun}; non è un incasso)"
            answer += (f", con {', '.join(extra)}" if extra else "") + " [1]."
        return StructuredResult(
            route="sql_aggregate",
            evidence=[evidence_chunk(merchant_id, "sql:total", "sql", SQL_SOURCE_TITLE[language]["total"], text)],
            direct_answer=answer,
            total=count,
        )

    def _top_product(
        self,
        merchant_id: uuid.UUID,
        repo: PaymentRepository,
        filters: PaymentFilters,
        *,
        ranking: bool = False,
        language: str = "it",
    ) -> StructuredResult:
        if not filters.statuses:
            filters = filters.model_copy(update={"statuses": (PaymentStatus.PAID,)})
        rows = repo.product_totals(merchant_id, filters)
        en = language == "en"
        if not rows:
            template = (
                "I found no sales matching these criteria: {criteria}."
                if en
                else "Non ho trovato vendite con questi criteri: {criteria}."
            )
            return StructuredResult(
                route="no_match", direct_answer=template.format(criteria=describe(filters, language))
            )

        def units_word(n: int) -> str:
            return ("unit" if n == 1 else "units") if en else "unità"

        lines = [
            f"- {self._name(p)}: {units} {units_word(units)} in {n} {payments_noun(n, language)}, "
            f"{money(total, currency)}"
            for p, currency, n, units, total in rows
        ]
        header = (
            f"Products of payments with status paid, sorted by units sold (filters: {describe(filters, language)}):\n"
            if en
            else f"Prodotti dei pagamenti con stato pagato, ordinati per unità vendute (filtri: {describe(filters)}):\n"
        )
        text = header + "\n".join(lines)
        by_units = rows[0]
        by_revenue = max(rows, key=lambda r: (r[4], r[0]))
        if en:
            answer = (
                f"The best-selling product is {self._name(by_units[0])}: "
                f"{by_units[3]} {units_word(by_units[3])} in {by_units[2]} {payments_noun(by_units[2], language)}"
            )
            if by_revenue[0] != by_units[0]:
                answer += (
                    f". By revenue, {self._name(by_revenue[0])} comes first with {money(by_revenue[4], by_revenue[1])}"
                )
        else:
            answer = (
                f"Il prodotto più venduto è {self._name(by_units[0])}: "
                f"{by_units[3]} unità in {by_units[2]} {payments_noun(by_units[2])}"
            )
            if by_revenue[0] != by_units[0]:
                answer += (
                    f". Per incasso invece è primo {self._name(by_revenue[0])} con "
                    f"{money(by_revenue[4], by_revenue[1])}"
                )
        answer += " [1]."
        if ranking and len(rows) > 1:
            title = (
                "Ranking by units sold (payments with status paid):"
                if en
                else ("Classifica per unità vendute (pagamenti con stato pagato):")
            )
            answer += f"\n{title}\n" + "\n".join(
                f"{n}. {self._name(p)}: {units} {units_word(units)}, {money(total, currency)}"
                for n, (p, currency, _, units, total) in enumerate(rows, start=1)
            )
        return StructuredResult(
            route="sql_aggregate",
            evidence=[evidence_chunk(merchant_id, "sql:products", "sql", SQL_SOURCE_TITLE[language]["products"], text)],
            direct_answer=answer,
            total=len(rows),
        )

    @staticmethod
    def _name(product_id: str) -> str:
        return CATALOG[product_id].name if product_id in CATALOG else product_id


def _extra_filters(filters: PaymentFilters, language: str) -> list[str]:
    """Filters other than the status, as described to the merchant; empty when there are none."""
    described = describe(filters.model_copy(update={"statuses": ()}), language)
    return [d for d in described.split(", ") if d != _LABELS[language]["none"]]


def quoted(terms: Sequence[str]) -> str:
    return ", ".join(f"«{t}»" for t in terms)


def payment_ids_in(chunks: Sequence[ScoredChunk]) -> set[str]:
    return {c.chunk.source_key.removeprefix("payment:") for c in chunks if c.chunk.source_key.startswith("payment:")}
