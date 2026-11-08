# Clocked endpoints used by invoice-gate

Docs read with WebFetch on Sat 7 Nov 2026, 01:29 to 01:46 UTC, and the Decide page again on Sun 8 Nov 2026. Field names come from each page's field table and its recorded JSON example; quotes are under 15 words. The real run of Sun 8 Nov 2026 (42 calls, `fixtures/real_*.json`) settled the shapes the docs did not show: each "Real run" row below is read from those files.

- Robots (clocked.rezolve.ai/robots.txt): `Allow: /`, `Disallow: /api/`. The docs pages are allowed.
- Terms: no Terms of Service or Acceptable Use URL is published. The rules page says entries "must not violate Clocked's terms of service or acceptable use" and links none. Terms text: UNVERIFIED.
- Rules page (clocked.rezolve.ai/build-challenge/rules, published 19 Oct 2026, no changes listed when read 8 Nov): "AI-assisted development is welcome."; deadline November 10, 2026, 11:59 p.m. Eastern Time; prizes paid through Wise; group awards score breadth at 10 points per endpoint used meaningfully (30 max).

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
| Real run: status and cost | all 42 calls HTTP 200; 172 credits in all (`X-Clocked-Credits`); `X-Clocked-Cost-Basis` was `reference` on every call, `X-Clocked-Cost-Usd` summed to 0.034; per endpoint in the table below | https://clocked.rezolve.ai/docs/responses |

| Endpoint (step) | Calls | Credits | Per call | Model ms |
|---|---|---|---|---|
| `/injection` (Guard) | 6 | 16 | 2.7 | 295; https://clocked.rezolve.ai/docs/limits |
| `/pii` (Protect) | 6 | 40 | 6.7 | 921; https://clocked.rezolve.ai/docs/limits |
| `/extract` invoice | 5 | 18 | 3.6 | 375; https://clocked.rezolve.ai/docs/limits |
| `/extract` PO | 5 | 20 | 4.0 | 450; https://clocked.rezolve.ai/docs/limits |
| `/fill` | 5 | 24 | 4.8 | 535; https://clocked.rezolve.ai/docs/limits |
| `/compare` | 5 | 23 | 4.6 | 555; https://clocked.rezolve.ai/docs/limits |
| `/grounding` (Verify) | 5 | 26 | 5.2 | 598; https://clocked.rezolve.ai/docs/limits |
| `/decide` | 5 | 5 | 1.0 | 121; https://clocked.rezolve.ai/docs/limits |

A full invoice costs 30 to 34 credits (8 calls); the injected one 13 (it stops after Guard and Protect). ESTIMATE from these 6: the 4,000 credits of a free account cover about 120 invoices.

## Guard: POST /injection (step `injection`)

