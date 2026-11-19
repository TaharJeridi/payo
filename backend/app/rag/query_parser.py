"""Deterministic interpretation of a merchant's question into a typed query plan.

Exact values (amounts, quantities, ids, dates, products, statuses) are recognised with
rules, never by the LLM, and validated into ``PaymentFilters``. The plan never carries a
merchant: the tenant is added by the server when the plan is executed. Questions the rules
do not recognise keep the semantic route.
"""

import re
import uuid
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches
from typing import Literal

from pydantic import ValidationError

from app.payments.catalog import CATALOG
from app.payments.filters import AmountRange, PaymentFilters
from app.payments.state import PaymentStatus
from app.rag.language import reply_language

Intent = Literal["semantic", "find", "count", "total", "top_product", "unsupported_metric"]
Completeness = Literal["complete", "incomplete", "ambiguous", "unsupported"]

_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_TENANT_WORD = re.compile(r"\b(?:merchant|tenant|account|negozio|esercente)")
FOREIGN_TENANT_NOTE = "tenant reference ignored"
_OTHER_TENANTS = re.compile(
    r"\b(?:tutti i|tutte le|degli altri|dagli altri|altri|altro|other|every|all|another|any)\s+"
    r"(?:merchant|tenant|negozi|esercenti|accounts?)"
)
_NUMBER = r"\d[\d.,]*\d|\d"
_CURRENCY = r"€|\$|\beur\b|\beuro\b|\busd\b|\bdollari\b|\bdollars?\b"
_AMOUNT = re.compile(rf"(?:(?P<pre>{_CURRENCY})\s*(?P<a>{_NUMBER}))|(?:(?P<b>{_NUMBER})\s*(?P<post>{_CURRENCY}))")
_RANGE = re.compile(
    rf"\b(?:tra|fra|between|da|from)\s+(?:(?P<c1>{_CURRENCY})\s*)?(?P<low>{_NUMBER})\s*(?:(?P<c2>{_CURRENCY})\s*)?"
    rf"(?:e|a|and|to)\s+(?:(?P<c3>{_CURRENCY})\s*)?(?P<high>{_NUMBER})\s*(?P<c4>{_CURRENCY})?"
)
_ARTICLE = r"(?:\s+(?:i|il|gli|l'|the))?\s*$"
_COMPARATORS: tuple[tuple[str, str, bool], ...] = (
    (
        r"(?:non (?:superano|supera|superior[ei] a|è superiore a|più di|piu di|oltre|sopra)"
        r"|not (?:more than|over|above|exceeding)|no more than)" + _ARTICLE,
        "max",
        True,
    ),
    (r"(?:non (?:inferior[ei] a|meno di|sotto)|not (?:less than|under|below)|no less than)" + _ARTICLE, "min", True),
    (r"(?:almeno|minimo|at least|>=)" + _ARTICLE, "min", True),
    (
        r"(?:superano|supera|superior[ei] a|più di|piu di|oltre|maggior[ei] di|sopra(?: i| a)?"
        r"|above|over|more than|greater than|>)" + _ARTICLE,
        "min",
        False,
    ),
    (r"(?:al massimo|massimo|fino a|at most|<=)" + _ARTICLE, "max", True),
    (r"(?:meno di|inferior[ei] a|minor[ei] di|sotto(?: i| a)?|below|under|less than|<)" + _ARTICLE, "max", False),
)
_NUMBER_WORDS = {
    "due": 2,
    "tre": 3,
    "quattro": 4,
    "cinque": 5,
    "sei": 6,
    "sette": 7,
    "otto": 8,
    "nove": 9,
    "dieci": 10,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_PRODUCT_ALIASES: dict[str, tuple[str, ...]] = {
    "payflow-pro": ("payflow pro", "piano pro"),
    "payflow-starter": ("payflow starter", "piano starter", "starter"),
    "payflow-report": ("financial report pack", "financial report", "report pack"),
}
_FAILED_PHRASES = (
    r"\bandat[oaie] male\b|\bnon (?:sono |è |e' )?(?:andat[oaie] a[ _]buon[ _]fine|riuscit[oaie])\b"
    r"|\bunsuccessful\b|\bnot successful\b|\b(?:went|gone) wrong\b"
)
_BARE_PRODUCT = {"payflow-pro": re.compile(r"\b(?:col|con il|con|del|il|piano|plan|the) pro\b")}
_STATUS_PATTERNS: tuple[tuple[PaymentStatus, str], ...] = (
    (
        PaymentStatus.OPEN,
        r"\bin attesa\b|\bda pagare\b|\bnon (?:ancora )?pagat[oaie]\b|\baperti\b|\bopen\b|\bpending\b|\bunpaid\b"
        r"|\bawaiting(?: payment)?\b",
    ),
    (
        PaymentStatus.CREATION_UNKNOWN,
        r"\besito (?:sconosciuto|incerto|ignoto|non noto)\b|\bnon si conosce l'esito\b|\bsconosciut[oaie]\b"
        r"|\bcreation_unknown\b|\bunknown\b",
    ),
    (PaymentStatus.CREATING, r"\bin preparazione\b|\bcreating\b"),
    (PaymentStatus.EXPIRED, r"\bscadut[oaie]\b|\bexpired\b"),
    (PaymentStatus.FAILED, r"\bfallit[oaie]\b|\brifiutat[oaie]\b|\bfailed\b|\bdeclined\b|" + _FAILED_PHRASES),
    (
        PaymentStatus.PAID,
        r"\bpagat[oaie]\b|\bcompletat[oaie]\b|\bandat[oi] a buon fine\b|\briuscit[oaie]\b"
        r"|\bpaid\b|\bcompleted\b|\bsucceeded\b|\b(?:go|goes|went|gone) through\b",
    ),
)
_PAID_AFTER_EXPIRY = re.compile(
    r"dopo (?:la (?:sua )?|che la )?(?:scadenza|sessione (?:era|fosse|è) (?:già )?scadut)"
    r"|(?:anche se|nonostante|quando) (?:la sessione )?(?:era|fosse|è) (?:già )?scadut"
    r"|after (?:the )?(?:session )?(?:had )?expir|pagat[oi] in ritardo"
    r"|dopo che (?:il checkout|la sessione(?: di checkout)?|il link(?: di pagamento)?) (?:era|fosse|è) (?:già )?scadut"
    r"|after (?:the |their |its )?(?:checkout )?(?:session|link) (?:had )?expired"
)
_RECOVERED = re.compile(
    r"\brecuperat[oaie]\b|\brecovered\b|\bsalvat[oaie] (?:dalla|con la|grazie alla|tramite la) riconciliazion"
    r"|\bsaved by (?:the )?reconcil|\brescued\b"
)
_RECONCILIATION = re.compile(r"\briconciliazion|\breconcil")
_UNSUPPORTED = (
    ("rimborsi", re.compile(r"\brimbors|\brefund")),
    ("dispute", re.compile(r"\bdisput|\bchargeback|\bcontestazion")),
    ("netto", re.compile(r"\bnett[oi]\b|\bnet revenue\b")),
    ("liquidazioni", re.compile(r"\bliquidat|\bliquidazion|\bpayout|\bversament|\bsettle")),
    ("commissioni", re.compile(r"\bcommission|\bfees?\b")),
)
_RESTRICTED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("customer_email", re.compile(r"\be-?mail\b|\bindirizzo (?:di posta|e-?mail)\b")),
    (
        "card",
        re.compile(
            r"\bcarta di credito\b|\bnumero (?:della|di) carta\b|\bdati della carta\b|\bcard\b|\bcvc\b|\biban\b"
        ),
    ),
    ("phone", re.compile(r"\btelefono\b|\bcellulare\b|\bphone\b")),
    ("customer_name", re.compile(r"\bnome del cliente\b|\bcustomer(?:'s)? name\b|\bchi ha pagato\b|\bwho paid\b")),
    ("address", re.compile(r"\bindirizzo\b|\baddress\b")),
    ("checkout_url", re.compile(r"\blink (?:di|del) checkout\b|\bcheckout (?:url|link)\b|\burl di pagamento\b")),
    ("idempotency_key", re.compile(r"\bidempotency[- ]key\b|\bchiave di idempotenza\b")),
)
_COUNT = re.compile(r"\bquant[ie]\b|\bnumero (?:di|dei|delle)\b|\bhow many\b|\bcount\b")
_TOTAL = re.compile(
    r"\bincass|\bfatturat|\bguadagnat|\bricavi\b|\btotale\b|\bin totale\b|\bsomma\b"
    r"|\bhow much\b.*\b(?:earn|made|receiv|collect)|\brevenue\b|\btotal\b"
)
_TOP_PRODUCT = re.compile(
    r"\bpiù vendut|\bpiù acquistat|\bpiù popolar|\bpiù unità vendut|\bbest[- ]?sell|\bmost (?:sold|popular)\b"
    r"|\bmost units\b|\btop product"
    # A ranking of products (TAH-143, v3 q090): the backend's SQL ranking, never one computed by the model.
    r"|\bclassifica\b[^?.]{0,30}\bprodott\w*|\branking\b[^?.]{0,30}\bproducts?\b|\brank\w*\b[^?.]{0,20}\bproducts?\b"
    r"|\bproducts?\b[^?.]{0,10}\branked\b|\b(?:per|by) (?:unità|units) (?:vendut\w*|sold)\b"
)
_RANKING_REQUEST = re.compile(r"\bclassifica\b|\branking\b|\brank\w*\b|\branked\b")
_LISTING = re.compile(
    r"\b(?:quali|quale|elenca|elencami|elenco|mostra|mostrami|fammi vedere|trova|trovami|cerca|lista|c'è|ci sono"
    r"|esiste|esistono"
    r"|which|list|show|find|is there|are there)\b"
)
_PAYMENT_WORD = re.compile(r"\bpagament|\bpayment|\btransazion|\bacquist|\bordin")
_SALES = re.compile(r"\b(?:vendit[ae]|incassi|sales)\b")
_LIST_VERB = re.compile(r"\b(?:elenca\w*|elenco|lista|mostra\w*|fammi vedere|list|show)\b")
_MONEY = re.compile(
    r"\b(?:quant[oi] (?:ho|abbiamo|hai) (?:\w+ ){0,2}(?:incass|guadagn|fatturat|fatto|tirato|portato|ricevut)"
    r"|quanti soldi|quanto (?:mi )?ha (?:portato|fruttato|reso)|how much (?:\w+ ){0,4}(?:earn|made|make|collect"
    r"|receiv|brought|bring|revenue|money)|totale (?:incassat|dei pagament|delle vendit)|total (?:revenue|sales))"
)
_MONTHS = {
    name: i + 1
    for i, names in enumerate(
        (
            ("gennaio", "january"),
            ("febbraio", "february"),
            ("marzo", "march"),
            ("aprile", "april"),
            ("maggio", "may"),
            ("giugno", "june"),
            ("luglio", "july"),
            ("agosto", "august"),
            ("settembre", "september"),
            ("ottobre", "october"),
            ("novembre", "november"),
            ("dicembre", "december"),
        )
    )
    for name in names
}
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_MONTH = re.compile(rf"\b(?P<day>\d{{1,2}}) (?P<month>{_MONTH_NAMES})(?: (?P<year>\d{{4}}))?\b")
_MONTH = re.compile(rf"\b(?:a |in |di |nel mese di |during )?(?P<month>{_MONTH_NAMES})(?: (?P<year>\d{{4}}))?\b")
_ISO_RANGE = re.compile(
    r"\b(?:dal|from|tra il|between)\s+(\d{4}-\d{2}-\d{2})\s+(?:al|to|e il|and)\s+(\d{4}-\d{2}-\d{2})\b"
)
_PLURAL_PAYMENTS = re.compile(
    r"\b(?:pagamenti|transazioni|checkout|vendite|incassi|payments|transactions|checkouts|sales)\b"
)
_QUANTITY = re.compile(r"\b(?:quantità|quantita|quantity)\s*(?:di|pari a|of|=|:)?\s*(?P<n>\d{1,2})\b")
_YEAR = r"(?:\s+(?P<year>\d{4}))?"
_DAY_RANGE = re.compile(
    rf"\b(?:tra il|fra il|tra|fra|dal|between|from)\s+(?P<d1>\d{{1,2}})\s*(?:e il|e|al|a|and|to|-|–)\s*"
    rf"(?P<d2>\d{{1,2}})\s+(?P<month>{_MONTH_NAMES}){_YEAR}\b"
)
_MONTH_PART = re.compile(
    r"\b(?P<part>prima metà|prima meta|seconda metà|seconda meta|prima quindicina|seconda quindicina"
    r"|prima settimana|ultima settimana|first half|second half|first week|last week)\s+"
    rf"(?:di |del mese di |of )?(?P<month>{_MONTH_NAMES}){_YEAR}\b"
)
_DAY_OPERATOR = re.compile(
    r"\b(?P<op>dopo il|dopo l'|prima del|prima dell'|a partire dal|dal|dall'|fino al|fino all'|entro il|entro l'"
    rf"|after|before|since|until|by)\s*(?P<day>\d{{1,2}})\s+(?P<month>{_MONTH_NAMES}){_YEAR}\b"
)
_ISO_DAY = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b|\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


