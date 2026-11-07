"""invoice-gate: AI-safe invoice intake on the Clocked API.

One run takes an invoice and its purchase order through Guard (/injection), Protect (/pii),
Extract (/extract), Fill (/fill), Compare (/compare), Verify (/grounding) and Decide (/decide),
checks the remittance email with the Bulk Email Verifier Actor, and returns a record plus
approve, hold or reject. Hard checks only make a decision stricter: the model never approves
an invoice that a check held, and an invoice that carries an instruction aimed at the AI
stops after Guard and Protect, so no later model step reads it.

Transports are swappable: StubTransport answers from fixtures/, HttpsTransport calls the
real APIs with a key read from the environment (never from a file, never logged or saved).
"""
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES, FIXTURES = HERE / "samples", HERE / "fixtures"
CLOCKED_BASE = "https://clocked.rezolve.ai/clocked"
VERIFIER_URL = ("https://api.apify.com/v2/actors/edgef-4~bulk-email-verifier/run-sync-get-dataset-items"
                "?timeout=120&maxTotalChargeUsd=0.01")
CLOCKED_KEY_ENV, APIFY_KEY_ENV = "CLOCKED_API_KEY", "APIFY_TOKEN"

INVOICE_FIELDS = {
    "vendor": "Company or person that issued the invoice",
    "invoice_number": "Invoice number",
    "po_number": "Purchase order number the invoice refers to",
    "invoice_date": "Invoice date",
    "due_date": "Payment due date",
    "total": "Total amount due, the number only, without the currency code",
    "currency": "Three letter currency code of the total due",
    "remit_email": {"description": "Email address for remittance advice", "format": "email"},
    "bank_account": "Bank account number the invoice asks to be paid to",
}
PO_FIELDS = {
    "po_number": "Purchase order number",
    "vendor": "Supplier the order was placed with",
    "total": "Order total, the number only, without the currency code",
    "currency": "Three letter currency code of the order total",
    "terms": "Payment terms",
    "account_on_file": "Last digits of the supplier bank account on file",
}
TERMS = ["net 15", "net 30", "net 45", "net 60", "due on receipt", "not stated"]
FILL_FIELDS = {
    "payment_terms": {"type": "choice", "description": "Payment terms stated on the invoice", "options": TERMS},
    "asks_new_account": {"type": "yes_no",
                         "description": "The invoice asks to pay a new or different bank account than usual"},
    "urgency_pressure": {"type": "yes_no",
                         "description": "The invoice pushes for urgent, secret or unusual payment"},
    "approver_note": {"type": "generate", "format": "text", "max_tokens": 60,
                      "description": "One plain sentence for the approver saying what the invoice is for"},
}
PII_KINDS = ["person", "email", "phone", "address", "date_of_birth", "ssn", "bank_account", "credit_card"]
DECIDE_RULES = ("You review supplier invoices for accounts payable. Approve only when every check in the "
                "state passed. Hold when any check failed or is missing. Reject only when the document is "
                "not a bill for this buyer.")
DECIDE_QUESTIONS = {
    "action": {"type": "choice", "instructions": "What should accounts payable do with this invoice now?",
               "criteria": {"approve": "It matches its purchase order and every check passed",
                            "hold": "A check failed or a person must look before paying",
                            "reject": "It is not a valid bill for this buyer"}},
    "priority": {"type": "score", "instructions": "How soon should a person act on this invoice?",
                 "criteria": ["low", "normal", "urgent"]},
}
REVIEW_REASONS = {"disposable_domain", "possible_typo", "non_ascii_local_part"}

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
NUMBERS = [re.compile(p) for p in (r"\+\d[\d ().-]{7,}\d", r"\b\d{3}-\d{2}-\d{4}\b",
                                   r"\b\d{4}(?:[ -]\d{4}){2,}\b", r"\b\d{7,}\b")]