| Item | Detail | Doc |
|---|---|---|
| Request | `text` (string, up to 60,000 characters, required unless `pdf`), `pdf`, `return_pdf` (highlight, box, redact), `source` (`user` default, or `content` for documents and emails), `instructions` (up to 20,000), `categories` (2 to 255 labels, include a benign one), `threshold` (0 to 1, default 0.5), `evidence` (boolean, default true) | https://clocked.rezolve.ai/docs/injection |
| We send | `text`, `source: "content"`, `evidence: true`, `threshold: 0.5` | https://clocked.rezolve.ai/docs/injection |
| Response | `model`, `injection` (0 to 1), `verdict`, `threshold`, `category`, `categories` {label: p}, `severity`, `severities` {none, low, medium, high, critical}, `severity_expected`, `evidence[]` {quote, start, end, confidence}, `source`, `instructions_used`, `timing` {read_ms, total_ms}; `pdf` only for PDF input | https://clocked.rezolve.ai/docs/injection |
| Default categories | benign, instruction override, role-play or persona jailbreak, system prompt or secret extraction, obfuscated or encoded payload, tool misuse or data exfiltration, social engineering | https://clocked.rezolve.ai/docs/injection |
| Note | a detector, not an enforcement boundary; a missing evidence passage does not prove a text is safe | https://clocked.rezolve.ai/docs/injection |
| Real run: clean texts | `verdict` is `benign` (5 of 5), `injection` 0.002 to 0.003, an unrounded float | https://clocked.rezolve.ai/docs/injection |
| Real run: flagged text | 03: `injection` 0.996, category instruction override, severity critical, 2 evidence passages; each evidence item adds `without` (the probability with that passage taken out: 0.389 and 0.003), and the body adds `residual` (0.003, every passage out) and `residual_verdict` (`benign`) | https://clocked.rezolve.ai/docs/injection |
| Real run: severity_expected | counts none as 1 and critical as 5 (1.01 on clean texts, 4.929 on 03) | https://clocked.rezolve.ai/docs/injection |

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
| Real run: entities | a first sighting has `source: "model"` and `mentions` (how often the value occurs); each repeat is its own entity with `source: "mention"`, `form: "exact"` and `variant_of` (the first value), and no `mentions` | https://clocked.rezolve.ai/docs/pii |
| Real run: extra blocks | top level `mentions` {candidates, variants, reads, verified, occurrences {read, kept}, rejected, spans, ms} and `checks` {trimmed, split, rejected} | https://clocked.rezolve.ai/docs/pii |
| Real run: findings | in all 6 samples it found every name, address, email, phone, birth date, SSN-style and account number the samples hold, and nothing else (the first stubs had left out 5 street addresses) | https://clocked.rezolve.ai/docs/pii |

The gate feeds every found value into its log mask; patterns for emails and long numbers back it up.

## Extract: POST /extract (steps `extract_invoice`, `extract_po`)

| Item | Detail | Doc |
|---|---|---|
| Request | `text` (up to 200,000), `pdf`, `pages`, `pdf_mode`, `return_text`, `fields` (1 to 64; a string is the description, or a field object), `instructions` (up to 20,000), `return_pdf` | https://clocked.rezolve.ai/docs/extract |
| Field object | `description`, `list`, `max_items` (table says 1 to 256, the errors note refuses above 32), `format` (text, integer, number, date, email, phone, url, code), `normalize`, `fields` (1 to 16 columns), `row_anchor` | https://clocked.rezolve.ai/docs/extract |
| We send | invoice: vendor, invoice_number, po_number, invoice_date, due_date, total, currency, remit_email (`format: "email"`), bank_account; PO: po_number, vendor, total, currency, terms, account_on_file | https://clocked.rezolve.ai/docs/extract |
| Response | `model`, `fields.{name}` {value (the verbatim span), start, end (exclusive, Unicode characters), p_start, p_close, p_point, p_verify, confidence}; a missing value is `null` with `p_blank` and no start or end; list fields add `items[]`; `timing` {read_ms, total_ms, server_ms} | https://clocked.rezolve.ai/docs/extract |
| Real run: invoice | fields carry p_start, p_close, p_point, p_verify, confidence, and often `alternatives[]` {value, start, p_verify}; `timing` adds `forwards` {fields} and `stages.fields` {doc, point, resolve, extend, contest, lists, verify} | https://clocked.rezolve.ai/docs/extract |
| Real run: PO | fields carry value, start, end, confidence and p_verify only; `stages.fields` is {reader, doc, branches, generate, locate, verify, assemble}: the shorter PO went through another pipeline | https://clocked.rezolve.ai/docs/extract |
| Real run: not read | `value: null`, `confidence`, `p_blank`, `rejected_value`, `rejected_p_verify`, `reason: "verification_rejected"`; no span | https://clocked.rezolve.ai/docs/extract |
| Real run: misreads | 5 of 75 field reads differed from the sample text, all on invoices: 01 invoice date read as "28" (the day of 2026-10-28), 01 and 02 due date not read, 06 due date "28", 06 total not read (rejected "128.00" from 1,128.00); all 30 PO reads right; every span matched its text | https://clocked.rezolve.ai/docs/extract |
| Normalized value key | where a `normalize` result lands is not shown; the gate sends no `normalize` and parses the span itself | UNVERIFIED |