@dataclass(frozen=True, slots=True)
class QueryPlan:
    intent: Intent
    filters: PaymentFilters = field(default_factory=PaymentFilters)
    asked_statuses: tuple[PaymentStatus, ...] = ()
    unsupported_metric: str | None = None
    notes: tuple[str, ...] = ()
    language: Literal["it", "en"] = "it"
    requested_attribute: str | None = None
    unresolved: tuple[str, ...] = ()
    listing: bool = False
    payment_set_question: bool = False
    ambiguous: tuple[str, ...] = ()
    unsupported_constraints: tuple[str, ...] = ()
    constraints: tuple[tuple[str, str], ...] = ()
    ranking: bool = False

    @property
    def completeness(self) -> Completeness:
        if self.intent == "unsupported_metric" or self.unsupported_constraints:
            return "unsupported"
        if self.ambiguous:
            return "ambiguous"
        if self.unresolved:
            return "incomplete"
        return "complete"

    @property
    def complete(self) -> bool:
        return self.completeness == "complete"

    @property
    def structured(self) -> bool:
        return self.intent != "semantic"

    @property
    def is_aggregate(self) -> bool:
        return self.intent in ("count", "total", "top_product")


def parse_amount(text: str) -> int | None:
    """Return integer cents, or None when the number is not a well-formed amount."""
    raw = text.replace(" ", "")
    if "." in raw and "," in raw:
        decimal_sep = "." if raw.rfind(".") > raw.rfind(",") else ","
        thousands = "," if decimal_sep == "." else "."
        raw = raw.replace(thousands, "").replace(decimal_sep, ".")
    elif "." in raw or "," in raw:
        sep = "." if "." in raw else ","
        parts = raw.split(sep)
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            raw = parts[0] + "." + parts[1]
        elif all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3:
            raw = "".join(parts)
        else:
            return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if value < 0 or value != value.quantize(Decimal("0.01")):
        return None
    return int(value * 100)