class MaskedLog:
    """Log lines, each masked before it is kept or printed. The mask uses the values Protect
    found plus patterns for emails and long numbers, so a finding Protect missed still does
    not put an address or an account number in the log."""

    def __init__(self, echo=False):
        self.lines, self.values, self.echo = [], set(), echo

    def learn(self, values):
        self.values.update(v for v in values if isinstance(v, str) and len(v) >= 3)

    def mask(self, text):
        for value in sorted(self.values, key=len, reverse=True):
            text = text.replace(value, "<MASKED>")
        text = EMAIL.sub("<EMAIL>", text)
        for pattern in NUMBERS:
            text = pattern.sub("<NUMBER>", text)
        return text

    def __call__(self, line):
        line = self.mask(line)
        self.lines.append(line)
        if self.echo:
            print(line)


class ApiError(Exception):
    def __init__(self, step, status, body):
        super().__init__(f"{step}: HTTP {status}: {str(body)[:300]}")
        self.step, self.status, self.body = step, status, body


class Client:
    """One API base URL plus a swappable transport: transport(step, url, body) returns parsed JSON."""

    def __init__(self, transport, base):
        self.transport, self.base = transport, base

    def post(self, step, path, body):
        return self.transport(step, self.base + path, body)


class StubTransport:
    """Answers from fixtures/<prefix><sample>.json and keeps every (step, body) for the tests."""

    def __init__(self, sample, prefix=""):
        self.file = FIXTURES / f"{prefix}{sample}.json"
        self.steps = json.loads(self.file.read_text())["steps"]
        self.calls = []

    def __call__(self, step, url, body):
        self.calls.append((step, body))
        if step not in self.steps:
            raise KeyError(f"{self.file.name} has no recorded response for step {step}")
        record = self.steps[step]
        if record["status"] >= 400:
            raise ApiError(step, record["status"], record["body"])
        return record["body"]


class HttpsTransport:
    """Real HTTPS POST with a bearer key from the environment. With idempotency on (Clocked),
    one Idempotency-Key per logical request is reused on each retry of it, as the docs ask.
    Retries only 429, 5xx and unknown network outcomes, at most `retries` times, and stops
    when the body says retryAsNew. Each response (status, cost headers, body) lands in
    self.saved for fixtures/real_<sample>.json; request headers are never kept."""
    KEEP_HEADERS = ("x-clocked-", "server-timing", "retry-after")

    def __init__(self, key_env, idempotency=True, retries=3, timeout=130, sleep=time.sleep):
        self.key_env, self.idempotency, self.retries = key_env, idempotency, retries
        self.timeout, self.sleep, self.saved = timeout, sleep, {}

    def __call__(self, step, url, body):
        key = os.environ.get(self.key_env)
        if not key:
            raise SystemExit(f"{self.key_env} is not set in the environment")
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        if self.idempotency:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        data = json.dumps(body).encode()
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    status, head, raw = response.status, response.headers, response.read()
            except urllib.error.HTTPError as err:
                status, head, raw = err.code, err.headers, err.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == self.retries:
                    raise
                self.sleep(2 ** attempt)  # unknown outcome: same key, identical payload
                continue
            try:
                payload = json.loads(raw.decode() or "null")
            except ValueError:
                payload = {"error": raw.decode(errors="replace")[:500]}
            stop = isinstance(payload, dict) and payload.get("retryAsNew")
            if (status == 429 or status >= 500) and attempt < self.retries and not stop:
                wait = head.get("Retry-After") if head else None
                self.sleep(min(float(wait), 30.0) if wait and wait.isdigit() else 2 ** attempt)
                continue
            kept = {k: v for k, v in (head.items() if head else []) if k.lower().startswith(self.KEEP_HEADERS)}
            self.saved[step] = {"note": "REAL response", "status": status, "headers": kept, "body": payload}
            if status >= 400:
                raise ApiError(step, status, payload)
            return payload


def val(fields, name):
    return (fields.get(name) or {}).get("value")


