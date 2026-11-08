"""invoice-gate tests: stub transports only. Any socket connect fails the test run."""
import email.message
import io
import json
import re
import socket
import urllib.error
from datetime import date

import pytest

import invoice_gate as gate

TODAY = date(2026, 11, 7)
EXPECT = {"01_clean": "approve", "02_amount_mismatch": "hold", "03_injected": "hold",
          "04_personal_data": "approve", "05_overdue": "hold", "06_payee_no_mail": "hold"}
# Typed from the sample files by hand, independent of the Protect fixtures.
PERSONAL = {
    "01_clean": ["Dana Whitlock", "5521 0087 3301", "billing@ferncastle-paper.example", "12 Mill Lane Business Park"],
    "02_amount_mismatch": ["Priya Okonkwo-Lund", "7304 1189 6614", "billing@duskvale-fasteners.example",
                           "Unit 4, Foundry Row"],
    "03_injected": ["Tomasz Ferreira-Holt", "4100 2563 4421", "9000 1122 7788", "billing@brindlewick-cleaning.example",
                    "8 Harbour Yard"],
    "04_personal_data": ["Marisol Quentin-Vale", "27 Larkspur Close", "+1 202 555 0143",
                         "marisol.qv@quentinvale-translate.example", "1984-03-09", "000-12-3456", "3108 4471 5562"],
    "05_overdue": ["Rowan Achterberg", "6620 9013 2207", "billing@marrowgate-print.example", "3 Ink Street"],
    "06_payee_no_mail": ["Ines Barrowclough", "2291 5530 8840", "billing@example.net", "22 Relay Road"],
}
REAL_RUN = "2026-11-07"  # the --today of the real run, so the overdue count stays 41 days
ALL_STEPS = ["injection", "pii", "extract_invoice", "extract_po", "fill", "compare", "grounding", "decide"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def go(sample, patch=None):
    """Run one sample on its stub fixture; patch(steps) may edit the recorded answers first."""
    clocked, verifier = gate.StubTransport(sample), gate.StubTransport(sample)
    if patch:
        patch(clocked.steps)
        patch(verifier.steps)
    log = gate.MaskedLog()
    invoice, po = gate.load(sample)
    record, decision = gate.run(invoice, po, gate.Client(clocked, gate.CLOCKED_BASE),
                                gate.Client(verifier, gate.VERIFIER_URL), log, TODAY)
    return record, decision, log, clocked, verifier


def test_all_six_samples_run_to_the_expected_decision():
    assert gate.sample_ids() == sorted(EXPECT)
    for sample, want in EXPECT.items():
        record, decision, log, _, _ = go(sample)
        assert decision == want, (sample, record["holds"])
        assert record["decision"] == want and log.lines[-1].startswith(f"decision: {want.upper()}")


def test_injected_invoice_is_held_before_any_later_ai_step():
    record, decision, log, clocked, verifier = go("03_injected")
    assert decision == "hold"
    assert [s for s, _ in clocked.calls] == ["injection", "pii"] and verifier.calls == []
    assert "instruction aimed at the AI" in record["holds"][0]
    assert any("ignore the purchase order" in line for line in log.lines)


def test_model_cannot_approve_what_a_check_held():
    def model_says_approve(steps):
        if "decide" in steps:
            steps["decide"]["body"]["answers"]["action"].update(
                choice="approve", probabilities={"approve": 0.99, "hold": 0.005, "reject": 0.005})
    for sample in ("02_amount_mismatch", "06_payee_no_mail"):
        _, decision, _, _, _ = go(sample, model_says_approve)
        assert decision == "hold", sample


def test_no_mail_payee_is_held_with_the_verifier_reason():
    record, decision, log, _, verifier = go("06_payee_no_mail")
    assert decision == "hold"
    assert record["holds"] == ["payee email @example.net is invalid: domain_accepts_no_mail"]
    assert record["payee"]["status"] == "invalid" and record["payee"]["mxHost"] is None
    assert [s for s, _ in verifier.calls] == ["payee"]
    assert verifier.calls[0][1] == {"emails": ["billing@example.net"], "checkSmtp": False, "timeoutSeconds": 10,
                                    "maxEmails": 1}


def test_clean_sample_is_approved_through_every_step():
    record, decision, log, clocked, verifier = go("01_clean")
    assert decision == "approve" and record["holds"] == [] and record["flags"] == []
    assert [s for s, _ in clocked.calls] == ALL_STEPS and [s for s, _ in verifier.calls] == ["payee"]
    assert record["verify"]["contradicted"] == [] and record["compare"]["relation"] == "overlapping"


def test_personal_data_is_masked_in_every_logged_line():
    for sample, values in PERSONAL.items():
        record, _, log, clocked, _ = go(sample)
        assert len(log.lines) >= 3
        for line in log.lines + record["holds"]:
            for value in values:
                assert value not in line, (sample, value)
        for step, body in clocked.calls:
            if step == "decide":  # Decide gets a masked state, not the raw invoice
                assert not any(v in body["state"] for v in values), sample


def test_mask_still_hides_emails_and_numbers_when_protect_misses():
    def protect_finds_nothing(steps):
        if "pii" in steps:
            steps["pii"]["body"].update(entities=[], counts={})
    _, _, log, _, _ = go("04_personal_data", protect_finds_nothing)
    text = "\n".join(log.lines)
    for value in ("marisol.qv@quentinvale-translate.example", "3108 4471 5562", "000-12-3456"):
        assert value not in text


def test_amount_mismatch_is_held_on_the_totals():
    record, decision, _, _, _ = go("02_amount_mismatch")
    assert decision == "hold"
    assert "totals: invoice USD 327.00 vs PO USD 264.00" in record["holds"]
    assert len(record["compare"]["conflicts"]) == 1
    assert [c["claim"] for c in record["verify"]["contradicted"]] == ["The order total is USD 327.00."]


def test_overdue_invoice_is_held_for_a_person_even_when_decide_approves():
    """Fix 2 (DECISIONS.md): 41 days past due, matching its PO. The gate has no payment history,
    so a person checks it was not paid already; the real Decide held it too (p 0.9324)."""
    def model_says_approve(steps):
        if "decide" in steps:
            steps["decide"]["body"]["answers"]["action"].update(
                choice="approve", probabilities={"approve": 0.99, "hold": 0.005, "reject": 0.005})
    for patch in (None, model_says_approve):
        record, decision, _, clocked, _ = go("05_overdue", patch)
        assert decision == "hold" and record["holds"] == ["overdue 41 days: a person checks it was not paid already"]
        state = json.loads(dict(clocked.calls)["decide"]["state"])
        assert state["invoice"]["days_overdue"] == 41 and state["checks"]["failed_checks"] == record["holds"]
    record, decision, _, _, _ = go("01_clean")  # due 27 Nov: not overdue on 7 Nov
    assert decision == "approve" and record["holds"] == []


def real_8_nov(choice, probabilities):
    def patch(steps):
        if "decide" in steps:
            steps["decide"]["body"]["answers"]["action"].update(choice=choice, probabilities=probabilities)
    return patch


def test_decide_near_tie_leaves_a_clean_invoice_approved_and_a_clear_hold_holds():
    """Fix 1 (DECISIONS.md): the real Decide answered 01 with a near tie (confidence 0.28), which
    must not hold an invoice that passed every check; a clear hold (confidence 0.90) still does."""
    record, decision, log, _, _ = go("01_clean", real_8_nov("hold", {"approve": 0.4388, "hold": 0.5222,
                                                                     "reject": 0.039}))
    assert decision == "approve" and record["holds"] == [] and record["decide"]["confidence"] == 0.2833
    assert record["flags"] == ["decide unsure (hold at p 0.5222, confidence 0.28): the checks stand"]
    record, decision, _, _, _ = go("01_clean", real_8_nov("hold", {"approve": 0.0363, "hold": 0.9324,
                                                                   "reject": 0.0313}))
    assert decision == "hold" and record["holds"] == ["decide: hold at p 0.9324 (confidence 0.90)"]
    _, decision, _, _, _ = go("02_amount_mismatch", real_8_nov("reject", {"approve": 0.2, "hold": 0.35,
                                                                          "reject": 0.45}))
    assert decision == "hold"  # an unclear reject does not make a held invoice stricter
    _, decision, _, _, _ = go("02_amount_mismatch", real_8_nov("reject", {"approve": 0.01, "hold": 0.09,
                                                                          "reject": 0.9}))
    assert decision == "reject"


def test_decide_confidence_is_the_top_probability_rescaled():
    """confidence = (p_top - 1/k) / (1 - 1/k) on every choice and yes_no answer the real run
    recorded (Decide and Fill), and on the docs example (network 0.9933 -> 0.9899)."""
    def rescaled(probs):
        k = len(probs)
        return (max(probs.values()) - 1 / k) / (1 - 1 / k)
    checked = 0
    for real in sorted(gate.FIXTURES.glob("real_*.json")):
        steps = json.loads(real.read_text())["steps"]
        answers = list(steps.get("decide", {}).get("body", {}).get("answers", {}).values())
        answers += [f for f in steps.get("fill", {}).get("body", {}).get("fields", {}).values() if "probabilities" in f]
        for a in answers:
            assert abs(rescaled(a["probabilities"]) - a["confidence"]) < 0.001, (real.name, a)
            checked += 1
    assert checked >= 20 and round(rescaled({"network": 0.9933, "hardware": 0.0035, "identity": 0.0032}), 4) == 0.9899


def test_replay_on_the_real_fixtures_gives_the_expected_decisions(capsys):
    reals = sorted(p.stem[len("real_"):] for p in gate.FIXTURES.glob("real_*.json"))
    assert reals == sorted(EXPECT)
    assert gate.main(["--replay", "--today", REAL_RUN]) == 0
    got = re.findall(r"^decision: (\w+)", capsys.readouterr().out, re.M)
    assert got == [EXPECT[s].upper() for s in reals]


def test_decide_state_is_valid_json_and_carries_the_gates_own_check_results():
    for sample in EXPECT:
        record, _, _, clocked, _ = go(sample)
        if sample == "03_injected":
            assert "decide" not in dict(clocked.calls)
            continue
        state = json.loads(dict(clocked.calls)["decide"]["state"])  # the 8 Nov state masked its own digits
        checks = state["checks"]
        assert isinstance(checks["injection_p"], float) and checks["injection_p"] == round(checks["injection_p"], 3)
        assert checks["payee_email"].startswith("failed" if sample == "06_payee_no_mail" else "passed")
        assert checks["failed_checks"] == [h for h in record["holds"] if not h.startswith("decide:")]
    assert json.loads(dict(go("01_clean")[3].calls)["decide"]["state"])["checks"]["payee_email"] == \
        "passed (mx_found, role_account)"


NOT_READ = {"value": None, "confidence": 1, "p_blank": 0, "rejected_value": "21.25", "rejected_p_verify": 0,
            "reason": "verification_rejected"}  # the real run's shape for a field Extract could not read


def test_unread_due_date_does_not_hold_a_clean_invoice_but_an_unread_total_does():
    def unread(name):
        def patch(steps):
            if "extract_invoice" in steps:
                steps["extract_invoice"]["body"]["fields"][name] = dict(NOT_READ)
        return patch
    record, decision, _, clocked, _ = go("01_clean", unread("due_date"))
    assert decision == "approve" and json.loads(dict(clocked.calls)["decide"]["state"])["invoice"]["due_date"] is None
    record, decision, _, _, _ = go("01_clean", unread("total"))
    assert decision == "hold" and "totals: could not read both totals" in record["holds"]


def folded(paths):
    """Key paths with the field name folded, so shapes compare whatever fields carried a key."""
    return {re.sub(r"^fields\.[^.]+", "fields.*", p) for p in paths}


def test_stubs_carry_the_real_response_shapes():
    """Every key path the real run returned for a step appears in the stubs for that step, and the
    stubs carry none it did not return. Allowed gap: the not-read field shape, because the stubs
    read every field right."""
    real, stub = {}, {}
    for path in sorted(gate.FIXTURES.glob("real_*.json")):
        sample = path.stem[len("real_"):]
        for side, data in ((real, json.loads(path.read_text())),
                           (stub, json.loads((gate.FIXTURES / f"{sample}.json").read_text()))):
            for step, rec in data["steps"].items():
                side.setdefault(step, set()).update(folded(gate.key_paths(rec["body"])))
    not_read = folded(gate.key_paths({"fields": {"x": NOT_READ}}))
    for step, paths in real.items():
        assert not paths - stub[step] - not_read, (step, sorted(paths - stub[step] - not_read))
        assert not stub[step] - paths, (step, sorted(stub[step] - paths))


def test_payee_policy_role_ok_disposable_and_unknown_hold():
    def verdict(row):
        client = gate.Client(lambda step, url, body: [row], gate.VERIFIER_URL)
        return gate.payee(client, row["email"], lambda line: None)["hold"]
    base = {"email": "billing@shop.example", "normalized": "billing@shop.example", "domain": "shop.example"}
    assert verdict({**base, "status": "risky", "reasons": ["mx_found", "role_account"]}) is False
    assert verdict({**base, "status": "risky", "reasons": ["mx_found", "disposable_domain"]}) is True
    assert verdict({**base, "status": "unknown", "reasons": ["dns_temporary_failure"]}) is True


def test_fixtures_are_marked_stub_and_extract_spans_are_exact():
    for sample in EXPECT:
        data = json.loads((gate.FIXTURES / f"{sample}.json").read_text())
        assert data["_note"].startswith("STUB, not a real response")
        assert all(rec["note"] == "STUB, not a real response" for rec in data["steps"].values())
        invoice, po = gate.load(sample)
        for step, text in (("extract_invoice", invoice), ("extract_po", po)):
            for name, f in data["steps"].get(step, {}).get("body", {}).get("fields", {}).items():
                assert text[f["start"]:f["end"]] == f["value"], (sample, name)
        for e in data["steps"]["pii"]["body"]["entities"]:
            assert invoice[e["start"]:e["end"]] == e["value"]


# Documented response key paths (ENDPOINTS.md); every stub body must carry them.
DOC_KEYS = {
    "injection": {"model", "injection", "verdict", "threshold", "category", "categories", "severity", "severities",
                  "severity_expected", "evidence", "source", "instructions_used", "timing"},
    "pii": {"model", "entities", "entities[].type", "entities[].value", "entities[].start", "entities[].end",
            "entities[].confidence", "entities[].source", "counts", "anonymized_text", "threshold",
            "instructions_used"},
    "extract_invoice": {"model", "fields", "fields.total.value", "fields.total.start", "fields.total.end",
                        "fields.total.p_verify", "fields.total.confidence"},
    "fill": {"model", "fields", "fields.payment_terms.value", "fields.payment_terms.probabilities",
             "fields.approver_note.source"},
    "compare": {"model", "verdict", "threshold", "instructions_used", "evidence", "documents", "documents[].id",
                "pairs", "pairs[].a", "pairs[].b", "pairs[].relation", "pairs[].related", "pairs[].probabilities",
                "pairs[].conflicts", "duplicates", "overlaps", "conflicts", "related_pairs"},
    "grounding": {"model", "grounded", "ungrounded", "verdict", "threshold", "documents", "claims", "claims[].claim",
                  "claims[].verdict", "claims[].supported", "claims[].contradicted", "claims[].not_stated",
                  "claims[].sources", "claims[].source", "unsupported_claims", "not_claims", "weakest_claim"},
    "decide": {"model", "answers", "answers.action.type", "answers.action.choice", "answers.action.probabilities",
               "answers.action.confidence", "answers.priority.score", "answers.priority.legend", "usage"},
    "payee": {"[].email", "[].normalized", "[].domain", "[].status", "[].reasons", "[].mxHost", "[].isDisposable",
              "[].isRole", "[].isFreeProvider", "[].didYouMean", "[].smtpResult", "[].smtpDetail"},
}


def test_stub_bodies_carry_the_documented_keys():
    for sample in ("02_amount_mismatch", "06_payee_no_mail"):
        steps = json.loads((gate.FIXTURES / f"{sample}.json").read_text())["steps"]
        for step, keys in DOC_KEYS.items():
            missing = keys - gate.key_paths(steps[step]["body"])
            assert not missing, (sample, step, missing)


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status, self._body = status, json.dumps(body).encode()
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, body):
    return urllib.error.HTTPError("https://x.example", status, "err", email.message.Message(),
                                  io.BytesIO(json.dumps(body).encode()))