## Fill: POST /fill (step `fill`)

| Item | Detail | Doc |
|---|---|---|
| Request | `text`, `pdf`, `pages`, `pdf_mode`, `return_text`, `fields` (1 to 64 typed fields), `instructions` (up to 20,000), `reference_date` (YYYY-MM-DD), `return_pdf` | https://clocked.rezolve.ai/docs/fill |
| Field types | `extract`, `choice` (options 2 to 255, allow_none), `multiselect` (options 2 to 64, threshold 0.5), `score` (levels 2 to 10), `yes_no` (description), `generate` (format text, date or number; max_tokens 1 to 400, default 40) | https://clocked.rezolve.ai/docs/fill |
| We send | payment_terms (`choice` of 6), asks_new_account (`yes_no`), urgency_pressure (`yes_no`), approver_note (`generate`, max_tokens 60) | https://clocked.rezolve.ai/docs/fill |
| Response | `model`, `fields.{name}` {value, confidence}; extract fields add start, end, p_start, p_close, p_point, p_verify; choice, multiselect, score and yes_no add `probabilities` {label: p}; score adds `expected`; generate adds `source: "generated"`; `timing` | https://clocked.rezolve.ai/docs/fill |
| Real run: yes_no | `value` is a boolean, `probabilities` are keyed `yes` and `no` | https://clocked.rezolve.ai/docs/fill |
| Real run: generate | `confidence` is present (0.99 on the notes); `timing` adds source_states, source_prefills, shared_source_tokens, shared_read_generate_forwards | https://clocked.rezolve.ai/docs/fill |
| Real run: answers | net 30 on all 5, no new account ask, no pressure; each note one plain sentence such as "This invoice is for 2,000 full colour A5 flyers" | https://clocked.rezolve.ai/docs/fill |

## Compare: POST /compare (step `compare`)

| Item | Detail | Doc |
|---|---|---|
| Request | `documents` (2 to 20: strings, `{id, text}`, or `{id, pdf, pages, date}`), `instructions`, `threshold` (default 0.5), `evidence` (all, flagged default, none), `kinds` (default true), `rotations` (1 to 8), `debug`, `return_text`, `return_pdf` | https://clocked.rezolve.ai/docs/compare |
| We send | documents `[{id: invoice}, {id: po}]`, instructions (supplier, items, quantities, prices, totals, terms), `evidence: "flagged"`, `kinds: false` | https://clocked.rezolve.ai/docs/compare |
| Response | `model`, `verdict`, `threshold`, `instructions_used`, `evidence`, `documents[]` {id, duplicates[], overlaps[], conflicts[]}, `pairs[]` {a, b, relation (same, overlapping, conflicting, unrelated), related, probabilities, duplicates[], conflicts[] with `a` and `b` passages {document, quote, start, end, confidence}}, top `duplicates`, `overlaps`, `conflicts`, `related_pairs` (indexes into pairs), `stages` (optional), `timing` | https://clocked.rezolve.ai/docs/compare |
| Limits | 2 to 20 documents; 200,000 characters and 58,000 tokens across the set | https://clocked.rezolve.ai/docs/limits |
| Real run: verdict | `clean` (01, 04), `overlaps` (05, 06), `conflicts` (02) | https://clocked.rezolve.ai/docs/compare |
| Real run: items | with `kinds: false` a conflict or duplicate item is {confidence, a, b}; the PO side passage was the whole PO; 02 flagged the bolts line (650 billed, 500 ordered) with relation conflicting at 0.992 | https://clocked.rezolve.ai/docs/compare |
| Real run: stages | {pairs_reread, pairs_ms, passage_reads, passages_ms, passages, counterparts_ms} | https://clocked.rezolve.ai/docs/compare |

## Verify: POST /grounding (step `grounding`)

