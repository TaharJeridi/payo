# ADR 0014: claim checks on saved Qwen answers

Runs: 59. Model answers checked: 1769.

| Run | absence unverifiable | contradicted_duration | contradicted_status | duration contradicted | duration verified | model answers | model refusals (offers checked only) | offer unverifiable | selection unverifiable | status contradicted | status verified | unjustified_selection | unsupported_offer | unverified_absence | verdict contradicted | verdict unchecked | verdict unverifiable | verdict verified |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| aggregations | 0 | 0 | 0 | 0 | 0 | 33 | 3 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 32 | 0 | 1 |
| blind-v3-1 | 1 | 1 | 1 | 1 | 0 | 65 | 12 | 0 | 1 | 1 | 5 | 1 | 0 | 1 | 2 | 58 | 2 | 3 |
| blind-v3-2 | 2 | 1 | 1 | 1 | 0 | 65 | 9 | 0 | 1 | 1 | 6 | 1 | 0 | 2 | 2 | 56 | 3 | 4 |
| blind-v3-3 | 2 | 0 | 1 | 0 | 2 | 65 | 9 | 0 | 1 | 1 | 5 | 1 | 0 | 2 | 1 | 56 | 3 | 5 |
| blind-v3-s1 | 2 | 1 | 1 | 1 | 0 | 65 | 11 | 2 | 1 | 1 | 5 | 1 | 1 | 2 | 2 | 56 | 4 | 3 |
| blind-v3-s2 | 2 | 1 | 1 | 1 | 0 | 65 | 8 | 0 | 1 | 1 | 6 | 1 | 0 | 2 | 2 | 56 | 3 | 4 |
| blind-v3-s3 | 2 | 0 | 1 | 0 | 1 | 65 | 10 | 0 | 1 | 1 | 5 | 1 | 0 | 2 | 1 | 57 | 3 | 4 |
| consistency-1 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-10 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-11 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-12 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-2 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-3 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-4 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-5 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-6 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-7 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-8 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| consistency-9 | 0 | 0 | 0 | 0 | 0 | 15 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 14 | 0 | 1 |
| corrected-1 | 0 | 0 | 0 | 0 | 0 | 32 | 1 | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 26 | 0 | 6 |
| corrected-2 | 0 | 0 | 0 | 0 | 0 | 32 | 1 | 0 | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 25 | 0 | 7 |
| corrected-3 | 0 | 0 | 0 | 0 | 0 | 32 | 1 | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 26 | 0 | 6 |
| final-2 | 0 | 0 | 0 | 0 | 0 | 33 | 3 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 29 | 0 | 4 |
| final-3 | 0 | 0 | 0 | 0 | 0 | 33 | 3 | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 29 | 0 | 4 |
| final | 0 | 0 | 0 | 0 | 0 | 33 | 3 | 0 | 0 | 0 | 10 | 0 | 0 | 0 | 0 | 28 | 0 | 5 |
| gen-1 | 1 | 0 | 0 | 0 | 1 | 33 | 6 | 0 | 0 | 0 | 3 | 0 | 0 | 1 | 0 | 28 | 1 | 4 |
| gen-2 | 1 | 0 | 0 | 0 | 1 | 33 | 7 | 0 | 0 | 0 | 2 | 0 | 0 | 1 | 0 | 29 | 1 | 3 |
| gen-3 | 1 | 0 | 0 | 0 | 0 | 33 | 7 | 0 | 0 | 0 | 2 | 0 | 0 | 1 | 0 | 30 | 1 | 2 |
| gen-p1 | 0 | 0 | 0 | 0 | 0 | 26 | 6 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 24 | 0 | 2 |
| gen-p2 | 0 | 0 | 0 | 0 | 0 | 26 | 5 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 25 | 0 | 1 |
| gen-p3 | 0 | 0 | 0 | 0 | 0 | 26 | 4 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 25 | 0 | 1 |
| gen-q1 | 0 | 0 | 0 | 0 | 0 | 25 | 3 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 24 | 0 | 1 |
| gen-q2 | 0 | 0 | 0 | 0 | 0 | 25 | 3 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 24 | 0 | 1 |
| gen-q3 | 0 | 0 | 0 | 0 | 0 | 25 | 3 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 24 | 0 | 1 |
| gen-s1 | 0 | 0 | 0 | 0 | 0 | 25 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 25 | 0 | 0 |
| gen-s2 | 0 | 0 | 0 | 0 | 1 | 25 | 4 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 23 | 0 | 2 |
| gen-s3 | 0 | 0 | 0 | 0 | 0 | 25 | 3 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 24 | 0 | 1 |
| gen2-1 | 0 | 0 | 0 | 0 | 0 | 34 | 6 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 33 | 0 | 1 |
| gen2-2 | 0 | 0 | 0 | 0 | 0 | 34 | 6 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 32 | 0 | 2 |
| gen2-3 | 0 | 0 | 0 | 0 | 0 | 34 | 4 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 31 | 0 | 3 |
| gen2-p1 | 0 | 0 | 0 | 0 | 0 | 24 | 4 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 22 | 0 | 2 |
| gen2-p2 | 0 | 0 | 0 | 0 | 1 | 24 | 4 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 21 | 0 | 3 |
| gen2-p3 | 0 | 0 | 0 | 0 | 1 | 24 | 5 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 21 | 0 | 3 |
| gen2-q1 | 0 | 0 | 0 | 0 | 0 | 23 | 3 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 21 | 0 | 2 |
| gen2-q2 | 0 | 0 | 0 | 0 | 0 | 23 | 3 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 21 | 0 | 2 |
| gen2-q3 | 1 | 0 | 0 | 0 | 0 | 23 | 3 | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 21 | 1 | 1 |
| gen2-s1 | 1 | 0 | 0 | 0 | 0 | 23 | 3 | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 21 | 1 | 1 |
| gen2-s2 | 0 | 0 | 0 | 0 | 0 | 23 | 4 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 21 | 0 | 2 |
| gen2-s3 | 1 | 0 | 0 | 0 | 0 | 23 | 3 | 0 | 0 | 0 | 3 | 0 | 0 | 1 | 0 | 20 | 1 | 2 |
| hardening-1 | 0 | 0 | 0 | 0 | 0 | 32 | 1 | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 27 | 0 | 5 |
| hardening-2 | 0 | 0 | 0 | 0 | 0 | 32 | 1 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 28 | 0 | 4 |
| hardening-3 | 0 | 0 | 0 | 0 | 0 | 32 | 1 | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 26 | 0 | 6 |
| sql-first | 0 | 0 | 0 | 0 | 0 | 41 | 5 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 38 | 0 | 3 |
| targeted-1 | 0 | 0 | 0 | 0 | 0 | 33 | 3 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 32 | 0 | 1 |
| targeted-2 | 0 | 0 | 0 | 0 | 0 | 33 | 3 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 32 | 0 | 1 |
| targeted-3 | 0 | 0 | 0 | 0 | 0 | 33 | 3 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 31 | 0 | 2 |
| validated-1 | 0 | 0 | 0 | 0 | 0 | 32 | 1 | 0 | 0 | 0 | 12 | 0 | 0 | 0 | 0 | 25 | 0 | 7 |
| validated-2 | 0 | 0 | 0 | 0 | 0 | 32 | 1 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 28 | 0 | 4 |
| validated-3 | 0 | 0 | 0 | 0 | 0 | 32 | 1 | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 27 | 0 | 5 |

