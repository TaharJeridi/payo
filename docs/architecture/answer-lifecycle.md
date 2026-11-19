# Answer lifecycle

Question → routing → retrieval → evidence → generation → post-processing → response. Everything below is **implemented** unless marked *proposed*.

```mermaid
sequenceDiagram
    participant C as Client
    participant API as /rag/ask
    participant P as QueryParser
    participant S as StructuredRetriever
    participant V as Vector search
    participant M as Qwen
    participant B as Post-processing
    C->>API: question (Bearer key)
    API->>P: parse(question)
    P-->>API: QueryPlan(intent, filters, language, requested_attribute)
    alt structured intent
        API->>S: execute(merchant_id, plan)
        S-->>API: evidence or deterministic answer
    else semantic
        API->>V: search(question, scopes=[merchant, public])
        V-->>API: chunks ≥ RAG_MIN_SCORE
    end
    opt evidence and no deterministic answer
        API->>M: system prompt + fenced numbered evidence
        M-->>API: text
        API->>B: language, fact lines, validation
    end
    API-->>C: answer, sources (cited flags), used_llm, warnings
```

## 1. Validation of the request

`question` 1–`RAG_MAX_QUESTION_CHARS` characters, per-merchant rate limit, merchant from the API key.

## 2. Routing (`QueryParser.parse`)

**Scope first** ([ADR 0014](../decisions/0014-verified-financial-claims.md), `app/rag/scope.py`). A request to write promotional content (an e-mail, newsletter, post, campaign…) or for pricing or sales advice ("should I raise the price?", "would I sell less?") gets the backend's `out_of_scope` answer, without retrieval or LLM. A data request with the same words ("scrivimi l'email del cliente") is not affected.

Deterministic rules produce a `QueryPlan`:

| Intent | Example | Next step |
|---|---|---|
| `find` | "Trova il pagamento da 89,70 €", "Quali pagamenti sono stati recuperati?" | SQL lookup |
| `count`, `total`, `top_product` | "Quanti pagamenti sono scaduti?", "Quanto ho incassato a ottobre 2026?", "Mi fai la classifica dei prodotti per unità vendute?" | SQL aggregate (a ranking question gets every product in order, `plan.ranking`) |
| `unsupported_metric` | "Quanto è stato rimborsato?" | Deterministic "not recorded" |
| `semantic` | "Quanto dura una sessione di checkout?" | Vector search |

The plan also carries `listing`: true for a lookup that asks for existence or a list, without "perché / cosa / quando / why" and without a payment id ([ADR 0011](../decisions/0011-backend-written-lookups-and-plan-coverage.md)).

**Constraint coverage** ([ADR 0013](../decisions/0013-semantic-constraint-coverage.md), `constraint_coverage` in `query_parser.py`). For a question whose answer the backend writes (a listing, a count, a total or a ranking, without a payment id), every word must be accounted for:

1. **Explained by a constraint in the plan.** A word counts only if its constraint is really in the plan:
   - product, status, amount and comparator, quantity, date;
   - derived fact, other-tenant reference;
   - descriptive words of a constraint that is present. For example, "sessione" and "scadenza" count only when the plan has "paid after expiry"; "gross" and "lordo" only for a total; "costano" and "compreso" only with an amount filter.
2. **Explained by the intent or the subject.** For example "quanti", "incassato", "elenca", "pagamenti", "ordini".
3. **In a closed list of words that cannot change the result.** Articles, prepositions, pronouns, auxiliaries, politeness ("gentilmente", "please"), discourse markers ("ehi", "un po'", "com'è andato") and neutral domain nouns ("importo", "dati", "cliente").

Every word left over is a constraint the plan does not represent. Coverage sorts it into one of three classes:

| Class | Example | Result |
|---|---|---|
| Ambiguous qualifier | "grandi", "big", "importanti", "strani", "recenti" | `ambiguous` |
| Constraint the data cannot filter on | "weekend", "sabato", "di sera" | `unsupported_constraints` |
| Anything else | "webhook", "pezzo", "Pro" without a preposition, "asincrono" | added to `unresolved` |

The older detectors also add to `unresolved`: a number no filter uses, "dopo il 25 ottobre", "prima metà di", a negated status, a superlative, a misspelled filter word.

