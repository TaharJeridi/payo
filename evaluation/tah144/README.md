# TAH-144: legitimate questions without an answer (blind v3)

The first v3 measurement found 21 answerable questions that got no figure (no wrong figure either). This folder records what changed in the parser, how it was verified without models, and what is still open. No dataset, golden answer or saved run was changed. From now on these questions are **regression** cases, not evidence of generalization.

## Rules added (`backend/app/rag/query_parser.py`, `language.py`)

| Rule | Example | Cases |
|---|---|---|
| A number with exactly two decimals and no currency is an amount; not a time ("alle 16.30"), a date, a percentage, or a number with one or no decimals | "pagamenti da 9,90", "the 29.90 payment" | q040, q055, q149; also v1 `g-col-8970-noeuro`, v2 `v2-col-5980-ended` |
| An article before the bounds of a range | "tra i 20 e i 50 euro" | r002, r012 |
| "in qualsiasi stato" / "any status" is an explicit absence of status filter, never next to a named status | "…in qualsiasi stato" | r012 |
| A count or a list of payments "collected" ("incassato", "collect", "guadagnato", "earn") counts or lists paid payments; never on one identified payment, never over a named status | "quanti pagamenti … ho incassato?" | r002 (it also fixes a status-free count the parser used to give for such questions) |
| A misspelt neutral word of 9+ letters (similarity ≥ 0,9) is neutral; misspelt or unknown constraint words stay unresolved | "effetivamente" | r002 |
| The first N days or weeks of a month, capped at the month's end | "prime due settimane di ottobre", "primi 10 giorni" | r009, r010 |
| A day range with the month on both sides, also across the new year | "between October 5 and October 10" | q038 |
| From mid-month: from the 16th, the first day of the second half (as "seconda metà") | "since mid-October", "da metà ottobre in poi" | r017 |
| Parts of a relative month | "seconda metà del mese scorso" | q076 |
| "quanti pagati?", "how many failed?", "number of open checkouts" are counts; a duration ("quanti minuti…") is not; "quante volte" still is | | q058, q059 |
| "N units / pezzi" is a quantity; "più di un pezzo", "almeno 2 pezzi" are not (a bound the filters cannot represent) | "bought 5 units" | q036 |
| "quantity" is the English word, not a misspelling of "quantità" | "with quantity 1" | q080 |
| "orders", "purchase", "checkout(s)" are payment nouns, as "ordini" already was | | q036, q059, r017 |
| Which product sells or earns most reaches the SQL ranking (units, plus the revenue leader when different) | "Which product brought in the most money?", "il prodotto che vendeva più pezzi" | q087, r003 |
| One product named in a best-seller question is the claim being checked, not a filter; the full ranking is shown | "Il Pro è il prodotto che vende di più, giusto?" | q091 |
| "è passato o no?" asks whether a payment went through; "è passato un mese" stays a period; "mese passato" is last month | | v1 `g-col-8970-noeuro` |
| Short English questions with no other marker ("has", "any", "since", "orders"…) are answered in English | | q036, r017; q117 and q142 replies now in English |

The TAH-142 principle is kept: a word the plan cannot represent keeps the answer partial, with no figure.

## Verification without models

- **Tests:** `backend/tests/test_unanswered_questions.py`, 79 tests, positive and negative, with service-level checks on the seed (lists, counts and totals equal to the seed, no payment of the other merchant). Two earlier tests were updated because the behaviour changed on purpose: the single-payment question "ehi, quel pagamento da 89,70 è passato o no?" now reaches the SQL lookup instead of the semantic route (`test_evidence_consistency.py`), and "how many Pro orders…" is now a partial count instead of an unrecognised noun; the unrecognised-noun test uses "charges" (`test_constraint_coverage.py`). The TAH-143 claim test on the semantic route now builds its retrieval explicitly, because q055 no longer reaches that route (`test_verified_claims.py`).
- **Mutation testing** (`evaluation/mutation/parser_tah144.json`, `language_tah144.json`): **39/39 mutants killed** (37 parser, 2 language). The first rounds left 8 survivors; each needed a missing test (count over a named status, identified payment, new-year range, period after "è passato", misspelling tolerance, "has"). One mutant was dropped as equivalent: applying the misspelling tolerance to neutral words shorter than 9 letters — at similarity 0,9 no plausible constraint word of that length is affected.
- **Full suite:** 1,035 backend tests pass, 3 skipped (live Ollama/Qdrant, Stripe test mode, saved-runs path); ruff, ruff format and mypy clean.
- **Deterministic regression, four datasets** (`tests.eval_deterministic`, 419 questions, routes and backend answers before and after): 25 questions change, all intended and read one by one; every other route and backend answer is byte-identical. No backend answer contains a payment of the other merchant (the 6 that contain one of its ids quote that id from the question and answer "no match", as before).

