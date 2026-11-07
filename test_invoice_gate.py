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
          "04_personal_data": "approve", "05_overdue": "approve", "06_payee_no_mail": "hold"}
# Typed from the sample files by hand, independent of the Protect fixtures.
PERSONAL = {
    "01_clean": ["Dana Whitlock", "5521 0087 3301", "billing@ferncastle-paper.example"],
    "02_amount_mismatch": ["Priya Okonkwo-Lund", "7304 1189 6614", "billing@duskvale-fasteners.example"],
    "03_injected": ["Tomasz Ferreira-Holt", "4100 2563 4421", "9000 1122 7788", "billing@brindlewick-cleaning.example"],
    "04_personal_data": ["Marisol Quentin-Vale", "27 Larkspur Close", "+1 202 555 0143",
                         "marisol.qv@quentinvale-translate.example", "1984-03-09", "000-12-3456", "3108 4471 5562"],
    "05_overdue": ["Rowan Achterberg", "6620 9013 2207", "billing@marrowgate-print.example"],
    "06_payee_no_mail": ["Ines Barrowclough", "2291 5530 8840", "billing@example.net"],
}
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
    assert len(record["compare"]["conflicts"]) == 2
    assert [c["claim"] for c in record["verify"]["contradicted"]] == ["The order total is USD 327.00."]


def test_overdue_invoice_is_flagged_not_held():
    record, decision, _, _, _ = go("05_overdue")
    assert decision == "approve" and record["flags"] == ["overdue 41 days"] and record["decide"]["priority"] == "urgent"


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