def money(text):
    # ponytail: plain "1,234.56" style only; add a locale rule when a comma-decimal invoice shows up
    try:
        return Decimal(re.sub(r"[^\d.]", "", str(text or ""))).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_date(text):
    # ponytail: ISO dates only (the samples); Fill's reference_date can resolve other formats later
    try:
        return date.fromisoformat(str(text).strip())
    except ValueError:
        return None


def flat(text):
    return " ".join(str(text).split())


def guard(clocked, text, log):
    """Guard (POST /injection): an instruction aimed at the AI inside the invoice?"""
    r = clocked.post("injection", "/injection",
                     {"text": text, "source": "content", "evidence": True, "threshold": 0.5})
    threshold = r.get("threshold", 0.5)
    evidence = r.get("evidence") or []
    log(f"guard: injection {r['injection']:.3f} (threshold {threshold}), verdict {r.get('verdict')}, "
        f"category {r.get('category')}, severity {r.get('severity')}"
        + "".join(f"; evidence '{flat(e['quote'])}'" for e in evidence))
    return {"flagged": r["injection"] >= threshold, "p": r["injection"], "category": r.get("category"),
            "severity": r.get("severity"), "evidence": evidence}


def protect(clocked, text, log):
    """Protect (POST /pii): personal data; the values feed the log mask, the replaced text is
    what a person sees."""
    r = clocked.post("pii", "/pii", {"text": text, "entities": PII_KINDS, "anonymize": "replace", "threshold": 0.5})
    counts = r.get("counts") or {}
    log("protect: " + (", ".join(f"{k} {n}" for k, n in sorted(counts.items())) or "no findings"))
    return {"values": [e["value"] for e in r.get("entities") or []], "counts": counts,
            "masked_text": r.get("anonymized_text")}


def extract(clocked, step, text, fields, log):
    """Extract (POST /extract): verbatim facts, each with its span in the source text."""
    r = clocked.post(step, "/extract", {"text": text, "fields": fields})
    out = {name: {k: f.get(k) for k in ("value", "start", "end", "confidence")} for name, f in r["fields"].items()}
    log(f"{step}: " + ", ".join(f"{n} {v['value']!r}" if v["value"] is not None else f"{n} missing"
                                 for n, v in out.items()))
    return out


def fill(clocked, text, log):
    """Fill (POST /fill): judgments on the invoice: terms, a bank change ask, pressure, a note."""
    r = clocked.post("fill", "/fill", {"text": text, "fields": FILL_FIELDS})
    out = {name: (r["fields"].get(name) or {}).get("value") for name in FILL_FIELDS}
    log("fill: " + ", ".join(f"{n} {out[n]!r}" for n in FILL_FIELDS))
    return out


def compare(clocked, invoice, po, log):
    """Compare (POST /compare): invoice against its PO; each conflict comes with both passages."""
    r = clocked.post("compare", "/compare", {
        "documents": [{"id": "invoice", "text": invoice}, {"id": "po", "text": po}],
        "instructions": ("An invoice and the purchase order it bills. Flag any difference in supplier, "
                         "items, quantities, unit prices, totals or payment terms."),
        "evidence": "flagged", "kinds": False})
    pair = r["pairs"][0]
    conflicts = pair.get("conflicts") or []
    log(f"compare: relation {pair['relation']} (related {pair.get('related')}), conflicts {len(conflicts)}"
        + "".join(f"; invoice '{flat(c['a']['quote'])}' vs po '{flat(c['b']['quote'])}'"
                  for c in conflicts if "a" in c and "b" in c))
    return {"relation": pair["relation"], "conflicts": conflicts,
            "flagged": pair["relation"] == "conflicting" or bool(conflicts)}


def claims_for(inv, terms):
    """What the invoice claims about its PO; Verify checks each claim against the PO text."""
    v = {name: val(inv, name) for name in inv}
    claims = []
    if v.get("po_number"):
        claims.append(f"The purchase order number is {v['po_number']}.")
    if v.get("vendor"):
        claims.append(f"The supplier is {v['vendor'].rstrip('.')}.")
    if v.get("total"):
        claims.append(" ".join(f"The order total is {v.get('currency') or ''} {v['total']}.".split()))
    if terms and terms != "not stated":
        claims.append(f"Payment terms are {terms}.")
    digits = re.sub(r"\D", "", v.get("bank_account") or "")
    if len(digits) >= 4:
        claims.append(f"The supplier bank account ends in {digits[-4:]}.")
    return claims