## The 21 cases, and the other v3 non-answers

| Case | Before | After | Reading |
|---|---|---|---|
| q058 "quanti pagati?" | unrecognized | `sql_aggregate` "Hai 10 pagamenti pagati" | correct (10) |
| q059 "number of open checkouts" | unrecognized | `sql_aggregate` 3 awaiting payment | correct (3) |
| q036 "bought 5 units" | semantic | `sql_list` Starter ×5, 49,50 €, paid | correct |
| q040 "pagamenti da 9,90" | unrecognized | `sql_list` 4 payments | correct (the 4 ids of the golden reading) |
| r017 "since mid-October … Pro … paid" | unrecognized | `sql_list` 677506ad | correct |
| q080 "paid orders with quantity 1" | partial | `sql_aggregate` 207,50 € from 7 | correct |
| r002 "tra i 20 e i 50 euro … incassato" | partial | `sql_aggregate` 6 paid | correct (6) |
| r012 "tra i 40 e i 60 euro, in qualsiasi stato" | partial | `sql_list` 7 payments | correct (the 7 expected) |
| r009 "prime due settimane di ottobre", Pro | partial | `sql_aggregate` 149,50 € | correct |
| q076 "seconda metà del mese scorso" | semantic | `sql_aggregate` 79,40 € from 2 | correct |
| q087 "product … most money" | semantic | `sql_aggregate` Starter by units; Report first by revenue, 196,00 € | correct (the revenue leader is stated) |
| r003 "prima metà di ottobre … più pezzi" | semantic | `sql_aggregate` Pro, 5 units | correct |
| q091 "Il Pro è il prodotto che vende di più, giusto?" | semantic | `sql_aggregate` Starter 7 units, full ranking | correct (does not confirm Pro) |
| q055 "Pro payment for exactly 59.80" | semantic | `sql_list` 4838cd98 | correct; also removes the TAH-143 false absence at its root |
| q038, r010, q149 | partial / partial / semantic | `sql_find`: Qwen writes from SQL evidence that holds exactly the matching payments | **real run needed** |
| r004 "da metà mese in poi" | semantic | partial, no figure ("metà", "mese") | which month is not said: honest partial; still unanswered |
| q035 "più di un pezzo" | unrecognized | unchanged | a quantity bound is not representable in the filters |
| q042 "pagina di checkout mai creata" | unrecognized | unchanged | needs a filter on "no Stripe session"; not implemented |
| q063 "quante volte … più di un pezzo" | partial | partial | same as q035 |
| q052 "Starter scaduti per checkout_session_expired" | partial | unchanged | a failure-reason filter does not exist |
| q145 "quanto ho venduto a ottobre?" | semantic | unchanged | money or units is ambiguous by design (`clarify_or_interpret`) |
| q108, q034 (documentation) | semantic | unchanged | retrieval with bge-m3: real run needed |

## Still open (TAH-144 cannot close yet)

1. **Real runs** on the Mac for the questions that now reach Qwen with SQL evidence (q038, r010, q149, `g-col-8970-noeuro`) and the documentation cases (q108, q034): commands in [`evaluation/tah143/README.md` §6](../tah143/README.md#6-real-evaluation-mac-pending); the code fingerprint there changes with this work.
2. **Not implemented:** quantity bounds (q035, q063), "no checkout page created" (q042), failure-reason filter (q052), the month of "da metà mese" (r004).
3. **Generalization:** the issue requires measuring on a new blind set. These questions informed the rules, so v3 no longer measures generalization for them.
