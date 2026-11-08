# Decisions after the first real run (8 Nov 2026)

The first real run sent the 6 samples through Clocked (42 calls, 172 credits, answers in `fixtures/real_*.json`). Four decisions matched the plan. Two did not: Decide held 01_clean and 05_overdue, both planned as approve. For each, the question was whether the gate's rule or the sample's expected decision was wrong. The real answers were never edited.

## How to read a Decide answer

- `probabilities` cover our own criteria (approve, hold, reject) and sum to 1; `choice` is the top one.
- `confidence` is not defined on the docs page. On all 25 choice and yes/no answers the run recorded (Decide and Fill) it equals (p_top minus 1/k) divided by (1 minus 1/k), with k options: 0 is a uniform guess, 1 is certain. The docs example fits too (0.9933 gives 0.9899). `test_decide_confidence_is_the_top_probability_rescaled` checks it on every recorded answer.
- Our request sends no documents, so Decide reads only the `state` the gate builds and our instructions. Its answer says how that state reads; it adds no fact the checks did not have.
- The docs leave the policy to the application: whether to act, reject or send to a person, with thresholds checked on your own data.

## 01_clean: the gate was wrong (rule fixed, expectation kept: approve)

Evidence:

| What | Value |
|---|---|
| Sample | matches its PO in every field: supplier, PO number, both lines, total USD 1,795.00, terms net 30, account ending 3301 |
| Hard checks | all passed, `failed_checks` empty |
| Decide | hold 0.5222, approve 0.4388, reject 0.039: confidence 0.28, a near tie |
| State sent: payee | `"risky ['mx_found', 'role_account']"`: the verifier's raw status, although the gate's own rule passes a role address such as billing@ |
| State sent: Guard | `"injection_p": 0.<NUMBER>`: the probability went in unrounded and the log mask's long-number pattern ate its digits, so the state was not even valid JSON |
| State sent: due date | `null`: Extract could not read it (it rejected "21.25"), while the instructions said "Hold when any check failed or is missing" |
| Control | 04, same state shape but payee "valid" and a due date: approve 0.9857 |

Root cause: the gate described passing checks to Decide as failing or missing, then followed Decide's top choice even at a coin flip.

Fix (rule):

1. The state carries the gate's own check results: `"payee_email": "passed (mx_found, role_account)"`; probabilities are rounded to 3 places, so the mask cannot reach them; the instructions now say `failed_checks` names each failed check, an empty list means every check passed, and a fact shown as null was not read, which is not a failed check.
2. Decide changes the checks' outcome only when it is clear: confidence of at least 0.5 (with three options, a top probability of at least 2/3). Below that the checks' outcome stands and the record carries a flag, `decide unsure (hold at p 0.5222, confidence 0.28): the checks stand`. The same bar applies to turning a held invoice into reject. Decide still never approves what a check held.

Why 0.5: it is the midpoint between a uniform guess and certainty, and on the recorded answers it separates the one near tie (0.28) from every other answer (0.90 or more). Six invoices are not a calibration set: check the bar on more real invoices.

Tests: `test_decide_near_tie_leaves_a_clean_invoice_approved_and_a_clear_hold_holds` (the real 01 numbers approve, a clear hold holds, an unclear reject does not make a hold stricter, a clear one does), `test_decide_state_is_valid_json_and_carries_the_gates_own_check_results`, `test_unread_due_date_does_not_hold_a_clean_invoice_but_an_unread_total_does`. Both near-tie and overdue tests fail against the old rules.

## 05_overdue: the expectation was wrong (approve, urgent; now hold)

Evidence:

| What | Value |
|---|---|
| Sample | due 27 Sep 2026, 41 days past due on the run date (7 Nov); matches its PO |
| Hard checks | all passed, `failed_checks` empty |
| Decide | hold 0.9324, approve 0.0363, reject 0.0313: confidence 0.90, a clear answer to the criterion "A check failed or a person must look before paying" |
| Priority | expected level 0.81 (normal 0.63, urgent 0.09): normal, not the planned urgent |

Reasoning: an invoice that turns up long after its due date is often a resent copy of one already paid or in process. This gate holds no payment history, so it cannot rule that out, and paying it straight through is the duplicate payment an accounts payable control exists to stop. The plan treated overdue as a reason to pay sooner; the right call is a person checking first. Decide read the facts that way with a clear answer.

Fix (expectation, and a rule so it does not hang on the model): 05 is now expected to hold. Overdue is a hard check, so the hold no longer depends on how Decide reads a number: `overdue 41 days: a person checks it was not paid already`. Test: `test_overdue_invoice_is_held_for_a_person_even_when_decide_approves`.

## Replay of the first run's answers after both fixes

`python3 invoice_gate.py --replay --today 2026-11-07`

| Sample | Decide (first run) | Decision | Why |
|---|---|---|---|
| 01_clean | hold 0.5222, confidence 0.28 | APPROVE | every check passed; Decide unsure, flagged |
| 02_amount_mismatch | hold 0.994 | HOLD | totals 327.00 against 264.00, Compare conflict, Verify contradicted the total |
| 03_injected | (not asked) | HOLD | Guard 0.996, stopped before any later model step |
| 04_personal_data | approve 0.9857 | APPROVE | every check passed; its 7 personal values (9 mentions) stay out of the log |
| 05_overdue | hold 0.9324 | HOLD | overdue 41 days: a person checks it was not paid already |
| 06_payee_no_mail | hold 0.9945 | HOLD | remittance domain accepts no mail; total not read |

Replay answers each step with the recorded answer. The fixes changed the state and instructions sent to Decide, so the recorded Decide answers belong to the old request: a new `--real` run (about 172 credits) records answers to the new one, and `test_replay_on_the_real_fixtures_gives_the_expected_decisions` then checks them against the expected decisions.

## Second real run, on the fixed requests (8 Nov 2026, 09:46 UTC)

`python3 invoice_gate.py --real --today 2026-11-07`, then the replay. `fixtures/real_*.json` and `demo_transcript.txt` hold this run.

| Sample | Decide (second run) | Decision |
|---|---|---|
| 01_clean | approve 0.9959, confidence 0.99 | APPROVE |
| 02_amount_mismatch | hold 0.9955 | HOLD |
| 03_injected | (not asked: Guard 0.996) | HOLD |
| 04_personal_data | approve 0.9961 | APPROVE |
| 05_overdue | hold 0.9963 | HOLD |
| 06_payee_no_mail | hold 0.9957 | HOLD |

The same six decisions. With the gate's own check results in the state, Decide answered every sample clearly (confidence 0.99); the near tie on 01 (0.28) is gone. 42 calls, 145 credits (172 in the first run). `test_replay_on_the_real_fixtures_gives_the_expected_decisions` passes on these answers.

## Seen in the real run, not changed yet

- Extract: 5 of 75 field reads differed from the sample text, all invoice dates or totals (details in ENDPOINTS.md). An unread total holds the invoice (06); an unread due date means no overdue check for that invoice. Next: try `format: "date"` and `format: "number"` on those fields in a real run.
- Verify: the claim "The supplier bank account ends in NNNN." came back not_stated on all 5, because the PO says "account on file". So no check compares the invoice's bank account with the account on file today. Next: a plain last-four-digits comparison of the two extracted fields, or the claim worded as the PO puts it.