def _currency(*markers: str | None) -> str | None:
    for marker in markers:
        if not marker:
            continue
        marker = marker.lower()
        if marker in ("$", "usd", "dollari", "dollar", "dollars"):
            return "usd"
        return "eur"
    return None


class QueryParser:
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock

    def parse(self, question: str) -> QueryPlan:
        text = english_dates(" ".join(question.lower().replace("’", "'").replace("`", "'").split()))
        notes: list[str] = []
        text = corrected_typos(text, notes)

        language = reply_language(question)
        for metric, pattern in _UNSUPPORTED:
            if pattern.search(text):
                return QueryPlan(intent="unsupported_metric", unsupported_metric=metric, language=language)

        ids_found: list[uuid.UUID] = []
        for match in _UUID.finditer(question):
            if _TENANT_WORD.search(question[max(0, match.start() - 30) : match.start()].lower()):
                notes.append(FOREIGN_TENANT_NOTE)
            else:
                ids_found.append(uuid.UUID(match.group(0).lower()))
        ids = tuple(dict.fromkeys(ids_found))
        if _OTHER_TENANTS.search(text):
            notes.append(FOREIGN_TENANT_NOTE)
        scrubbed = _UUID.sub(" ", text)
        amount, currency, scrubbed = self._amounts(scrubbed, notes)
        products = self._products(scrubbed)
        quantity = self._quantity(scrubbed)
        created_from, created_to = self._dates(scrubbed)
        paid_after_expiry = bool(_PAID_AFTER_EXPIRY.search(scrubbed))
        recovered = bool(_RECOVERED.search(scrubbed))
        recovery_source = "reconciliation" if recovered and _RECONCILIATION.search(scrubbed) else None
        statuses = self._statuses(_PAID_AFTER_EXPIRY.sub(" ", scrubbed))
        if paid_after_expiry:
            statuses = tuple(s for s in statuses if s not in (PaymentStatus.EXPIRED, PaymentStatus.PAID))
        if not statuses and _SALES.search(scrubbed):
            statuses = (PaymentStatus.PAID,)

        try:
            filters = PaymentFilters(
                payment_ids=ids,
                statuses=statuses,
                product_ids=products,
                quantity=quantity,
                amount=amount,
                currency=currency,
                created_from=created_from,
                created_to=created_to,
                recovered=True if recovered else None,
                recovery_source=recovery_source,
                paid_after_expiry=True if paid_after_expiry else None,
            )
        except ValidationError:
            return QueryPlan(intent="semantic", notes=("invalid filters",), language=language)

        asked: tuple[PaymentStatus, ...] = ()
        plural = bool(_PLURAL_PAYMENTS.search(scrubbed)) and not filters.payment_ids
        if filters.identifies_payments and filters.statuses and not plural:
            asked, filters = filters.statuses, filters.model_copy(update={"statuses": ()})

        intent = self._intent(text, filters, asked)
        requested = next((name for name, pattern in _RESTRICTED if pattern.search(scrubbed)), None)
        if intent in ("count", "total") and (requested or filters.payment_ids):
            intent = "find"
        if intent == "semantic":
            return QueryPlan(
                intent="semantic",
                notes=tuple(dict.fromkeys(notes)),
                language=language,
                payment_set_question=asks_for_payment_set(text),
            )
        plan = QueryPlan(
            intent=intent,
            filters=filters,
            asked_statuses=asked,
            notes=tuple(dict.fromkeys(notes)),
            language=language,
            requested_attribute=requested if intent == "find" else None,
            ranking=intent == "top_product" and bool(_RANKING_REQUEST.search(text)),
        )
        listing = intent == "find" and not filters.payment_ids and not _EXPLANATION.search(text)
        if filters.payment_ids:
            return replace(plan, listing=listing)
        body = _UUID.sub(" ", text)
        found = constraint_coverage(body, plan) if plan.requested_attribute is None else Coverage()
        if found.excluded_products or found.excluded_statuses:
            filters = filters.model_copy(
                update={
                    "product_ids": tuple(p for p in filters.product_ids if p not in found.excluded_products),
                    "statuses": tuple(s for s in filters.statuses if s not in found.excluded_statuses),
                }
            )
            asked = tuple(s for s in asked if s not in found.excluded_statuses)
        plan = replace(plan, filters=filters, asked_statuses=asked)
        unresolved = list(unresolved_terms(body, plan))
        backend_written = listing or plan.is_aggregate
        exclusions = [t for t in found.unresolved if " … " in t]
        for term in found.unresolved if backend_written else exclusions:
            if not any(term in known for known in unresolved):
                unresolved.append(term)
        return replace(
            plan,
            unresolved=tuple(dict.fromkeys(unresolved)),
            ambiguous=found.ambiguous if backend_written else (),
            unsupported_constraints=found.unsupported if backend_written else (),
            constraints=found.recognized,
            listing=listing,
        )

    @staticmethod
    def _intent(text: str, filters: PaymentFilters, asked: tuple[PaymentStatus, ...]) -> Intent:
        if _TOP_PRODUCT.search(text):
            return "top_product"
        if _MONEY.search(text) and not filters.payment_ids:
            return "total"
        if _COUNT.search(text) and (_PAYMENT_WORD.search(text) or _COUNTED_PRODUCT.search(text)):
            return "count"
        listed = bool(_LIST_VERB.search(text) and _PLURAL_PAYMENTS.search(text))
        if _TOTAL.search(text) and not filters.payment_ids and not (listed and not _TOTAL_WORD.search(text)):
            return "total"
        if filters.payment_ids or (filters.amount is not None):
            return "find"
        descriptors = bool(filters.product_ids or filters.quantity or filters.created_from or filters.created_to)
        derived = filters.recovered is not None or filters.paid_after_expiry is not None
        if (descriptors or derived or asked) and (_LISTING.search(text) or _PAYMENT_WORD.search(text)):
            return "find"
        if filters.statuses and _LISTING.search(text):
            return "find"
        if filters.statuses and _STATUS_SUBJECT.search(text) and not _OPEN_QUESTION.search(text):
            return "find"
        if listed and not _OPEN_QUESTION.search(text):
            return "find"
        return "semantic"

    @staticmethod
    def _amounts(text: str, notes: list[str]) -> tuple[AmountRange | None, str | None, str]:
        match = _RANGE.search(text)
        if match and any(match.group(g) for g in ("c1", "c2", "c3", "c4")):
            low, high = parse_amount(match.group("low")), parse_amount(match.group("high"))
            if low is not None and high is not None and low <= high:
                currency = _currency(match.group("c1"), match.group("c2"), match.group("c3"), match.group("c4"))
                return (
                    AmountRange(minimum=low, maximum=high),
                    currency,
                    text[: match.start()] + " " + text[match.end() :],
                )
        minimum: tuple[int, bool] | None = None
        maximum: tuple[int, bool] | None = None
        exact: int | None = None
        currency = None
        for found in _AMOUNT.finditer(text):
            cents = parse_amount(found.group("a") or found.group("b"))
            if cents is None:
                notes.append(f"ignored amount {found.group(0)!r}")
                continue
            currency = currency or _currency(found.group("pre"), found.group("post"))
            before = text[max(0, found.start() - 30) : found.start()]
            for pattern, side, inclusive in _COMPARATORS:
                if re.search(pattern, before):
                    if side == "min":
                        minimum = (cents, inclusive)
                    else:
                        maximum = (cents, inclusive)
                    break
            else:
                exact = cents
        if exact is not None and minimum is None and maximum is None:
            amount: AmountRange | None = AmountRange.exactly(exact)
        elif minimum is not None or maximum is not None:
            try:
                amount = AmountRange(
                    minimum=minimum[0] if minimum else None,
                    maximum=maximum[0] if maximum else None,
                    minimum_inclusive=minimum[1] if minimum else True,
                    maximum_inclusive=maximum[1] if maximum else True,
                )
            except ValidationError:
                notes.append("contradictory amount bounds")
                amount = None
        else:
            amount = None
        return amount, currency, _AMOUNT.sub(" ", text)

    @staticmethod
    def _products(text: str) -> tuple[str, ...]:
        found: list[str] = []
        for product_id, aliases in _PRODUCT_ALIASES.items():
            names = (*aliases, CATALOG[product_id].name.lower(), product_id)
            bare = _BARE_PRODUCT.get(product_id)
            if any(re.search(rf"\b{re.escape(name)}\b", text) for name in names) or (bare and bare.search(text)):
                found.append(product_id)
        return tuple(found)

    @staticmethod
    def _quantity(text: str) -> int | None:
        names = "|".join(re.escape(a) for aliases in _PRODUCT_ALIASES.values() for a in aliases)
        words = "|".join(_NUMBER_WORDS)
        match = re.search(rf"\b(?P<n>\d{{1,2}}|{words})\s+(?:x\s+)?(?:{names})\b", text) or _QUANTITY.search(text)
        if not match:
            return None
        value = match.group("n")
        return int(value) if value.isdigit() else _NUMBER_WORDS[value]

    @staticmethod
    def _statuses(text: str) -> tuple[PaymentStatus, ...]:
        found: list[PaymentStatus] = []
        for status, pattern in _STATUS_PATTERNS:
            if re.search(pattern, text):
                found.append(status)
                text = re.sub(pattern, " ", text)
        return tuple(found)

    def _dates(self, text: str) -> tuple[datetime | None, datetime | None]:
        now = self._clock().astimezone(UTC)
        today = datetime(now.year, now.month, now.day, tzinfo=UTC)

        def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
            start = datetime(year, month, 1, tzinfo=UTC)
            return start, start + timedelta(days=monthrange(year, month)[1])

        def day(value: str) -> datetime | None:
            try:
                return datetime.fromisoformat(value).replace(tzinfo=UTC)
            except ValueError:
                return None

        if match := _ISO_RANGE.search(text):
            start, end = day(match.group(1)), day(match.group(2))
            if start and end and start <= end:
                return start, end + timedelta(days=1)
        if re.search(r"\boggi\b|\btoday\b", text):
            return today, today + timedelta(days=1)
        if re.search(r"\bieri\b|\byesterday\b", text):
            return today - timedelta(days=1), today
        if re.search(r"\bquesta settimana\b|\bthis week\b", text):
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=7)
        if re.search(r"\bquesto mese\b|\bthis month\b", text):
            return month_bounds(today.year, today.month)
        if re.search(r"\b(?:mese scorso|scorso mese|last month)\b", text):
            year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            return month_bounds(year, month)
        if re.search(r"\bquest'anno\b|\bthis year\b", text):
            return datetime(today.year, 1, 1, tzinfo=UTC), datetime(today.year + 1, 1, 1, tzinfo=UTC)
        if qualified := qualified_date(text, today):
            return qualified[0], qualified[1]
        if match := _ISO_DAY.search(text):
            if match.group(1):
                start = day(match.group(1))
            else:
                try:
                    start = datetime(int(match.group(4)), int(match.group(3)), int(match.group(2)), tzinfo=UTC)
                except ValueError:
                    start = None
            if start:
                return start, start + timedelta(days=1)
        if match := _DAY_MONTH.search(text):
            month = _MONTHS[match.group("month")]
            year = int(match.group("year")) if match.group("year") else self._year_for(month, today)
            try:
                start = datetime(year, month, int(match.group("day")), tzinfo=UTC)
            except ValueError:
                return None, None
            return start, start + timedelta(days=1)
        if match := _MONTH.search(text):
            month = _MONTHS[match.group("month")]
            year = int(match.group("year")) if match.group("year") else self._year_for(month, today)
            return month_bounds(year, month)
        return None, None

    @staticmethod
    def _year_for(month: int, today: datetime) -> int:
        return today.year if month <= today.month else today.year - 1


