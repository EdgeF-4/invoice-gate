# Clocked endpoints used by invoice-gate

Read with WebFetch on Sat 7 Nov 2026, 01:29 to 01:46 UTC (the lane shell cannot reach the host). Field names come from each page's field table and its recorded JSON example; quotes are under 15 words. Nothing here was called: no account, no key.

- Robots (clocked.rezolve.ai/robots.txt): `Allow: /`, `Disallow: /api/`. The docs pages are allowed.
- Terms: no Terms of Service or Acceptable Use URL is published. The rules page says entries "must not violate Clocked's terms of service or acceptable use" and links none; /terms serves the landing page; /security and /status link none. Terms text: UNVERIFIED.
- Rules page (clocked.rezolve.ai/build-challenge/rules): "AI-assisted development is welcome."; deadline "November 10, 2026, 11:59 p.m. Eastern Time."; prizes "paid through Wise."; group awards score breadth at 10 points per endpoint used meaningfully (30 max). Page says "none since publication on October 19, 2026."

## Common to every call

| Item | Detail | Doc |
|---|---|---|
| Base URL | `https://clocked.rezolve.ai/clocked`; "All ten endpoints use `POST` with JSON." | https://clocked.rezolve.ai/docs |
| Auth header | `Authorization: Bearer <account API key>` (the docs placeholder is YOUR_CLOCKED_API_KEY); keys are created and revoked in the account dashboard; send from a trusted backend | https://clocked.rezolve.ai/docs/authentication |
| Key handling | "Never log the Authorization header." If a key is exposed, revoke it | https://clocked.rezolve.ai/docs/authentication |
| Idempotency | `Idempotency-Key: <new UUID per logical request>`; reuse the same key with the identical payload only to retry an unknown outcome | https://clocked.rezolve.ai/docs/authentication |
| Content type | `Content-Type: application/json` | https://clocked.rezolve.ai/docs/authentication |
| Body limit | 3.5 MB JSON body, including any base64 PDF or image | https://clocked.rezolve.ai/docs/limits |
| Concurrency | 1 (Trial), 4 (Build), 8 (Production), 16 (Scale); a 429 means wait, never add parallelism | https://clocked.rezolve.ai/docs/limits |
| Credits | model time: 25 ms per credit, at least 1 credit per request; a pipeline is one debit; token counts are not debits | https://clocked.rezolve.ai/docs/limits |
| Cost headers | `X-Clocked-Credits`, `X-Clocked-Cost-Usd`, `X-Clocked-Cost-Basis` (prepaid, overage, mixed, reference), `X-Clocked-Model-Ms`, `X-Clocked-Transport` (`replay` when served from the ledger), `Server-Timing` | https://clocked.rezolve.ai/docs/responses |
| Success body | the bare result object, not wrapped | https://clocked.rezolve.ai/docs/responses |
| Error body | `error` (string, wording not stable), `retryAsNew` (boolean: stop automatic retries when true) | https://clocked.rezolve.ai/docs/errors |
| Status codes | 400 or 422 fix the request; 401 or 403 key or access; 402 credits; 409 conflict (keep the key, check retryAsNew); 413 body too large; 429 retry with bounded backoff; 5xx retry within a budget | https://clocked.rezolve.ai/docs/errors |
| Timeouts | gateway 110 s per model request; Python SDK client 130 s; `Retry-After` is not always sent | https://clocked.rezolve.ai/docs/errors |
| Request id | none documented; a replay is matched by its Idempotency-Key | https://clocked.rezolve.ai/docs/responses |
| Retention | maintenance clears response bodies older than 24 hours, "not a hard 24-hour deletion guarantee" | https://clocked.rezolve.ai/security |
| model field | string such as `clocked-1.0`; it does not pin a deployment | https://clocked.rezolve.ai/docs/injection |
| Credits for our 42 calls | not stated per endpoint; read from X-Clocked-Credits on the first real run | UNVERIFIED |

## Guard: POST /injection (step `injection`)

