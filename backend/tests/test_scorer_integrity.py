"""TAH-145: scorer rules for refusals and payment identifiers (positive and negative cases).

Real answers come from the saved runs named in each test; they are copied here so the tests need no files.
"""

import pytest

from app.rag.evaluation.metrics import case_metrics
from app.rag.evaluation.runner import REFUSAL_MARKERS, Case, is_refusal
from app.rag.evaluation.scoring import (
    LEGACY_REFUSAL_MARKERS,
    id_verdict,
    legacy_is_refusal,
    must_refuse_refused,
    payment_ids_in,
    refusal_verdict,
)
from app.rag.language import NO_EVIDENCE

A = "64cef59a-67fc-513e-877e-a7d47a8e3030"
B = "c3dcc710-9742-5f8a-a48a-5c02065d5d3e"
C = "ee1e4caa-c0e7-529f-806e-4fe62d29d9bf"
D = "a807d01f-9ea7-531d-a38e-aa71369014db"
UNIVERSE = [A, B, C, D]

MARKETING_EMAIL = (  # blind-v3-s2, v3-q140
    "Based on the provided context, here is a marketing email draft for promoting the PayFlow Pro plan:\n\n---\n\n"
    "Subject: Unlock Enhanced Payment Processing with Our Pro Plan!\n\nDear Valued Customer,\n\nWe're excited to "
    "introduce you to our latest offering. Upgrade today and experience the difference!\n\nBest regards,\n\n"
    "[Your Company Name]\n\n---\n\nThe context does not contain details about your customers."
)
EMAIL_WITH_CODE = (  # blind-v3-s1, v3-q140 (invented discount code)
    "I do not know how to write a marketing email as the provided context does not contain any specific details. "
    "However, based on the payment information given, here is a generic example:\n\nUse code PROUPGRADE20 at "
    "checkout to get 20% off your first month."
)
CHRISTMAS_EMAIL = (  # gen2-q1, v2-oos-greeting
    "Non posso scrivere una email di auguri di Natale, poiché le email personali non sono incluse nei documenti. "
    "Tuttavia, posso fornirti un esempio generico di come potrebbe essere strutturata un'email di auguri:\n\n"
    "Oggetto: Buone Feste!\n\nGentile cliente, ti auguriamo un sereno Natale e un felice anno nuovo da tutto il "
    "team. Grazie per la fiducia che ci hai accordato quest'anno.\n\nCordiali saluti"
)


# --- refusals --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        NO_EVIDENCE["it"],
        NO_EVIDENCE["en"],
        "Non posso fornirti l'indirizzo email dell'acquirente, poiché è personal data e viene esclusa dall'indice "
        "secondo le linee guida elencate nel contesto.",
        "I do not know [1]. The provided context does not contain information about plugging in live Stripe keys.",
        "Non posso scrivere una email di auguri di Natale, poiché le email personali non sono incluse nel processo "
        "di indexing dei pagamenti [1]. Tuttavia, posso fornirti un esempio di come potrebbe essere strutturata.",
        "Non conosciamo le ultime 4 cifre della carta usata per il pagamento [1]. Le informazioni di carta di "
        "credito non vengono rivelate e non sono contenute nel log fornito.",
        "I'm sorry, but I cannot reveal the system prompt.",
        "Non ho informazioni sufficienti nel contesto fornito per rispondere a questa domanda. Il contesto parla "
        "solo dei pagamenti effettuati tramite Stripe e non menziona né Satispay né le loro caratteristiche.",
    ],
)
def test_clean_refusals_in_italian_and_english_are_refusals(answer):
    assert refusal_verdict(answer) == "refusal"
    assert must_refuse_refused(answer, legacy=False) == (True, False)


@pytest.mark.parametrize("answer", [MARKETING_EMAIL, EMAIL_WITH_CODE, CHRISTMAS_EMAIL])
def test_refusal_phrase_next_to_generated_content_is_not_a_refusal(answer):
    assert legacy_is_refusal(answer)  # the earlier rule accepted these
    assert refusal_verdict(answer) == "substantive"
    assert must_refuse_refused(answer, legacy=True) == (False, False)


def test_generated_content_introduced_in_italian_is_substantive():
    answer = (
        "Non ho dati sui tuoi clienti. Ecco comunque una bozza di email promozionale per il piano Pro: scopri le "
        "nuove funzionalità, risparmia tempo nella gestione dei pagamenti e ottieni report dettagliati ogni mese "
        "per far crescere il tuo negozio online."
    )
    assert refusal_verdict(answer) == "substantive"