def verify(clocked, po, claims, log):
    """Verify (POST /grounding): each claim against the PO; a contradicted claim holds the invoice."""
    if not claims:
        log("verify: no claims to check")
        return {"grounded": None, "contradicted": [], "not_stated": []}
    r = clocked.post("grounding", "/grounding", {"documents": [{"id": "po", "text": po}], "answer": " ".join(claims),
                                                "claims": claims, "evidence": "all", "threshold": 0.5})
    rows = r.get("claims") or []
    bad = [c for c in rows if c.get("verdict") == "contradicted"]
    unclear = [c for c in rows if c.get("verdict") == "not_stated"]
    log(f"verify: grounded {r.get('grounded')}, {len(rows) - len(bad) - len(unclear)} supported, "
        f"{len(bad)} contradicted, {len(unclear)} not stated"
        + "".join(f"; contradicted '{c['claim']}'" + (f" (po: '{flat(c['evidence']['quote'])}')"
                                                      if c.get("evidence") else "") for c in bad))
    return {"grounded": r.get("grounded"), "contradicted": bad, "not_stated": unclear}


def payee(verifier, email, log):
    """Payee email (Bulk Email Verifier Actor): hold when the remittance address cannot get mail.
    A role address (billing@, accounts@) is normal for remittance and does not hold."""
    if not email:
        log("payee: no remittance email on the invoice")
        return {"hold": False, "row": None, "why": None}
    rows = verifier.post("payee", "", {"emails": [email], "checkSmtp": False, "timeoutSeconds": 10, "maxEmails": 1})
    row = next((x for x in rows or [] if x.get("normalized") == email.lower()), (rows or [None])[0])
    if row is None:
        log("payee: the verifier returned no row")
        return {"hold": True, "row": None, "why": "payee email: the verifier returned no row"}
    status, reasons = row.get("status"), row.get("reasons") or []
    domain = row.get("domain") or email.rsplit("@", 1)[-1]
    hold = status in ("invalid", "unknown") or (status == "risky" and bool(REVIEW_REASONS & set(reasons)))
    log(f"payee: @{domain} {status} ({', '.join(reasons)})")
    return {"hold": hold, "row": row, "why": f"payee email @{domain} is {status}: {', '.join(reasons)}" if hold else None}


def decide(clocked, state, log):
    """Decide (POST /decide): approve, hold or reject with probabilities, on a masked state."""
    r = clocked.post("decide", "/decide", {"state": state, "instructions": DECIDE_RULES, "questions": DECIDE_QUESTIONS})
    action, prio = r["answers"]["action"], r["answers"].get("priority") or {}
    level = (prio.get("legend") or {}).get(str(round(prio["score"]))) if "score" in prio else None
    p = (action.get("probabilities") or {}).get(action["choice"])
    log(f"decide: {action['choice']} (p {p}), priority {level}")
    return {"choice": action["choice"], "p": p or 0.0, "probabilities": action.get("probabilities") or {},
            "priority": level}


def final(holds, d):
    """Hard holds win; the model may only make a held invoice stricter (reject), never approve it."""
    if d is None or holds:
        return "reject" if d and d["choice"] == "reject" else "hold"
    return d["choice"] if d["p"] >= 0.5 else "hold"