| Item | Detail | Doc |
|---|---|---|
| Request | `text` (string, up to 60,000 characters, required unless `pdf`), `pdf`, `return_pdf` (highlight, box, redact), `source` (`user` default, or `content` for documents and emails), `instructions` (up to 20,000), `categories` (2 to 255 labels, include a benign one), `threshold` (0 to 1, default 0.5), `evidence` (boolean, default true) | https://clocked.rezolve.ai/docs/injection |
| We send | `text`, `source: "content"`, `evidence: true`, `threshold: 0.5` | https://clocked.rezolve.ai/docs/injection |
| Response | `model`, `injection` (0 to 1), `verdict`, `threshold`, `category`, `categories` {label: p}, `severity`, `severities` {none, low, medium, high, critical}, `severity_expected`, `evidence[]` {quote, start, end, confidence}, `source`, `instructions_used`, `timing` {read_ms, total_ms}; `pdf` only for PDF input | https://clocked.rezolve.ai/docs/injection |
| Default categories | benign, instruction override, role-play or persona jailbreak, system prompt or secret extraction, obfuscated or encoded payload, tool misuse or data exfiltration, social engineering | https://clocked.rezolve.ai/docs/injection |
| Note | a detector, not an enforcement boundary; a missing evidence passage does not prove a text is safe | https://clocked.rezolve.ai/docs/injection |
| verdict on a clean text | only `injection` is shown; the stub uses `benign`; the gate decides on injection >= threshold, never on this string | UNVERIFIED |

The gate holds, and stops before any later AI step, when `injection >= threshold`.

## Protect: POST /pii (step `pii`)

| Item | Detail | Doc |
|---|---|---|
| Request | `text` (up to 60,000), `pdf`, `pages`, `pdf_mode`, `return_text`, `entities` (list of 1 to 32 catalog kinds, or an object with descriptions; default nine kinds), `instructions`, `threshold` (default 0.5), `max_items` (1 to 256), `allow`, `deny`, `anonymize` (replace, mask, hash, redact), `mask_keep` (0 to 32), `return_pdf` | https://clocked.rezolve.ai/docs/pii |
| Kinds | default nine: person, email, phone, address, date_of_birth, ssn, credit_card, bank_account, ip_address; more: passport, drivers_license, medical, username, organization, location | https://clocked.rezolve.ai/docs/pii |
| We send | `text`, `entities`: person, email, phone, address, date_of_birth, ssn, bank_account, credit_card; `anonymize: "replace"`; `threshold: 0.5` | https://clocked.rezolve.ai/docs/pii |
| Response | `model`, `entities[]` {type, value, start, end (exclusive), confidence, source}, `counts` {kind: n}, `anonymized_text` (with anonymize), `threshold`, `instructions_used`, `timing` | https://clocked.rezolve.ai/docs/pii |
| Placeholders | `replace` gives typed uppercase placeholders such as `<PERSON>`, `<EMAIL>` | https://clocked.rezolve.ai/docs/pii |
| Notes | offsets refer to the original text; `hash` is an unsalted truncated SHA-256; detection can miss data, keep a review step | https://clocked.rezolve.ai/docs/pii |
| entities[].source values | only `model` is shown | UNVERIFIED |

The gate feeds every found value into its log mask; patterns for emails and long numbers back it up.

## Extract: POST /extract (steps `extract_invoice`, `extract_po`)

| Item | Detail | Doc |
|---|---|---|
| Request | `text` (up to 200,000), `pdf`, `pages`, `pdf_mode`, `return_text`, `fields` (1 to 64; a string is the description, or a field object), `instructions` (up to 20,000), `return_pdf` | https://clocked.rezolve.ai/docs/extract |
| Field object | `description`, `list`, `max_items` (table says 1 to 256, the errors note refuses above 32), `format` (text, integer, number, date, email, phone, url, code), `normalize`, `fields` (1 to 16 columns), `row_anchor` | https://clocked.rezolve.ai/docs/extract |
| We send | invoice: vendor, invoice_number, po_number, invoice_date, due_date, total, currency, remit_email (`format: "email"`), bank_account; PO: po_number, vendor, total, currency, terms, account_on_file | https://clocked.rezolve.ai/docs/extract |
| Response | `model`, `fields.{name}` {value (the verbatim span), start, end (exclusive, Unicode characters), p_start, p_close, p_point, p_verify, confidence}; a missing value is `null` with `p_blank` and no start or end; list fields add `items[]`; `timing` {read_ms, total_ms, server_ms} | https://clocked.rezolve.ai/docs/extract |
| Normalized value key | where a `normalize` result lands is not shown; the gate sends no `normalize` and parses the span itself | UNVERIFIED |

## Fill: POST /fill (step `fill`)