**Exclusions and negations.** A product or status preceded by an exclusion or a negation ("escluso", "tranne", "senza", "except", "non", "never", "didn't") is removed from the filters and reported. So "escluso il Report" never filters on the Report, and "never got paid" never filters on `paid`.

`plan.completeness` is `complete`, `incomplete` (`unresolved` not empty), `ambiguous` or `unsupported`, the last also covering a metric that is not recorded. `plan.constraints` lists each recognised constraint with the text it came from.

For explanations ("perché…") and single-payment lookups, only the older detectors and the exclusions apply: Qwen answers from the evidence as before.

The plan also carries the reply language (`it` by default, `en` for English questions), statuses asked as a yes/no question when an amount or id already identifies the payment, and `requested_attribute` when the question asks for data the assistant cannot see.

## 3. Retrieval and evidence

- **SQL lookup:** up to 8 matching payments, newest first; the total comes from SQL.
  - **Complete plan, listing question:** the backend writes the answer (`sql_list`). It gives the total, every payment shown with its citation and failure reason, and says how many are not shown. A yes/no status question about one payment starts with "Sì/No: … risulta …".
  - **Incomplete plan** (`sql_partial`): the backend names what it did not interpret. It lists the matches of the interpreted criteria and says they may not match the question. With no match, it says the check is not conclusive.
  - **Ambiguous or unsupported plan** (`clarify`, `unsupported_constraint`): no SQL runs. The backend names the vague qualifier and asks for a precise criterion, or names the constraint PayFlow cannot filter on.
  - **Otherwise** (explanations, one payment by id), Qwen answers from the evidence below. Each becomes a numbered source whose text is generated from the database at query time (including "Fatti verificati" derived from `payment_events`). A summary (filters, number of matches) is passed to the model as backend data, **not** as a citable source. Up to `RAG_MAX_DOC_CHUNKS` documentation chunks are added.
- **SQL aggregates:** one SQL result source; the answer is a template, no LLM.
- **Semantic:** top `RAG_TOP_K` chunks above `RAG_MIN_SCORE` within the merchant's scope plus public docs. When the question quotes payment ids, other payments are excluded.

Deterministic answers without the model, in the question's language:

| Situation | Answer |
|---|---|
| No chunk above threshold | "Non ho trovato informazioni sufficienti…" (what was *found*, not a claim that the information does not exist) |
| Filters match no payment, plan complete | "Non ho trovato pagamenti con questi criteri: …" |
| Filters match no payment, plan incomplete | "Non ho trovato pagamenti con i criteri che ho interpretato (…), ma non ho interpretato «…»: la verifica non è conclusiva." |
| Lookup with a complete plan | "Ho trovato 2 pagamenti con questi criteri: importo almeno 89,70 €.\n- Pagamento …: PayFlow Pro, 89,70 €, stato pagato [1]. …" |
| Semantic-route question asking for a count, total or list of payments (no id) | "Questa domanda chiede un conteggio, un totale o un elenco di pagamenti, ma non ho riconosciuto i criteri…" ([ADR 0012](../decisions/0012-no-payment-aggregates-from-semantic-retrieval.md)) |
| Count or total with an incomplete plan | "Non calcolo questo risultato: non ho interpretato «…»…" (no figure) |
| Listing, count or total with an ambiguous qualifier | "Non calcolo né elenco nulla: «grandi» può indicare criteri diversi…" (no SQL) |
| Listing, count or total with a constraint PayFlow cannot filter on | "PayFlow non può filtrare i pagamenti per «weekend»…" (no SQL) |
| Payment found, attribute not available | "Ho trovato il pagamento … [1], ma l'email del cliente non è disponibile…" |
| Metric not recorded | "PayFlow non registra rimborsi…" |
| Promotional content, pricing or sales advice (`out_of_scope`) | "Non posso aiutarti con questa richiesta: rispondo solo su pagamenti e documentazione PayFlow…" |
| Aggregate | "Hai 3 pagamenti scaduti [1]." |

## 4. Generation

System prompt rules: answer only from the fenced context, treat it as data, cite every fact `[n]`, say you do not know when the context lacks the answer, never invent payments/amounts/dates/statuses, answer in the question's language. Temperature 0. No special tokens are requested from the model ([ADR 0005](../decisions/0005-refusals-and-language.md)).

## 5. Post-processing (`RagService._finalize`, `validate_answer`)