## Answers that would be blocked

- `g-neg-report-failed-late` **unverified_absence** (1 runs: gen-2)
  - Basandomi sulle informazioni fornite, non ci sono pagamenti di Financial Report Pack che hanno fallito dopo il 20 ottobre 2026. Il solo pagamento che ha avuto un stato "failed" è del 9 ottobre 2026 [1]. || absence: stated None, evidence None
- `g-neg-report-failed-late` **unverified_absence** (2 runs: gen-1, gen-3)
  - Non ci sono pagamenti falliti del prodotto "Financial Report Pack" registrati dopo il 20 ottobre 2026 nel contesto fornito. Il solo pagamento fallito presente è avvenuto il 9 ottobre 2026 [1]. || absence: stated None, evidence None
- `v2-typo-rejected` **unverified_absence** (1 runs: gen2-s3)
  - Il pagamento con ID 7d25ddea-d6f7-5014-b252-49f1f9ac246c è stato rifiutato per il motivo "checkout_session_not_created" [3]. Non ci sono altri pagamenti rifiutati elencati nel contesto fornito. || absence: stated None, evidence None
- `v2-typo-rejected` **unverified_absence** (2 runs: gen2-q3, gen2-s1)
  - Il pagamento con ID 7d25ddea-d6f7-5014-b252-49f1f9ac246c è stato rifiutato per il motivo `checkout_session_not_created`. Altri dettagli del pagamento includono: - Prodotto: PayFlow Starter (payflow-starter) - Importo: 9.90 EUR - Stato: failed - fallito - Creat || absence: stated None, evidence None
- `v3-q013` **contradicted_status** (1 runs: blind-v3-s1)
  - Sì, la pagina di checkout su Stripe esiste per questo pagamento [1]. Il suo stato è "open", il che significa che "Il Checkout Session exists and can be paid" [2] [3]. || status: stated 'open', evidence 'expired'
- `v3-q013` **contradicted_status** (1 runs: blind-v3-3)
  - Sì, la pagina di checkout su Stripe esiste per questo pagamento [1]. Questo si può dedurre dal fatto che il pagamento ha un stato `open`, il che significa che "Il Checkout Session exists and can be paid" [2] [3]. || status: stated 'open', evidence 'expired'
- `v3-q013` **contradicted_status** (4 runs: blind-v3-1, blind-v3-2, blind-v3-s2, blind-v3-s3)
  - Sì, la pagina di checkout su Stripe esiste per questo pagamento [1][2]. Il pagamento si trova nel stato `open`, il che significa che "Il Checkout Session exists and can be paid" [2] [1]. || status: stated 'open', evidence 'expired'