| Item | Detail | Doc |
|---|---|---|
| Request | `text`, `pdf`, `pages`, `pdf_mode`, `return_text`, `fields` (1 to 64 typed fields), `instructions` (up to 20,000), `reference_date` (YYYY-MM-DD), `return_pdf` | https://clocked.rezolve.ai/docs/fill |
| Field types | `extract`, `choice` (options 2 to 255, allow_none), `multiselect` (options 2 to 64, threshold 0.5), `score` (levels 2 to 10), `yes_no` (description), `generate` (format text, date or number; max_tokens 1 to 400, default 40) | https://clocked.rezolve.ai/docs/fill |
| We send | payment_terms (`choice` of 6), asks_new_account (`yes_no`), urgency_pressure (`yes_no`), approver_note (`generate`, max_tokens 60) | https://clocked.rezolve.ai/docs/fill |
| Response | `model`, `fields.{name}` {value, confidence}; extract fields add start, end, p_start, p_close, p_point, p_verify; choice, multiselect, score and yes_no add `probabilities` {label: p}; score adds `expected`; generate adds `source: "generated"`; `timing` | https://clocked.rezolve.ai/docs/fill |
| yes_no probability labels | not shown in the example; the gate reads `value` only | UNVERIFIED |
| confidence on generate fields | not shown; the stub leaves it out | UNVERIFIED |

## Compare: POST /compare (step `compare`)

| Item | Detail | Doc |
|---|---|---|
| Request | `documents` (2 to 20: strings, `{id, text}`, or `{id, pdf, pages, date}`), `instructions`, `threshold` (default 0.5), `evidence` (all, flagged default, none), `kinds` (default true), `rotations` (1 to 8), `debug`, `return_text`, `return_pdf` | https://clocked.rezolve.ai/docs/compare |
| We send | documents `[{id: invoice}, {id: po}]`, instructions (supplier, items, quantities, prices, totals, terms), `evidence: "flagged"`, `kinds: false` | https://clocked.rezolve.ai/docs/compare |
| Response | `model`, `verdict`, `threshold`, `instructions_used`, `evidence`, `documents[]` {id, duplicates[], overlaps[], conflicts[]}, `pairs[]` {a, b, relation (same, overlapping, conflicting, unrelated), related, probabilities, duplicates[], conflicts[] with `a` and `b` passages {document, quote, start, end, confidence}}, top `duplicates`, `overlaps`, `conflicts`, `related_pairs` (indexes into pairs), `stages` (optional), `timing` | https://clocked.rezolve.ai/docs/compare |
| Limits | 2 to 20 documents; 200,000 characters and 58,000 tokens across the set | https://clocked.rezolve.ai/docs/limits |
| verdict values | only `duplicates` is shown; the stub's `overlaps` and `conflicts` are guesses; the gate reads `pairs[0].relation` and `pairs[0].conflicts` | UNVERIFIED |
| conflict items with kinds false | whether kind, kinds and confidence drop out; the stub gives a and b only | UNVERIFIED |

## Verify: POST /grounding (step `grounding`)

| Item | Detail | Doc |
|---|---|---|
| Request | `documents` (1 to 20 strings or `{id, text}`) or `pdf`, `answer` (required, up to 20,000), `question` (adds answers_question), `claims` (1 to 30; otherwise the answer is split), `instructions`, `threshold` (default 0.5), `evidence` (all, flagged default, none), `rotations` (default 3), `return_pdf` | https://clocked.rezolve.ai/docs/grounding |
| We send | documents `[{id: po}]`, `answer` (the claims joined), `claims` (up to 5, built from the invoice facts), `evidence: "all"`, `threshold: 0.5` | https://clocked.rezolve.ai/docs/grounding |
| Response | `model`, `grounded`, `ungrounded`, `verdict`, `threshold`, `instructions_used`, `documents[]` {id, share, share_of_answer, claims[]}, `claims[]` {claim, kind, counted, weight, supported, contradicted, not_stated, not_a_claim, verdict (supported, contradicted, not_stated), sources {id: p}, source, evidence {document, quote, start, end, confidence}}, `unsupported_claims`, `not_claims`, `weakest_claim` {index, supported}, `answers_question` (only with question), `timing` | https://clocked.rezolve.ai/docs/grounding |
| Meaning | `not_stated` means the sources do not establish the claim, not that it is false; `grounded` weights support by claim length | https://clocked.rezolve.ai/docs/grounding |
| Limits | 200,000 source characters; 58,000-token admission budget; answers up to 20,000 characters | https://clocked.rezolve.ai/docs/limits |
| top verdict when all claims hold | only `contradicted` is shown; the stub uses `supported` | UNVERIFIED |

## Decide: POST /decide (step `decide`)