_NEGATION = re.compile(r"\b(?:non|not|no|senza|without|mai|never|ancora da|still to|yet to)\b|\b\w+n't\b")
_NEGATABLE = re.compile(
    r"\b(?:fallit\w*|scadut\w*|pagat\w*|pag(?:a|ano|hi|asse|are)\b|apert\w*|riuscit\w*|andat\w* a buon fine"
    r"|complet\w*|rifiutat\w*"
    r"|incass\w*|riscoss\w*|ricevut\w*|failed|expired|paid|succeeded|completed|declined|collected|received)"
)
_TEMPORAL = re.compile(
    r"\b(?:dopo|prima|dal|dalla|fino|entro|tra|fra|after|before|since|until|between|ultim\w*|settiman\w*"
    r"|met[àa]|quindicina|inizio|fine di|week|half|weekend|beginning|end of|early|late)\b"
)
_RANKING = re.compile(
    r"\b(?:ultim[oaie]|più recent\w*|piu recent\w*|primo|più alt[oaie]|più bass[oaie]|più grand\w*"
    r"|più piccol\w*|di più|di meno|latest|most recent|highest|lowest|largest|smallest|biggest|the most|the least)\b"
)
_VAGUE_PERIOD = re.compile(
    r"\b(?:(?:last |next |this |la |lo |l')?(?:primavera|estate|autunno|inverno|spring|summer|autumn|fall|winter)"
    r"(?: scors[oa]| passat[oa])?|trimestre|semestre|quarter|anno scorso|scorso anno|last year|scors[oa]|passat[oa])\b"
)
_HANDLED_PERIOD = (
    r"\b(?:mese scorso|scorso mese|last month|questo mese|this month|questa settimana|this week|quest'anno|this year)\b"
)
_QUANTITY_WORD = re.compile(r"\b(?:quantità|quantita|quantity|pezzi|unità|units?|copie)\b")
_EXPLANATION = re.compile(
    r"perch[eé]|come mai|\bcome\b|\bcosa\b|\bcos'|\bchi\b|\bquando\b|\bquanto tempo\b|\bmotiv|\bragion"
    r"|\bwhy\b|\bhow\b|\bwhat\b|\bwho\b|\bwhen\b|\breason|\bquante volte\b|\btentativ|\battempt"
    r"|\berror|\bstorico\b|\bhistory\b|\bdettagl|\bdetail|\binfo\b|\bspiega|\bexplain|\bo\b"
)
_FILTER_WORDS = (
    "fallito", "fallita", "falliti", "fallite", "scaduto", "scaduta", "scaduti", "scadute",
    "pagato", "pagata", "pagati", "pagate", "aperto", "aperta", "aperti", "aperte",
    "rifiutato", "rifiutata", "rifiutati", "rifiutate", "superiore", "superiori", "inferiore", "inferiori",
    "maggiore", "maggiori", "minore", "minori", "quantità", "expired", "failed", "declined",
)  # fmt: skip
_COMMON_WORDS = frozenset(
    ("pagamento", "pagamenti", "pagare", "payment", "payments", "importo", "importi", "quanti", "quante", "quanto")
)
_OPEN_PATTERN = dict(_STATUS_PATTERNS)[PaymentStatus.OPEN]