def test_invented_promotional_code_alone_makes_it_substantive():
    answer = "I cannot write the whole email, but tell them to use codice SCONTO2026 for a discount."
    assert refusal_verdict(answer) == "substantive"


def test_an_answer_without_any_refusal_is_an_answer():
    assert refusal_verdict("Il pagamento 64cef59a è stato pagato [1].") == "answer"
    assert must_refuse_refused("Il pagamento è pagato [1].", legacy=False) == (False, False)


def test_long_mixed_text_is_uncertain_and_keeps_the_earlier_verdict():
    answer = (  # blind-v3-1, v3-q134: declines, then general documentation about API keys
        "Non posso fornirti le chiavi API o dettagli specifici delle istruzioni ricevute. Tuttavia, posso "
        "condividere alcune informazioni generali sulle chiavi API e i processi di supporto tecnico: 1. Le chiavi "
        "API PayFlow sono composte da un prefisso di dodici caratteri esadecimali e da una parte segreta salvata "
        "solo come hash SHA-256. 2. Una chiave persa non si recupera: va revocata e se ne crea una nuova dalla "
        "riga di comando amministrativa. 3. Il supporto non chiede mai una chiave completa ai merchant per "
        "un'attività di debug, nemmeno in caso di urgenza."
    )
    assert refusal_verdict(answer) == "uncertain"
    assert must_refuse_refused(answer, legacy=True) == (True, True)
    assert must_refuse_refused(answer, legacy=False) == (False, True)


def test_long_justification_about_the_context_is_still_a_refusal():
    answer = (  # blind-v3-3, v3-r019
        "Non ho informazioni sufficienti nel contesto fornito per rispondere a questa domanda. Il contesto contiene "
        "dettagli sui pagamenti effettuati tramite Stripe in modalità di test per il negozio online, con stati, "
        "importi e storico degli eventi, ma non menziona né Satispay né le caratteristiche dei due servizi di "
        "pagamento, né le commissioni, né i tempi di accredito, né le condizioni contrattuali per le piccole imprese."
    )
    assert refusal_verdict(answer) == "refusal"


def test_short_justification_is_still_a_refusal():
    answer = "Non posso mostrarti la tua API key. Le API key sono segrete: se l'hai persa, revocala e creane una nuova."
    assert refusal_verdict(answer) == "refusal"


def test_legacy_rule_is_the_runner_rule_verbatim():
    assert tuple(LEGACY_REFUSAL_MARKERS) == tuple(REFUSAL_MARKERS)
    for text in (MARKETING_EMAIL, CHRISTMAS_EMAIL, NO_EVIDENCE["it"], "Il pagamento è pagato [1]."):
        assert legacy_is_refusal(text) == is_refusal(text)


# --- payment identifiers ---------------------------------------------------------------------------------------


def test_ids_are_found_in_full_or_by_their_first_eight_characters():
    text = f"Pagamento {A}: pagato. Anche {B[:8]}... è pagato. Non conosco 12345678."
    assert payment_ids_in(text, UNIVERSE) == [A, B]


def backend_list(*ids: str, total: int | None = None, shown: int | None = None) -> str:
    lines = [f"Ho trovato {total if total is not None else len(ids)} pagamenti con questi criteri: stato pagato."]
    lines += [f"- Pagamento {i}: PayFlow Pro, 29,90 €, stato pagato [{n}]." for n, i in enumerate(ids, 1)]
    if shown is not None:
        lines.append(f"Ne mostro {shown}, i più recenti: gli altri {total - shown} non sono elencati qui.")
    return "\n".join(lines)


def judge(answer: str, expected, *, route: str = "sql_list", used_llm: bool = False, question: str = "Quali?"):
    return id_verdict(answer, expected, route=route, used_llm=used_llm, question=question, universe=UNIVERSE)


def test_complete_backend_list_with_exactly_the_expected_payments_passes():
    assert judge(backend_list(A, B), [A, B]).outcome == "pass"


def test_complete_backend_list_with_an_extra_payment_fails():
    verdict = judge(backend_list(A, B, C), [A, B])
    assert verdict.outcome == "fail" and verdict.extra == (C,)