def test_https_transport_key_from_env_same_idempotency_key_on_retry(monkeypatch):
    sent, answers = [], [http_error(429, {"error": "busy"}), FakeResponse(200, {"ok": 1}, {"X-Clocked-Credits": "7"})]

    def fake_urlopen(request, timeout):
        sent.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv(gate.CLOCKED_KEY_ENV, "test-value-not-a-real-one")
    transport = gate.HttpsTransport(gate.CLOCKED_KEY_ENV, sleep=lambda s: None)
    assert transport("decide", gate.CLOCKED_BASE + "/decide", {"q": 1}) == {"ok": 1}
    assert len(sent) == 2
    keys = {r.get_header("Idempotency-key") for r in sent}
    assert len(keys) == 1 and re.fullmatch(r"[0-9a-f-]{36}", keys.pop())
    assert sent[0].get_header("Authorization") == "Bearer test-value-not-a-real-one"
    saved = json.dumps(transport.saved)
    assert "test-value-not-a-real-one" not in saved and transport.saved["decide"]["headers"] == {"X-Clocked-Credits": "7"}


def test_https_transport_stops_on_retry_as_new_and_needs_the_env_key(monkeypatch):
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(request)
        raise http_error(503, {"error": "ended", "retryAsNew": True})
    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv(gate.CLOCKED_KEY_ENV, "test-value-not-a-real-one")
    with pytest.raises(gate.ApiError) as err:
        gate.HttpsTransport(gate.CLOCKED_KEY_ENV, sleep=lambda s: None)("pii", gate.CLOCKED_BASE + "/pii", {})
    assert err.value.status == 503 and len(sent) == 1
    monkeypatch.delenv(gate.CLOCKED_KEY_ENV)
    with pytest.raises(SystemExit):
        gate.HttpsTransport(gate.CLOCKED_KEY_ENV)("pii", gate.CLOCKED_BASE + "/pii", {})