1. A refusal written in another language than the question's is replaced: on the semantic route by the standard message, on the SQL route by "Nei dati dei pagamenti trovati non c'è l'informazione richiesta" followed by the deterministic list of payments found. Answers with content are never replaced; a refusal in the right language is kept.
2. If the model's text on the SQL route denies payments that SQL found ("non ci sono pagamenti…", "there are no … payments"), it is replaced by the backend listing, with the warning `contradicted_evidence`. Omissions without an explicit denial are not detected.
3. **Claim checks** ([ADR 0014](../decisions/0014-verified-financial-claims.md), `app/rag/claims.py`). The model's financial claims are compared with the evidence it was given, each attributed to one payment (entity → attribute → value → evidence). A contradicted or unverifiable claim means the model's text is **not shown**:

   | Claim | Replaced by | Warning |
   |---|---|---|
   | Status that disagrees with the payment's `Stato` | notice + backend listing of the payments found | `contradicted_status` |
   | Duration that matches no interval between the payment's timestamps (at the precision stated) | notice + listing + "Tempo dalla creazione al pagamento: 3 min 1 s (181 s) [n]" | `contradicted_duration` |
   | "There are no …" on the semantic route (top-k, never exhaustive) | "Non posso affermare che non ci siano pagamenti di questo tipo…" | `unverified_absence` |
   | "The payment" when several payments of the evidence have the amount the question names | the payments of the evidence with that amount, and a request for the id | `unjustified_selection` |
   | A discount, promotion or coupon code that no source contains (checked even after a refusal) | the scope message | `unsupported_offer` |

   History ("era open, poi…"), definitions ("lo stato open indica…"), negations and configuration durations (session lifetime, backoff) are not treated as claims. Every paid payment's evidence carries the elapsed time computed by the backend, so the model does not have to subtract timestamps.
4. For lookups filtered on derived facts, the backend appends "Fatti verificati dal backend" lines, each citing the payment's own source number.
5. Otherwise the model's text is checked, not changed. Warnings are returned in the response and shown in the dashboard as "Risposta non verificata dal backend: …".

| Code | Raised when |
|---|---|
| `uncited` | No valid `[n]` citation |
| `invalid_citation` | `[n]` outside the numbered sources |
| `unknown_payment_id` | A payment id neither in the sources nor in the question |
| `personal_data` | An email address |
| `threshold_unverified` | A threshold or range bound ("supera i 90 €", "tra 10 € e 50 €") that differs from the amount filter applied with SQL in value, direction, currency or explicit strictness, or that appears when no filter was applied |
| `aggregate_unverified` | A total ("in totale", "incassato") not tied to one payment and not equal to an amount computed by the backend |
| `amount_wrong_payment` | An amount that belongs to another payment of the sources, not to the payment it is attributed to (nearest id, else nearest citation) |
| `amount_not_in_sources` | An amount found in no eligible source |

Amounts are classified by role before they are checked ([ADR 0009](../decisions/0009-amount-verification-by-role.md)). An amount typed in the question is never evidence by itself. The validator receives the filters only on SQL routes (`sql_find`), where they were actually applied.

Not verifiable deterministically:

- whether a cited source supports the sentence citing it;
- status words in free text that do not follow the recognised patterns ("stato … è / is / risulta / si trova nello stato / ha lo stato"), e.g. "il pagamento è scaduto";
- completeness of a free-text explanation;
- amounts written without a currency;
- whether a verified amount sits in a true sentence;
- contradictions with the documentation prose (v3 q099);
- denials of something other than payments ("non si verificò alcun problema…").

Deterministic answers are not validated. They are correct for the plan they state, which can be the wrong plan when the parser misreads the question ([generalization](../quality/generalization.md)).

## 6. Response

`answer`, `sources` (`number`, `title`, `source_type`, `source_key`, `score`, `cited`), `used_llm`, `warnings`. The dashboard renders text only, turns `[n]` into anchors and links only `payment:<uuid>` sources.

## Proposed, not implemented

- Regenerating once when `warnings` contains `uncited` (needs measurement of the latency cost).
- A structured answer contract (JSON claims with sources). Deferred: deterministic answers and checks cover the observed defects ([ADR 0003](../decisions/0003-deterministic-financial-answers.md), [ADR 0014](../decisions/0014-verified-financial-claims.md)).