def word_window(text: str, end: int, size: int) -> str:
    """The text before ``end``, at most ``size`` characters, never starting inside a word."""
    start = max(0, end - size)
    if start > 0 and text[start - 1].isalnum():
        while start < end and text[start].isalnum():
            start += 1
    return text[start:end]


def unresolved_terms(text: str, plan: QueryPlan) -> tuple[str, ...]:
    """Parts of a structured question that the plan does not represent.

    A plan with unresolved terms must not be presented as a complete answer: an empty result
    is not proof of absence, a count or a total may answer a different question. Detected,
    generically: numbers no filter accounts for, date expressions qualified by an operator
    the parser does not model ("dopo il 25 ottobre", "prima metà di ottobre"), negations of a
    status or of a money verb, superlatives, a quantity word without a quantity filter, and
    misspellings of filter words.
    """
    text = text.replace("a buon fine", "a_buon_fine")
    consumed: list[tuple[int, int]] = []
    dates: list[tuple[int, int]] = []
    for pattern in (_ISO_RANGE, _DAY_RANGE, _MONTH_PART, _DAY_OPERATOR):
        consumed += [m.span() for m in pattern.finditer(text)]
    for pattern in (_ISO_DAY, _DAY_MONTH, _MONTH):
        for match in pattern.finditer(text):
            if not any(a <= match.start() < b for a, b in consumed):
                consumed.append(match.span())
                dates.append(match.span())
    range_match = _RANGE.search(text)
    if range_match and any(range_match.group(g) for g in ("c1", "c2", "c3", "c4")):
        consumed.append(range_match.span())
    consumed += [m.span() for m in _AMOUNT.finditer(text)]
    if plan.filters.quantity is not None:
        names = "|".join(re.escape(a) for aliases in _PRODUCT_ALIASES.values() for a in aliases)
        consumed += [m.span() for m in re.finditer(rf"\b\d{{1,2}}\s+(?:x\s+)?(?:{names})\b", text)]
        consumed += [m.span() for m in _QUANTITY.finditer(text)]

    def inside(position: int) -> bool:
        return any(a <= position < b for a, b in consumed)

    terms: list[str] = []
    for match in re.finditer(r"\d+(?:[.,]\d+)?", text):
        if not inside(match.start()):
            before = text[max(0, match.start() - 20) : match.start()].split()
            terms.append(" ".join([*before[-1:], match.group(0)]))
    for start, end in dates:
        window = text[max(0, start - 30) : start]
        operator = list(_TEMPORAL.finditer(window)) or list(_TEMPORAL.finditer(text[start:end]))
        if operator:
            terms.append(f"{operator[-1].group(0)} … {text[start:end].strip()}")
    periods = re.sub(_HANDLED_PERIOD, " ", text)
    terms += [m.group(0) for m in _VAGUE_PERIOD.finditer(periods) if not inside(m.start())]
    quantity_word = _QUANTITY_WORD.search(text)
    counted = plan.filters.quantity is not None or plan.intent == "top_product"
    if quantity_word and not counted and not any(quantity_word.group(0) in t for t in terms):
        terms.append(quantity_word.group(0))
    negatable = re.sub(_FAILED_PHRASES, " ", re.sub(_OPEN_PATTERN, " ", text))
    unpaid = bool(plan.filters.statuses) and PaymentStatus.PAID not in plan.filters.statuses
    for match in _NEGATABLE.finditer(negatable):
        if unpaid and match.group(0).startswith(("pag", "paid")):
            continue
        window = word_window(negatable, match.start(), 25)
        if negation := list(_NEGATION.finditer(window)):
            terms.append(f"{negation[-1].group(0)} … {match.group(0)}")
    if plan.intent != "top_product":
        terms += [m.group(0) for m in _RANKING.finditer(text) if not inside(m.start())]
    for word in re.findall(r"[a-zàèéìòù]{6,}", text):
        if word in _FILTER_WORDS or word in _COMMON_WORDS:
            continue
        if close := get_close_matches(word, _FILTER_WORDS, n=1, cutoff=0.85):
            terms.append(f"{word} ({close[0]}?)")
    return tuple(dict.fromkeys(t.replace("a_buon_fine", "a buon fine") for t in terms))