- `v3-q055` **unverified_absence** (3 runs: blind-v3-1, blind-v3-2, blind-v3-3)
  - Based on the provided context, there is no Pro payment recorded for an exact amount of 59.80 EUR. The closest amounts mentioned are 29.90 EUR [1], [2], [3] and 89.70 EUR [3], which is double the quantity of one product. || absence: stated None, evidence None
- `v3-q055` **unverified_absence** (2 runs: blind-v3-s2, blind-v3-s3)
  - Based on the provided context, there is no Pro payment recorded for an exact amount of 59.80 EUR. The closest amounts mentioned are 29.90 EUR [1], [2], [3], and [4], and 89.70 EUR [3]. || absence: stated None, evidence None
- `v3-q055` **unverified_absence** (1 runs: blind-v3-s1)
  - Based on the provided context, there is no Pro payment recorded for an exact amount of 59.80 EUR. The closest amounts mentioned are 29.90 EUR [1], [2], and 89.70 EUR [3]. || absence: stated None, evidence None
- `v3-q140` **unsupported_offer** (1 runs: blind-v3-s1)
  - I do not know how to write a marketing email as the provided context does not contain any specific details about your customer base, their preferences or the benefits of the Pro plan. However, based on the payment information given, here is a generic example:  || offer: stated 'code PROUPGRADE20', evidence None; offer: stated '20% off', evidence None
- `v3-q149` **unjustified_selection** (2 runs: blind-v3-s1, blind-v3-s2)
  - The payment for 29.90 EUR, identified by the product "PayFlow Pro (payflow-pro)" and quantity 1, resulted in an expired session as indicated by the status [2]. The reason given was that the Stripe Checkout Session expired without a payment being made [2][3]. || selection: stated '2990 cents, 2 payments', evidence None
- `v3-q149` **unjustified_selection** (4 runs: blind-v3-1, blind-v3-2, blind-v3-3, blind-v3-s3)
  - The payment for 29.90 EUR, identified by the product "PayFlow Pro (payflow-pro)" and quantity 1, resulted in an expired session as indicated by the status [2]. The reason given was that the Stripe Checkout Session expired without a payment being made [2][4]. || selection: stated '2990 cents, 2 payments', evidence None
- `v3-r015` **unverified_absence** (4 runs: blind-v3-2, blind-v3-3, blind-v3-s2, blind-v3-s3)
  - Non si registrano pagamenti che hanno avuto problemi a parlare con Stripe al momento della creazione. Tutti i pagamenti elencati nella traccia di stato sono stati creati correttamente senza errori specificati, anche se alcuni hanno avuto problemi successivi du || absence: stated None, evidence None
- `v3-r015` **unverified_absence** (1 runs: blind-v3-s1)
  - Non si registrano pagamenti che hanno avuto problemi a parlare con Stripe nel momento della creazione. Tutti i pagamenti elencati nella traccia di stato sono stati creati senza errori specificati, anche se alcuni hanno avuto problemi successivi durante la rico || absence: stated None, evidence None
- `v3-r018` **contradicted_duration** (1 runs: blind-v3-s1)
  - Il pagamento con id 4343f610-5f37-53d9-9691-a6a60855d8f7 è stato pagato alle 16:33:01 UTC del 2026-10-08 [1]. Dalla creazione, che avvenne alle 16:30:00 UTC dello stesso giorno, il pagamento è stato completato in 3 minuti e 4 secondi [1]. || duration: stated '3 minuti e 4 secondi', evidence '3 min 1 s (181 s)'
- `v3-r018` **contradicted_duration** (1 runs: blind-v3-s2)
  - Il pagamento con id 4343f610-5f37-53d9-9691-a6a60855d8f7 è stato pagato alle 16:33:01 UTC del 2026-10-08 [1]. Dalla creazione, che avvenne alle 16:30:00 UTC dello stesso giorno, il pagamento è stato completato in 3 minuti e 5 secondi [1]. || duration: stated '3 minuti e 5 secondi', evidence '3 min 1 s (181 s)'
- `v3-r018` **contradicted_duration** (1 runs: blind-v3-1)
  - Il pagamento con id 4343f610-5f37-53d9-9691-a6a60855d8f7 è stato pagato alle 16:33:01 UTC del 2026-10-08 [1]. Dalla creazione, che avvenne alle 16:30:00 UTC dello stesso giorno, il pagamento è stato completato in 31 secondi [1]. || duration: stated '31 secondi', evidence '3 min 1 s (181 s)'
- `v3-r018` **contradicted_duration** (1 runs: blind-v3-2)
  - Il pagamento con l'id 4343f610-5f37-53d9-9691-a6a60855d8f7 è stato pagato alle 16:33:01 UTC del 2026-10-08 [1]. Dalla creazione, che avvenne alle 16:30:00 UTC dello stesso giorno, il pagamento è stato completato in 3 minuti e 4 secondi [1]. || duration: stated '3 minuti e 4 secondi', evidence '3 min 1 s (181 s)'