| Item | Detail | Doc |
|---|---|---|
| Request | `documents` (1 to 20 strings or `{id, text}`) or `pdf`, `answer` (required, up to 20,000), `question` (adds answers_question), `claims` (1 to 30; otherwise the answer is split), `instructions`, `threshold` (default 0.5), `evidence` (all, flagged default, none), `rotations` (default 3), `return_pdf` | https://clocked.rezolve.ai/docs/grounding |
| We send | documents `[{id: po}]`, `answer` (the claims joined), `claims` (up to 5, built from the invoice facts), `evidence: "all"`, `threshold: 0.5` | https://clocked.rezolve.ai/docs/grounding |
| Response | `model`, `grounded`, `ungrounded`, `verdict`, `threshold`, `instructions_used`, `documents[]` {id, share, share_of_answer, claims[]}, `claims[]` {claim, kind, counted, weight, supported, contradicted, not_stated, not_a_claim, verdict (supported, contradicted, not_stated), sources {id: p}, source, evidence {document, quote, start, end, confidence}}, `unsupported_claims`, `not_claims`, `weakest_claim` {index, supported}, `answers_question` (only with question), `timing` | https://clocked.rezolve.ai/docs/grounding |
| Meaning | `not_stated` means the sources do not establish the claim, not that it is false; `grounded` weights support by claim length | https://clocked.rezolve.ai/docs/grounding |
| Limits | 200,000 source characters; 58,000-token admission budget; answers up to 20,000 characters | https://clocked.rezolve.ai/docs/limits |
| Real run: verdict | `partially_grounded` (01, 04, 05, 06) and `contradicted` (02); `unsupported_claims` lists the not_stated claims too; `documents[].claims` lists the supported ones | https://clocked.rezolve.ai/docs/grounding |
| Real run: bank claim | "The supplier bank account ends in NNNN." came back not_stated on all 5 although the PO line says "account on file: ending NNNN"; the gate holds only on contradicted, so this claim never held an invoice | https://clocked.rezolve.ai/docs/grounding |
| top verdict when all claims hold | not seen: no sample had every claim supported; the stub guesses `grounded` | UNVERIFIED |

## Decide: POST /decide (step `decide`)

| Item | Detail | Doc |
|---|---|---|
| Request | `state` (string, object or array; required unless every question names a document or `pdf` is sent), `pdf`, `questions` (required), `instructions` (up to 20,000), `calibration_temperature` (0.5 to 50, default 4), `rotations` (1 to 8), `documents` (1 to 64 `{id, text}`) | https://clocked.rezolve.ai/docs/decide |
| Question | `type` (noul, choice, multiselect, score), `instructions`, `criteria` (choice: label to description, 2 to 255; multiselect: a map; score: ordered levels, 2 to 10, lowest first), `options` (multiselect only), `threshold`, `document` | https://clocked.rezolve.ai/docs/decide |
| We send | `state` (masked JSON text: the invoice facts and every check result as the gate decided it), `instructions` (failed_checks lists each failed check, a null fact is not a failed check), questions `action` (choice: approve, hold, reject) and `priority` (score: low, normal, urgent) | https://clocked.rezolve.ai/docs/decide |
| Response | `model`, `answers.{name}` {type; noul: noul, decision, threshold; choice: choice, probabilities, confidence; multiselect: selected[], probabilities, threshold; score: score, legend {index: label}, probabilities {index: p}, confidence}, `usage` {input_tokens, output_tokens}; `pdf` only for PDF input | https://clocked.rezolve.ai/docs/decide |
| noul | the yes/no question type; its answer `noul` is the probability of yes, plus `decision` and `threshold` when a threshold is sent (docs read 8 Nov); the gate does not use it | https://clocked.rezolve.ai/docs/decide |
| Policy | the page leaves acting, rejecting or sending to a person to the application, and says to check calibration and thresholds on your own data | https://clocked.rezolve.ai/docs/decide |
| Evidence | Decide returns no quote or offsets; the record's evidence comes from Guard, Compare and Verify | https://clocked.rezolve.ai/docs/decide |
| Limits | document mode: 1 to 64 documents, 20,000 characters each, 400,000 total | https://clocked.rezolve.ai/docs/limits |
| Real run: confidence | the page does not define it; on all 25 recorded choice and yes_no answers (Decide and Fill) it equals (p_top minus 1/k) divided by (1 minus 1/k), k the number of options: 0 is a uniform guess, 1 is certain; the docs example (0.9933 gives 0.9899) fits too | https://clocked.rezolve.ai/docs/decide |
| Real run: score | `score` is the expected level index (sum of index times p): 0.81 on 05 between normal and urgent | https://clocked.rezolve.ai/docs/decide |
| Real run: answers | 01 hold 0.5222 against approve 0.4388 (confidence 0.28, a near tie); 02 hold 0.994; 04 approve 0.9857; 05 hold 0.9324 (confidence 0.90); 06 hold 0.9945; 1 credit each | https://clocked.rezolve.ai/docs/decide |