def run(invoice, po, clocked, verifier, log=None, today=None):
    """One invoice and its PO through every step. Returns (record, decision)."""
    log = log if log is not None else MaskedLog()
    today = today or date.today()
    early = []  # Guard runs first, but its line is logged only after Protect has taught the mask
    g = guard(clocked, invoice, early.append)
    p = protect(clocked, invoice, early.append)
    log.learn(p["values"])
    for line in early:
        log(line)
    record = {"guard": g, "pii_counts": p["counts"], "masked_invoice": p["masked_text"]}
    holds, flags, d = [], [], None
    if g["flagged"]:
        holds.append(f"guard: the invoice carries an instruction aimed at the AI ({g['category']}, "
                     f"p {g['p']:.3f}); no later AI step read it")
    else:
        inv = extract(clocked, "extract_invoice", invoice, INVOICE_FIELDS, log)
        pof = extract(clocked, "extract_po", po, PO_FIELDS, log)
        f = fill(clocked, invoice, log)
        c = compare(clocked, invoice, po, log)
        v = verify(clocked, po, claims_for(inv, f.get("payment_terms")), log)
        pay = payee(verifier, val(inv, "remit_email"), log)
        inv_total, po_total = money(val(inv, "total")), money(val(pof, "total"))
        cur, po_cur = (val(inv, "currency") or "").upper(), (val(pof, "currency") or "").upper()
        if inv_total is None or po_total is None:
            holds.append("totals: could not read both totals")
        elif inv_total != po_total or cur != po_cur:
            holds.append(f"totals: invoice {cur} {inv_total} vs PO {po_cur} {po_total}")
        if val(inv, "po_number") != val(pof, "po_number"):
            holds.append(f"po: invoice names {val(inv, 'po_number')}, the PO is {val(pof, 'po_number')}")
        if c["flagged"]:
            holds.append(f"compare: {len(c['conflicts'])} conflict(s) between invoice and PO ({c['relation']})")
        holds += [f"verify: the PO contradicts '{x['claim']}'" for x in v["contradicted"]]
        if f.get("asks_new_account") is True:
            holds.append("fill: the invoice asks to pay a new or different bank account")
        if f.get("urgency_pressure") is True:
            flags.append("pressure language")
        if pay["hold"]:
            holds.append(pay["why"])
        due = parse_date(val(inv, "due_date"))
        overdue = (today - due).days if due and due < today else 0
        if overdue:
            flags.append(f"overdue {overdue} days")
        state = log.mask(json.dumps({
            "invoice": {"number": val(inv, "invoice_number"), "po": val(inv, "po_number"), "vendor": val(inv, "vendor"),
                        "total": str(inv_total), "currency": cur, "due_date": val(inv, "due_date"),
                        "days_overdue": overdue},
            "po": {"number": val(pof, "po_number"), "total": str(po_total), "currency": po_cur,
                   "terms": val(pof, "terms")},
            "checks": {"injection_p": g["p"], "compare_relation": c["relation"], "conflicts": len(c["conflicts"]),
                       "contradicted_claims": [x["claim"] for x in v["contradicted"]],
                       "payee_email": f"{(pay['row'] or {}).get('status')} {(pay['row'] or {}).get('reasons')}",
                       "asks_new_account": f.get("asks_new_account"),
                       "urgency_pressure": f.get("urgency_pressure"), "failed_checks": holds},
        }, indent=1))
        d = decide(clocked, state, log)
        record.update(invoice=inv, po=pof, fill=f, compare=c, verify=v, payee=pay["row"], decide=d)
    decision = final(holds, d)
    if not holds and decision != "approve" and d:
        holds.append(f"decide: {d['choice']} at p {d['p']}")
    record.update(decision=decision, holds=[log.mask(h) for h in holds], flags=flags)
    log(f"decision: {decision.upper()}" + "".join(f"; {log.mask(h)}" for h in holds)
        + "".join(f"; flag {x}" for x in flags))
    return record, decision


def sample_ids():
    return sorted(p.name for p in SAMPLES.iterdir() if p.is_dir())


def load(sample):
    folder = SAMPLES / sample
    return (folder / "invoice.txt").read_text(), (folder / "po.txt").read_text()