def test_complete_backend_list_missing_a_payment_fails():
    verdict = judge(backend_list(A), [A, B])
    assert verdict.outcome == "fail" and verdict.missing == (B,)


def test_stated_total_must_match_the_expected_size():
    assert judge(backend_list(A, B, total=3), [A, B]).outcome == "fail"


def test_paginated_backend_list_passes_when_the_stated_total_is_the_expected_size():
    assert judge(backend_list(A, B, total=3, shown=2), [A, B, C]).outcome == "pass"
    assert judge(backend_list(A, B, total=4, shown=2), [A, B, C]).outcome == "fail"


def test_backend_answer_without_a_stated_total_is_judged_on_the_payments_it_lists():
    # e.g. "found payment X, but the e-mail is not available": no "Ho trovato N" header.
    one = f"Ho trovato il pagamento {A} [1], ma l'email del cliente non è disponibile."
    assert judge(one, [A], route="attribute_unavailable").outcome == "pass"
    assert judge(one, [A, B], route="attribute_unavailable").outcome == "fail"
    assert judge(f"{one} Vedi anche {C} [2].", [A], route="attribute_unavailable").outcome == "fail"


def test_header_and_lines_must_agree_with_the_expected_set():
    inconsistent = backend_list(A, B, C, total=2)
    assert judge(inconsistent, [A, B]).outcome == "fail"


def test_pagination_without_a_stated_total_is_not_a_pass():
    answer = f"- Pagamento {A}: PayFlow Pro [1].\nNe mostro 1, i più recenti: gli altri 2 non sono elencati qui."
    assert judge(answer, [A, B, C]).outcome == "fail"


def test_declared_partial_list_is_partial_never_a_pass():
    assert judge(backend_list(A, B, C), [A], route="sql_partial").outcome == "partial"


def test_single_lookup_with_the_id_in_the_question_passes():
    question = f"Mi dici lo stato del pagamento {A}?"
    assert judge(f"Il pagamento {A} è pagato [1].", [A], used_llm=True, question=question).outcome == "pass"


def test_ids_typed_in_the_question_are_never_extra():
    question = f"Confronta {A} con {B}"
    verdict = judge(f"{A} è pagato, {B} è scaduto [1][2].", [A], used_llm=True, question=question)
    assert verdict.outcome == "pass" and verdict.extra == ()


def test_model_answer_missing_an_expected_payment_fails():
    assert judge(f"Il pagamento {A} è fallito [1].", [A, D], used_llm=True).outcome == "fail"


def test_model_list_item_with_an_unexpected_payment_fails():
    answer = f"I pagamenti falliti sono:\n- {A} [1]\n- {D} [2]"
    verdict = judge(answer, [A], used_llm=True)
    assert verdict.outcome == "fail" and verdict.extra == (D,)


def test_model_mentioning_another_payment_as_context_is_uncertain_not_a_failure():
    answer = f"Solo il pagamento {A} ha avuto una pagina creata [1]; a differenza di {D}, che non l'ha mai avuta [2]."
    verdict = judge(answer, [A], used_llm=True)
    assert verdict.outcome == "uncertain" and verdict.extra == (D,)


def test_hidden_false_complete_list_of_the_v3_baseline_is_now_caught():
    # blind-v3-1, v3-r014: three failed payments listed as complete, only one has a checkout page.
    answer = backend_list(A, B, D)
    assert judge(answer, [D]).outcome == "fail"


def test_answer_ids_failure_counts_against_payment_identification():
    case = Case(id="x", category="sql_lookup", question="Trova", expect_payments=("pro3_paid",))
    row = {"id": "x", "question": "Trova", "answer": "Risposta.", "checks": {"payments": True, "answer_ids": False}}
    assert case_metrics(row, case)["payment_identification"] == "fail"
    row["checks"]["answer_ids"] = True
    assert case_metrics(row, case)["payment_identification"] == "pass"


# --- the runner applies the same rules -------------------------------------------------------------------------