The gate's hard checks win: Decide can turn a held invoice into reject, or a passed one into hold or reject, only when its confidence is at least 0.5; it never approves what a check held. DECISIONS.md has the reasoning.

## Payee email: Bulk Email Verifier Actor (step `payee`, not a Clocked endpoint)

| Item | Detail | Doc |
|---|---|---|
| Endpoint | `POST https://api.apify.com/v2/actors/edgef-4~bulk-email-verifier/run-sync-get-dataset-items`; the body is the Actor input, the answer is the dataset items (HTTP 201) | https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post |
| Query | `timeout` (seconds) and `maxTotalChargeUsd` (cap on the run's total charge); the gate sends timeout=120, maxTotalChargeUsd=0.01 | https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post |
| Time limit | the run must finish in 300 s, else HTTP 408 | https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post |
| Auth | `Authorization: Bearer <Apify API token>`, recommended over the token query parameter | https://docs.apify.com/api/v2#authentication |
| Input | `emails` (array), `checkSmtp` (default false), `timeoutSeconds` (2 to 60, default 10), `maxEmails` (1 to 100,000); the gate sends one address with checkSmtp false | https://apify.com/edgef-4/bulk-email-verifier |
| Row | email, normalized, domain, status (valid, invalid, risky, unknown), reasons[], mxHost, isDisposable, isRole, isFreeProvider, didYouMean, smtpResult, smtpDetail: the 12 fields of the real run of 6 Nov 2026 on build 0.1.2 | https://apify.com/edgef-4/bulk-email-verifier |
| No mail server | reason `domain_accepts_no_mail`: "a null MX, or no MX and no address record" | https://apify.com/edgef-4/bulk-email-verifier |
| Price | Store line "from $0.60 / 1,000" verified addresses; runs in our own account bill platform usage only | https://apify.com/edgef-4/bulk-email-verifier |
| example.net | publishes a null MX (`0 .`), so a real run gives the same row as sample 6's recorded row | https://dns.google/resolve?name=example.net&type=MX |
| 8 Nov run | the payee rows stayed the recorded ones (no verifier run) | UNVERIFIED |

The gate holds on `invalid`, `unknown`, or `risky` with disposable_domain, possible_typo or non_ascii_local_part; a role address (billing@) alone is normal for remittance and passes, and Decide reads it as passed.

## Still UNVERIFIED (4)

1. Clocked terms of service text (no URL published).
2. Where Extract puts a `normalize` result.
3. Verify's top `verdict` when every claim holds.
4. A real verifier row for samples 01 to 05 (their `.example` domains never resolve, so a real run holds them by design).

Settled by the 8 Nov real run: Guard's verdict on a clean text, Protect's mention entities, Fill's yes_no labels and generate confidence, Compare's verdict values and conflict items, the credits per call, and (docs, 8 Nov) Decide's `noul`.