_SET_NOUN = (
    r"(?:pagament[oi]|transazion[ei]|checkout|vendit[ae]|acquist[oi]|ordin[ei]|incass[oi]|addebit[oi]"
    r"|payments?|transactions?|checkouts?|sales?|purchases?|orders?|charges?)"
)
_PLURAL_NOUN = (
    r"(?:pagamenti|transazioni|checkout|vendite|acquisti|ordini|incassi|addebiti"
    r"|payments|transactions|checkouts|sales|purchases|orders|charges)"
)
_SET_QUESTIONS = (
    re.compile(rf"\b(?:quant[ie]|how many|numero (?:di|dei|delle)|number of)\b(?:\s+[\w'’]+){{0,3}}?\s+{_SET_NOUN}\b"),
    _MONEY,
    re.compile(
        rf"\b(?:elenco|elenca\w*|lista|mostra\w*|fammi vedere|quali|which|list|show|ci sono|are there|tutti i|all)\b"
        rf"(?:\s+[\w'’]+){{0,4}}?\s+{_PLURAL_NOUN}\b"
    ),
)


_MEASURE = re.compile(
    r"\bquant[ie] (?:minuti|ore|giorni|secondi|settimane|mesi|tentativi|volte)\b"
    r"|\bhow (?:long|many (?:minutes|hours|days|seconds|weeks|months|attempts|times|retries))\b"
)
_STATUS_PLURAL = re.compile(
    r"\b(?:pagat[ie]|scadut[ie]|fallit[ie]|apert[ie]|rifiutat[ie]|in sospeso|paid|expired|failed|unpaid)\b"
)


def asks_for_payment_set(text: str) -> bool:
    """A question whose answer is a count, a total or a list of payments.

    Such an answer can only be computed with SQL over all of the merchant's payments. When the
    parser cannot turn the question into filters, the semantic route would let the model count or
    sum over the few chunks it retrieved, so the service abstains instead.
    """
    if _MEASURE.search(text):
        return False
    counted_or_summed, listed = _SET_QUESTIONS[:2], _SET_QUESTIONS[2]
    if any(pattern.search(text) for pattern in counted_or_summed):
        return True
    if listed.search(text) and not _OPEN_QUESTION.search(text):
        return True
    return bool(_STATUS_PLURAL.search(text)) and not _EXPLANATION.search(text) and not re.search(_MODAL, text)


_EN_MONTH_DAY = re.compile(
    r"\b(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{4}))?\b"
)


def english_dates(text: str) -> str:
    """ "october 9, 2026" -> "9 october 2026": one day-month order for the rules below."""

    def swap(match: re.Match[str]) -> str:
        year = f" {match.group('year')}" if match.group("year") else ""
        return f"{match.group('day')} {match.group('month')}{year}"

    return _EN_MONTH_DAY.sub(swap, text)


def qualified_date(text: str, today: datetime) -> tuple[datetime | None, datetime | None, tuple[int, int]] | None:
    """Day ranges, parts of a month and day operators ("dopo il 25 ottobre"): (from, to, span) or None."""

    def year_of(match: re.Match[str], month: int) -> int:
        if match.group("year"):
            return int(match.group("year"))
        return today.year if month <= today.month else today.year - 1

    def day(year: int, month: int, value: str) -> datetime | None:
        try:
            return datetime(year, month, int(value), tzinfo=UTC)
        except ValueError:
            return None

    if match := _DAY_RANGE.search(text):
        month = _MONTHS[match.group("month")]
        year = year_of(match, month)
        start, end = day(year, month, match.group("d1")), day(year, month, match.group("d2"))
        if start and end and start <= end:
            return start, end + timedelta(days=1), match.span()
    if match := _MONTH_PART.search(text):
        month = _MONTHS[match.group("month")]
        year = year_of(match, month)
        first = datetime(year, month, 1, tzinfo=UTC)
        after = first + timedelta(days=monthrange(year, month)[1])
        part = match.group("part").replace("meta", "metà")
        bounds = {
            "prima metà": (first, first + timedelta(days=15)),
            "first half": (first, first + timedelta(days=15)),
            "prima quindicina": (first, first + timedelta(days=15)),
            "seconda metà": (first + timedelta(days=15), after),
            "second half": (first + timedelta(days=15), after),
            "seconda quindicina": (first + timedelta(days=15), after),
            "prima settimana": (first, first + timedelta(days=7)),
            "first week": (first, first + timedelta(days=7)),
            "ultima settimana": (after - timedelta(days=7), after),
            "last week": (after - timedelta(days=7), after),
        }[part]
        return bounds[0], bounds[1], match.span()
    if match := _DAY_OPERATOR.search(text):
        month = _MONTHS[match.group("month")]
        start = day(year_of(match, month), month, match.group("day"))
        if start is None:
            return None
        op = match.group("op")
        if op in ("dopo il", "dopo l'", "after"):
            return start + timedelta(days=1), None, match.span()
        if op in ("prima del", "prima dell'", "before"):
            return None, start, match.span()
        if op in ("fino al", "fino all'", "entro il", "entro l'", "until", "by"):
            return None, start + timedelta(days=1), match.span()
        return start, None, match.span()
    return None


_PRODUCT_NAMES = "|".join(
    sorted(
        {
            re.escape(name)
            for pid, aliases in _PRODUCT_ALIASES.items()
            for name in (*aliases, CATALOG[pid].name.lower())
        },
        key=len,
        reverse=True,
    )
)
_COUNTED_PRODUCT = re.compile(
    rf"\b(?:quant[ie]|how many|numero (?:di|dei|delle)|number of)\s+(?:checkouts?|vendite|sales|{_PRODUCT_NAMES})\b"
)
_TOTAL_WORD = re.compile(r"\btotal|\bsomma\b|\bin tutto\b|\bquanto\b|\bhow much\b")
_STATUS_ADJECTIVE = (
    r"(?:pagat[oaie]|scadut[oaie]|fallit[oaie]|apert[oaie]|rifiutat[oaie]|andat[oaie] male|non riuscit[oaie]"
    r"|paid|expired|failed|declined|unpaid|open)"
)
_STATUS_SUBJECT = re.compile(
    rf"\b(?:{_SET_NOUN}|{_PRODUCT_NAMES}|quelli|quelle|those)(?:\s+(?:ancora|già|non))?\s+{_STATUS_ADJECTIVE}\b"
)
_MODAL = r"\b(?:possono|può|puo|posso|devo|deve|devono|succede|succedono|can|could|should|happens?)\b"
_OPEN_QUESTION = re.compile(_EXPLANATION.pattern.replace(r"|\bo\b", "") + "|" + _MODAL)
_TYPO_TARGETS = (
    "pagamenti", "pagamento", "transazioni", "transazione", "incassato", "incassati", "incassare",
    *(name for name in _MONTHS if len(name) >= 6),
)  # fmt: skip
_KNOWN_WORDS = frozenset(
    (*_TYPO_TARGETS, *_FILTER_WORDS, "incasso", "incassi", "incassa", "incassate", "incassata", "pagamentii")
)