def test_real_mode_end_to_end_on_a_fake_server_then_diff_and_replay(monkeypatch, tmp_path, capsys):
    """The path HQ runs: --real saves real_<sample>.json, --diff reads it, --replay runs on it."""
    stub = json.loads((gate.FIXTURES / "01_clean.json").read_text())
    (tmp_path / "01_clean.json").write_text(json.dumps(stub))
    monkeypatch.setattr(gate, "FIXTURES", tmp_path)
    by_path = {"/injection": "injection", "/pii": "pii", "/fill": "fill", "/compare": "compare",
               "/grounding": "grounding", "/decide": "decide"}
    extracts, seen = iter(["extract_invoice", "extract_po"]), []

    def fake_urlopen(request, timeout):
        assert request.full_url.startswith(gate.CLOCKED_BASE + "/")
        path = request.full_url[len(gate.CLOCKED_BASE):]
        seen.append(path)
        step = next(extracts) if path == "/extract" else by_path[path]
        return FakeResponse(200, stub["steps"][step]["body"], {"X-Clocked-Credits": "5"})
    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv(gate.CLOCKED_KEY_ENV, "test-value-not-a-real-one")
    assert gate.main(["--real", "--sample", "01_clean", "--today", "2026-11-07"]) == 0
    assert seen == ["/injection", "/pii", "/extract", "/extract", "/fill", "/compare", "/grounding", "/decide"]
    saved_text = (tmp_path / "real_01_clean.json").read_text()
    assert set(json.loads(saved_text)["steps"]) == set(stub["steps"]) - {"payee"}
    assert "test-value-not-a-real-one" not in saved_text
    assert "decision: APPROVE" in capsys.readouterr().out
    assert gate.main(["--diff"]) == 0
    diff = capsys.readouterr().out
    assert diff.count("only in stub [], only in real []") == 8 and "span mismatch" not in diff
    assert gate.main(["--replay", "--sample", "01_clean", "--today", "2026-11-07"]) == 0
    assert "decision: APPROVE" in capsys.readouterr().out