def key_paths(node, prefix=""):
    """Every key path of a JSON value; list items fold into [] so shapes compare, not lengths."""
    paths = {prefix} if prefix else set()
    if isinstance(node, dict):
        for key, value in node.items():
            paths |= key_paths(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for item in node:
            paths |= key_paths(item, prefix + "[]")
    return paths


def diff_real():
    """Real against stub, per sample and step: status, key paths only one side has, and any
    Extract span whose text[start:end] is not its value. Prints names and paths, never values."""
    reals = sorted(FIXTURES.glob("real_*.json"))
    if not reals:
        print("no fixtures/real_*.json yet: run with --real first")
        return 1
    for real in reals:
        sample = real.stem[len("real_"):]
        stub = json.loads((FIXTURES / f"{sample}.json").read_text())["steps"]
        got = json.loads(real.read_text())["steps"]
        invoice, po = load(sample)
        for step, rec in got.items():
            a, b = key_paths((stub.get(step) or {}).get("body")), key_paths(rec["body"])
            credits = next((v for k, v in rec["headers"].items() if k.lower() == "x-clocked-credits"), None)
            print(f"{sample} {step}: HTTP {rec['status']}, credits {credits}, "
                  f"only in stub {sorted(a - b)}, only in real {sorted(b - a)}")
            if step.startswith("extract_") and isinstance(rec["body"], dict):
                text = invoice if step == "extract_invoice" else po
                for name, f in (rec["body"].get("fields") or {}).items():
                    if f.get("start") is not None and text[f["start"]:f["end"]] != f.get("value"):
                        print(f"  span mismatch: {name} {f['start']}:{f['end']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="invoice-gate: run the samples through Clocked (stub or real)")
    ap.add_argument("--sample", action="append", help="sample folder name, repeatable (default: all)")
    ap.add_argument("--real", action="store_true",
                    help=f"call Clocked for real with {CLOCKED_KEY_ENV}; saves fixtures/real_<sample>.json")
    ap.add_argument("--real-verifier", action="store_true",
                    help=f"also run the verifier Actor for real with {APIFY_KEY_ENV} (platform credit)")
    ap.add_argument("--diff", action="store_true", help="compare fixtures/real_*.json with the stubs")
    ap.add_argument("--replay", action="store_true",
                    help="offline: Clocked answers from fixtures/real_<sample>.json, payee rows from the stub")
    ap.add_argument("--today", help="run date YYYY-MM-DD (default: today)")
    args = ap.parse_args(argv)
    if args.diff:
        return diff_real()
    today = date.fromisoformat(args.today) if args.today else date.today()
    for sample in args.sample or sample_ids():
        invoice, po = load(sample)
        ct = (HttpsTransport(CLOCKED_KEY_ENV) if args.real
              else StubTransport(sample, prefix="real_") if args.replay else StubTransport(sample))
        vt = (HttpsTransport(APIFY_KEY_ENV, idempotency=False, retries=1, timeout=320)
              if args.real_verifier else StubTransport(sample))
        print(f"== {sample}")
        stopped = None
        try:
            run(invoice, po, Client(ct, CLOCKED_BASE), Client(vt, VERIFIER_URL), MaskedLog(echo=True), today)
        except (ApiError, OSError) as err:  # OSError covers URLError and timeouts after the retries
            print(f"stopped: {err.step} HTTP {err.status}" if isinstance(err, ApiError)
                  else f"stopped: network error {type(err).__name__}")
            stopped = err
        saved = {**getattr(ct, "saved", {}), **getattr(vt, "saved", {})}
        if saved:  # merge, so a verifier-only real run keeps the Clocked answers saved earlier
            out = FIXTURES / f"real_{sample}.json"
            steps = json.loads(out.read_text())["steps"] if out.exists() else {}
            steps.update(saved)
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            out.write_text(json.dumps({"_note": f"REAL responses, last saved {stamp} UTC by invoice_gate.py",
                                       "sample": sample, "steps": steps}, indent=1) + "\n")
            print(f"saved fixtures/{out.name}")
        if isinstance(stopped, ApiError) and stopped.status in (401, 402, 403):
            print("key, access or credits problem: stopping all samples")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