def corrected_typos(text: str, notes: list[str]) -> str:
    """Misspelled payment nouns, money verbs and month names, corrected before the rules run.

    Status words are not corrected here: a misspelled status stays visible to plan coverage, which
    reports it instead of guessing a filter. Each correction is recorded in ``notes``.
    """

    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        if word in _KNOWN_WORDS:
            return word
        candidates = [t for t in _TYPO_TARGETS if t[:3] == word[:3] and abs(len(t) - len(word)) <= 2]
        close = get_close_matches(word, candidates, n=1, cutoff=0.85)
        if not close:
            return word
        notes.append(f"interpreted {word!r} as {close[0]!r}")
        return close[0]

    return re.sub(r"[a-zàèéìòù]{6,}", fix, text)


_NEUTRAL_WORDS = frozenset(
    " ".join(
        (
            "a ad al allo alla ai agli alle all da dal dallo dalla dai dagli dalle dall de del dello della dei degli",
            "delle dell di d e ed è era erano sono sia siano stato stata stati state essere c ci ce che chi cui il lo",
            "la i gli le l un uno una in nel nello nella nei negli nelle nell su sul sullo sulla sui sugli sulle sull",
            "per con col coi tra fra o oppure ma anche pure poi già ancora solo soltanto proprio tutto tutti tutte",
            "tutta ogni qualche qualcuno alcuni alcune quello quelli quelle quella questo questi queste questa ne non",
            "né no sì si se mi ti me te noi voi vi lui lei loro mio mia miei mie tuo tua tuoi tue nostro nostra nostri",
            "nostre suo sua suoi sue ho hai ha abbiamo avete hanno avuto ricevuto fatto fatti fatta fatte mai sempre",
            "come com dove quale quali quanto quanta quanti quante qual cosa cos perché perche successo succede",
            "successe andato andata andati andate andò finito finita finiti fine roba po intendo contando davvero",
            "mostra mostrami mostrarmi mostri fammi fammeli fammelo fai fare vedere vedi dimmi dirmi dammi darmi dare",
            "dici elenca elencami elenco lista trova trovami trovi cerca cercami cerchi calcola calcolami vorrei",
            "voglio",
            "volevo sapere serve servirebbe servono puoi potresti può potrei riesci gentilmente cortesia favore prego",
            "grazie ciao ehi hey salve buongiorno buonasera ok okay allora quindi dunque però invece insomma ecco",
            "veloce velocemente subito rapido adesso ora attualmente momento finora senza cliente clienti acquirente",
            "acquirenti paga pagano paghi pagasse pagare pagamento pagamenti transazione transazioni ordine ordini",
            "acquisto acquisti vendita vendite incasso incassi checkout importo importi soldi euro eur prodotto",
            "prodotti dati numero totale totali complessivo complessivamente risultano risulta risultato esiste",
            "esistono payflow status the a an of to in on at by for from with and or but not no is are was were be",
            "been being do does did done have has had having i me my mine we our ours you your yours it its they them",
            "their this that these those there here what which who whom whose how many much any some all every each",
            "please could would can will shall should may might just only also too still yet already ever now",
            "currently right so then tell show list give find get got getting see look check want need know let us",
            "about up out over into as if than kindly thanks thank hi hello quick quickly through via actually",
            "make makes question domanda",
            "happened happen sitting customer customers buyer buyers pay pays payment payments transaction",
            "transactions order orders purchase purchases sale sales checkouts amount amounts money euros product",
            "products data number numbers total totals overall altogether summary count",
        )
    ).split()
)
_AMBIGUOUS_WORDS = frozenset(
    " ".join(
        (
            "grande grandi grosso grossa grossi grosse piccolo piccola piccoli piccole importante importanti alto alta",
            "alti alte basso bassa bassi basse strano strana strani strane sospetto sospetta sospetti sospette anomalo",
            "anomala anomali anomale problematico problematica problematici problematiche recente recenti vecchio",
            "vecchia vecchi vecchie big bigger large larger huge small smaller tiny high low significant major minor",
            "strange weird suspicious unusual odd problematic recent old older",
        )
    ).split()
)
_UNSUPPORTED_WORDS = frozenset(
    " ".join(
        (
            "weekend fine-settimana lunedì martedì mercoledì giovedì venerdì sabato sabati domenica domeniche monday",
            "mondays tuesday tuesdays wednesday wednesdays thursday thursdays friday fridays saturday saturdays sunday",
            "sundays mattina mattino pomeriggio sera notte morning afternoon evening night orario ore hour hours",
        )
    ).split()
)
_DESCRIBES = {
    "paid_after_expiry": re.compile(
        r"\b(?:pagat\w*|paid|arrivat\w*|confermat\w*|confirmed|sessione|session|checkout|scadenza|scadut\w*"
        r"|expir\w*|dopo|after|link)\b"
    ),
    "recovered": re.compile(
        r"\b(?:recuperat\w*|recovered|riconciliazion\w*|reconcil\w*|tramite|dopo|after|creazione|creation|esito"
        r"|outcome|risposta|response|stripe|pers[aoie]|lost)\b"
    ),
    "creation_unknown": re.compile(r"\b(?:esito|outcome|creazione|creation|conosce|pers[aoie]|lost)\b"),
    "pending": re.compile(r"\b(?:bloccat\w*|stuck|ferm[oiae])\b"),
    "paid": re.compile(r"\b(?:chius[oaie]|conclus[oaie]|closed|arrivat\w*|ricevut\w*|received)\b"),
    "unpaid": re.compile(r"\b(?:pers[aoie]|lost|mancat\w*|missed)\b"),
    "product": re.compile(r"\b(?:packs?|piano|piani|plans?|licenz[ae]|licen[cs]es?)\b"),
    "amount": re.compile(
        r"\b(?:cost\w*|valgono|vale|valore|value|worth|exactly|esattamente|compres[oiae]|inclus\w*)\b"
    ),
    "date": re.compile(r"\b(?:compres[oiae]|inclus\w*)\b"),
    "total": re.compile(
        r"\b(?:gross|lord[oiae]|collected|collect|bring|brought|revenue|earned|made|portato|fruttato|reso|tirato"
        r"|entrat[oiae]|incassat\w*|somma|vale|valgono|valore|value|worth)\b"
    ),
}
_EXCLUSION = re.compile(
    r"\b(?:escluso|esclusa|esclusi|escluse|eccetto|tranne|salvo|senza|tolto|tolta|tolti|tolte|a parte|"
    r"except|excluding|excluded|without|other than|apart from|but not|non|not|no|never|mai|\w+n't)\b"
)
_RELATIVE_DATES = re.compile(
    r"\b(?:oggi|today|ieri|yesterday|questa settimana|this week|questo mese|this month|mese scorso|scorso mese"
    r"|last month|quest'anno|this year)\b"
)
_CREATED_WORD = re.compile(r"\bcreat\w*|\bcreated\b")
_COMPARATOR_WORDS = tuple(re.compile(pattern[: -len(_ARTICLE)]) for pattern, _, _ in _COMPARATORS)