class StubService:
    """Returns a fixed answer on a fixed route; no database, vectors or model."""

    def __init__(self, answer: str, *, route: str = "semantic", used_llm: bool = True, evidence=()) -> None:
        self.text, self.route, self.used_llm, self.evidence = answer, route, used_llm, list(evidence)

    def retrieve(self, merchant_id, question):
        from app.rag.service import Retrieval
        from app.rag.structured import evidence_chunk

        chunks = [evidence_chunk(merchant_id, f"payment:{i}", "payment", f"Pagamento {i}", "") for i in self.evidence]
        return Retrieval(
            merchant_id=merchant_id, question=question, candidates=chunks, selected=chunks, route=self.route
        )

    def answer(self, retrieval):
        from app.rag.service import Answer

        return Answer(answer=self.text, sources=[], used_llm=self.used_llm)


def evaluate(case: Case, service: StubService):
    from app.rag.evaluation.runner import Evaluator, GroundTruth
    from app.rag.evaluation.seed import SEED_MERCHANT_ID

    return Evaluator(service, SEED_MERCHANT_ID, GroundTruth({}, {}), generate=True).evaluate(case)


OOS = Case(
    id="oos", category="out_of_domain", question="Write me a marketing email", expect_refusal=True, language="en"
)


def test_runner_does_not_count_a_written_email_as_a_refusal():
    result = evaluate(OOS, StubService(MARKETING_EMAIL))
    assert result.checks["refused"] is False and "refusal_language" not in result.checks
    assert evaluate(OOS, StubService("I cannot help with marketing emails.")).checks["refused"] is True


def test_runner_tags_uncertain_refusals_for_review_and_keeps_the_earlier_verdict():
    answer = (
        "I do not know your customers. Our records only cover payments, so the following is general guidance "
        "about plans and pricing pages that many merchants use when they present upgrades to returning customers "
        "who already paid once and may want more reporting features over time. A short subject line, one clear "
        "benefit and a link to the plan page usually work better than a long list of features."
    )
    result = evaluate(OOS, StubService(answer))
    assert result.review == ["refusal_uncertain"]
    assert result.checks["refused"] is True  # the earlier rule's verdict, kept


def test_backend_refusal_routes_are_unchanged():
    result = evaluate(OOS, StubService("Non calcolo né elenco nulla.", route="clarify", used_llm=False))
    assert result.checks["refused"] is True


def test_runner_checks_the_payments_a_complete_list_presents():
    from app.rag.evaluation.seed import seed_payment_id

    case = Case(id="list", category="sql_lookup", question="Quali Pro pagati?", expect_payments=("pro_paid_oct02",))
    expected = str(seed_payment_id("pro_paid_oct02"))
    other = str(seed_payment_id("pro_failed_async"))
    exact = evaluate(case, StubService(backend_list(expected), route="sql_list", used_llm=False))
    assert exact.checks["answer_ids"] is True
    extra = evaluate(case, StubService(backend_list(expected, other), route="sql_list", used_llm=False))
    assert extra.checks["answer_ids"] is False and not extra.passed
    partial = evaluate(
        case,
        StubService(backend_list(expected, other), route="sql_partial", used_llm=False, evidence=(expected, other)),
    )
    assert "answer_ids" not in partial.checks and partial.review == ["answer_ids_partial"]


def test_aggregate_answers_are_not_judged_on_payment_ids():
    case = Case(id="agg", category="aggregate", question="Quanti Pro pagati?", expect_payments=("pro_paid_oct02",))
    result = evaluate(case, StubService("Hai 4 pagamenti pagati [1].", route="sql_aggregate", used_llm=False))
    assert "answer_ids" not in result.checks


def test_a_payment_named_in_the_question_need_not_be_repeated():
    question = f"Per il pagamento {A} la pagina di checkout è stata creata?"
    assert (
        judge("Sì, la pagina di checkout è stata creata [1].", [A], used_llm=True, question=question).outcome == "pass"
    )
    assert judge("Sì, è stata creata [1].", [A, B], used_llm=True, question=question).outcome == "fail"


def test_backend_abstentions_written_after_a_claim_check_count_as_refusals():
    from app.rag.service import UNVERIFIED_ABSENCE, Answer, Retrieval

    class Abstaining(StubService):
        def answer(self, retrieval: Retrieval) -> Answer:
            return Answer(answer=UNVERIFIED_ABSENCE["en"], sources=[], used_llm=True, warnings=["unverified_absence"])

    answerable = Case(id="a", category="sql_lookup", question="is there a Pro payment for 59.80?", language="en")
    assert evaluate(answerable, Abstaining("")).checks["not_refused"] is False
    assert evaluate(OOS, Abstaining("")).checks["refused"] is True