| Item | Detail | Doc |
|---|---|---|
| Request | `state` (string, object or array; required unless every question names a document or `pdf` is sent), `pdf`, `questions` (required), `instructions` (up to 20,000), `calibration_temperature` (0.5 to 50, default 4), `rotations` (1 to 8), `documents` (1 to 64 `{id, text}`) | https://clocked.rezolve.ai/docs/decide |
| Question | `type` (noul, choice, multiselect, score), `instructions`, `criteria` (choice: label to description, 2 to 255; multiselect: a map; score: ordered levels, 2 to 10, lowest first), `options` (multiselect only), `threshold`, `document` | https://clocked.rezolve.ai/docs/decide |
| We send | `state` (masked JSON text of every check), `instructions`, questions `action` (choice: approve, hold, reject) and `priority` (score: low, normal, urgent) | https://clocked.rezolve.ai/docs/decide |
| Response | `model`, `answers.{name}` {type; noul: noul, decision, threshold; choice: choice, probabilities, confidence; multiselect: selected[], probabilities, threshold; score: score, legend {index: label}, probabilities {index: p}, confidence}, `usage` {input_tokens, output_tokens}; `pdf` only for PDF input | https://clocked.rezolve.ai/docs/decide |
| Evidence | Decide returns no quote or offsets; the record's evidence comes from Guard, Compare and Verify | https://clocked.rezolve.ai/docs/decide |
| Limits | document mode: 1 to 64 documents, 20,000 characters each, 400,000 total | https://clocked.rezolve.ai/docs/limits |
| noul | a probability question type; its name is not explained beyond its fields; the gate does not use it | UNVERIFIED |

The gate's hard checks win: Decide can turn a held invoice into reject, never into approve.

## Payee email: Bulk Email Verifier Actor (step `payee`, not a Clocked endpoint)

| Item | Detail | Doc |
|---|---|---|
| Endpoint | `POST https://api.apify.com/v2/actors/edgef-4~bulk-email-verifier/run-sync-get-dataset-items`; the body is the Actor input, the answer is the dataset items (HTTP 201) | https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post |
| Query | `timeout` (seconds) and `maxTotalChargeUsd` (cap on the run's total charge); the gate sends timeout=120, maxTotalChargeUsd=0.01 | https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post |
| Time limit | the run must finish in 300 s, else HTTP 408 | https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post |
| Auth | `Authorization: Bearer <Apify API token>`, recommended over the token query parameter | https://docs.apify.com/api/v2#authentication |
| Input | `emails` (array), `checkSmtp` (default false), `timeoutSeconds` (2 to 60, default 10), `maxEmails` (1 to 100,000); the gate sends one address with checkSmtp false | https://apify.com/edgef-4/bulk-email-verifier |
| Row | email, normalized, domain, status (valid, invalid, risky, unknown), reasons[], mxHost, isDisposable, isRole, isFreeProvider, didYouMean, smtpResult, smtpDetail: the 12 fields of the real run of 6 Nov 2026 on build 0.1.2 (lab file 2026-11-06/n8n_template/evidence/real_run_1106.json) | https://apify.com/edgef-4/bulk-email-verifier |
| No mail server | reason `domain_accepts_no_mail`: "a null MX, or no MX and no address record" | https://apify.com/edgef-4/bulk-email-verifier |
| Price | Store line "from $0.60 / 1,000" verified addresses; runs in our own account bill platform usage only (0.00031 USD for the 6 Nov 3-address run, run record) | https://apify.com/edgef-4/bulk-email-verifier |
| example.net | publishes a null MX (`0 .`), so a real run gives the same row as sample 6's recorded row | https://dns.google/resolve?name=example.net&type=MX |

The gate holds on `invalid`, `unknown`, or `risky` with disposable_domain, possible_typo or non_ascii_local_part; a role address (billing@) alone is normal for remittance and passes.

## Still UNVERIFIED (8)

1. Clocked terms of service text (no URL published).
2. Guard `verdict` string for a clean text.
3. Protect `entities[].source` values other than `model`.
4. Where Extract puts a `normalize` result.
5. Fill `yes_no` probability labels and generate-field confidence.
6. Compare top-level `verdict` values other than `duplicates`, and conflict items when `kinds` is false.
7. Verify top-level `verdict` when every claim holds.
8. Credits per call for our requests; Decide `noul` meaning (unused).

The first real run (HQ_RUN.md) settles 2 to 8; `python3 invoice_gate.py --diff` prints every key path that differs.