@dataclass(frozen=True, slots=True)
class Coverage:
    recognized: tuple[tuple[str, str], ...] = ()
    unresolved: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()
    excluded_products: tuple[str, ...] = ()
    excluded_statuses: tuple[PaymentStatus, ...] = ()


def constraint_coverage(text: str, plan: QueryPlan) -> Coverage:
    """Account for every word of a structured question (ADR 0013).

    A word is explained by a constraint the plan represents (product, status, amount and comparator,
    quantity, date, derived fact, restricted attribute, other-tenant reference), by the intent or the
    subject of the question, or by the closed list of words that cannot change the result. Any other
    word is a constraint the plan does not represent: ambiguous, unsupported, or unrecognised. A product
    or status that the question excludes or negates is reported, never applied as a filter.
    """
    f = plan.filters
    covered: list[tuple[int, int]] = []
    recognized: list[tuple[str, str]] = []

    def cover(kind: str, pattern: re.Pattern[str] | str, when: bool = True) -> list[re.Match[str]]:
        if not when:
            return []
        matches = list(re.finditer(pattern, text))
        for m in matches:
            if m.group(0).strip():
                covered.append(m.span())
                recognized.append((kind, m.group(0).strip()))
        return matches

    excluded_products: list[str] = []
    excluded_statuses: list[PaymentStatus] = []
    unresolved: list[str] = []

    def excluded(m: re.Match[str]) -> str | None:
        window = word_window(text, m.start(), 25)
        negation = list(_EXCLUSION.finditer(window))
        return negation[-1].group(0) if negation else None

    for product_id, aliases in _PRODUCT_ALIASES.items():
        if product_id not in f.product_ids:
            continue
        names = (*aliases, CATALOG[product_id].name.lower(), product_id)
        pattern = "|".join(rf"\b{re.escape(n)}\b" for n in sorted(names, key=len, reverse=True))
        bare = _BARE_PRODUCT.get(product_id)
        for m in cover("product", pattern) + (cover("product", bare) if bare else []):
            if word := excluded(m):
                excluded_products.append(product_id)
                unresolved.append(f"{word} … {m.group(0).strip()}")
    for status, pattern in _STATUS_PATTERNS:
        if status in f.statuses or status in plan.asked_statuses:
            for m in cover("status", pattern):
                if not m.group(0).startswith(("non ", "not ")) and (word := excluded(m)):
                    excluded_statuses.append(status)
                    unresolved.append(f"{word} … {m.group(0).strip()}")
    cover("status", _SALES, PaymentStatus.PAID in f.statuses)
    statuses = {*f.statuses, *plan.asked_statuses}
    cover("status", _DESCRIBES["creation_unknown"], PaymentStatus.CREATION_UNKNOWN in statuses)
    pending = (PaymentStatus.OPEN, PaymentStatus.CREATING, PaymentStatus.CREATION_UNKNOWN)
    cover("status", _DESCRIBES["pending"], bool(statuses & set(pending)))
    cover("status", _DESCRIBES["paid"], PaymentStatus.PAID in statuses)
    cover("status", _DESCRIBES["unpaid"], bool(statuses & {PaymentStatus.FAILED, PaymentStatus.EXPIRED}))
    cover("product", _DESCRIBES["product"], bool(f.product_ids))
    if f.amount is not None:
        cover("amount", _AMOUNT)
        cover("amount", _RANGE)
        cover("amount", _DESCRIBES["amount"])
        if f.amount.minimum != f.amount.maximum:
            for comparator in _COMPARATOR_WORDS:
                cover("amount", comparator)
    cover("quantity", _QUANTITY, f.quantity is not None)
    if f.quantity is not None:
        alias_names = "|".join(re.escape(a) for aliases in _PRODUCT_ALIASES.values() for a in aliases)
        words = "|".join(_NUMBER_WORDS)
        cover("quantity", rf"\b(?:\d{{1,2}}|{words})\s+(?:x\s+)?(?:{alias_names})\b")
    dated = f.created_from is not None or f.created_to is not None
    for date_pattern in (
        _ISO_RANGE,
        _DAY_RANGE,
        _MONTH_PART,
        _DAY_OPERATOR,
        _ISO_DAY,
        _DAY_MONTH,
        _MONTH,
        _RELATIVE_DATES,
    ):
        cover("date", date_pattern, dated)
    cover("date", _DESCRIBES["date"], dated)
    cover("paid_after_expiry", _PAID_AFTER_EXPIRY, f.paid_after_expiry is not None)
    cover("paid_after_expiry", _DESCRIBES["paid_after_expiry"], f.paid_after_expiry is not None)
    cover("recovered", _RECOVERED, f.recovered is not None)
    cover("recovered", _DESCRIBES["recovered"], f.recovered is not None)
    cover("intent", _DESCRIBES["total"], plan.intent == "total")
    cover("recovered", _RECONCILIATION, f.recovery_source == "reconciliation")
    if FOREIGN_TENANT_NOTE in plan.notes:
        cover("tenant", _TENANT_WORD)
        cover("tenant", _OTHER_TENANTS)
    for intent_pattern in (_COUNT, _TOTAL, _MONEY, _TOP_PRODUCT, _LISTING, _LIST_VERB, _COUNTED_PRODUCT):
        cover("intent", intent_pattern)
    for subject_pattern in (_SET_NOUN, _PAYMENT_WORD, _CREATED_WORD):
        cover("subject", subject_pattern)

    def inside(position: int) -> bool:
        return any(a <= position < b for a, b in covered)

    ambiguous: list[str] = []
    unsupported: list[str] = []
    for m in re.finditer(r"[a-zàèéìòù]+(?:-[a-zàèéìòù]+)?", text):
        word = m.group(0)
        if inside(m.start()) or word in _NEUTRAL_WORDS or len(word) == 1:
            continue
        if word in _AMBIGUOUS_WORDS:
            ambiguous.append(word)
        elif word in _UNSUPPORTED_WORDS:
            unsupported.append(word)
        else:
            unresolved.append(word)
    return Coverage(
        recognized=tuple(dict.fromkeys(recognized)),
        unresolved=tuple(dict.fromkeys(unresolved)),
        ambiguous=tuple(dict.fromkeys(ambiguous)),
        unsupported=tuple(dict.fromkeys(unsupported)),
        excluded_products=tuple(dict.fromkeys(excluded_products)),
        excluded_statuses=tuple(dict.fromkeys(excluded_statuses)),
    )
