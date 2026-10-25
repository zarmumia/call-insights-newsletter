import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from call_insights.__main__ import main
from call_insights.draft import ApprovalError, approve, assemble, build_meta, fingerprints, leak_check, save_draft
from call_insights.notes import Note, load_notes
from call_insights.press import parse_feed, scan
from call_insights.redact import redact
from call_insights.topics import ClaudeTopicFinder, DemoTopicFinder, build_topics, validate_assignment
from call_insights.writer import Article, ClaudeWriter

SAMPLES = Path(__file__).parent.parent / "samples"
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=ZoneInfo("Asia/Jerusalem"))


class FakeClient:
    """Stands in for ClaudeClient: returns scripted replies and records prompts."""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt, max_tokens=1500):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def note(i, intent="Asks about refund status", summary="Client is waiting for the refund.", docs=(), mood="neutral"):
    return Note(f"c{i}", summary, intent, list(docs), mood)


# --- redaction & loading ------------------------------------------------------------------

def test_redaction_covers_identifiers_and_names():
    text, counts = redact("Mr. Cohen, 052-123-4567, ID 012345678, a@b.co, IL62 0108 0000 0009 9999 999")
    for secret in ("Cohen", "4567", "012345678", "a@b.co", "IL62"):
        assert secret not in text
    assert counts["NAME"] == 1 and counts["PHONE"] == 1 and counts["IBAN"] == 1


def test_load_notes_dedupes_skips_bad_and_unwraps_webhook_payloads(tmp_path):
    good = {"call_id": "a", "summary": "Called 052-123-4567", "client_intent": "Status", "missing_documents": []}
    wrapped = {"summary": {"call_id": "b", "summary": "S", "client_intent": "I"}, "note": "..."}
    f = tmp_path / "n.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [good, good, wrapped, {"summary": ""}]))
    notes, skipped = load_notes(f)
    assert [n.call_id for n in notes] == ["a", "b"] and skipped == 1
    assert "4567" not in notes[0].summary  # redacted again on load


def test_load_notes_from_folder(tmp_path):
    for i in range(3):
        (tmp_path / f"{i}.json").write_text(json.dumps({"summary": "S", "client_intent": "I", "call_id": str(i)}))
    assert len(load_notes(tmp_path)[0]) == 3


# --- topics & the publishing threshold ---------------------------------------------------

def test_threshold_holds_back_small_topics_and_rare_documents():
    notes = [note(i, docs=["106 form"]) for i in range(10)] + [note(10, docs=["rare letter"])]
    notes += [note(i, intent="Pension") for i in range(11, 14)]
    kept, held = build_topics(notes, {"Status": list(range(11)), "Pension": [11, 12, 13]}, min_calls=10)
    assert [t.label for t in kept] == ["Status"] and [t.label for t in held] == ["Pension"]
    assert kept[0].common_documents == ["106 form"]  # "rare letter" appears once: never surfaced


def test_demo_finder_on_samples_counts_each_call_once():
    notes, _ = load_notes(SAMPLES / "notes.jsonl")
    kept, held = build_topics(notes, DemoTopicFinder().assign(notes), min_calls=10)
    assert {t.label: t.calls for t in kept} == {
        "Missing Form 106 from a second employer": 13,
        "Why is my refund taking so long?": 11,
        "Donation receipts and the tax credit": 10,
    }
    assert {t.label for t in held} == {"Pension withdrawals and tax", "Moving abroad mid-year"}


@pytest.mark.parametrize("data", [
    {"topics": [{"label": "A", "calls": [0]}]},              # numbers start at 1
    {"topics": [{"label": "A", "calls": [1]}, {"label": "B", "calls": [1]}]},  # one call, two topics
    {"topics": [{"label": "", "calls": [1]}]},
    ["not", "an", "object"],
])
def test_assignment_validation_rejects_bad_output(data):
    with pytest.raises(ValueError):
        validate_assignment(data, n_notes=3)


def test_claude_finder_retries_on_invalid_json():
    client = FakeClient("sorry, no json", '```json\n{"topics": [{"label": "Status", "calls": [1, 2]}]}\n```')
    out = ClaudeTopicFinder(client).assign([note(1), note(2)])  # type: ignore[arg-type]
    assert out == {"Status": [0, 1]} and len(client.prompts) == 2


# --- writer: never sees a single call -------------------------------------------------------

def test_writer_prompt_contains_aggregates_only():
    notes = [note(i, summary=f"Unique detail number {i} about a nurse in a small town.") for i in range(12)]
    kept, _ = build_topics(notes, {"Status": list(range(12))}, min_calls=10)
    body = " ".join(["word"] * 150)
    client = FakeClient(json.dumps({"title": "Status", "body": body}))
    ClaudeWriter(client).article(kept[0])  # type: ignore[arg-type]
    assert "nurse" not in client.prompts[0] and "12 calls" in client.prompts[0]


def test_writer_rejects_too_short_article_then_accepts():
    client = FakeClient(json.dumps({"title": "T", "body": "too short"}),
                        json.dumps({"title": "T", "body": " ".join(["ok"] * 120)}))
    topic = build_topics([note(i) for i in range(10)], {"S": list(range(10))})[0][0]
    assert ClaudeWriter(client).article(topic).title == "T"  # type: ignore[arg-type]


# --- press scan --------------------------------------------------------------------------

def test_press_scan_filters_by_keyword_and_date():
    items, errors = scan(SAMPLES / "feeds.txt", ["tax", "refund"], NOW, days=30)
    titles = [i.title for i in items]
    assert errors == [] and len(items) == 2
    assert not any("football" in t.lower() or "old story" in t.lower() for t in titles)


def test_press_parses_atom_and_reports_broken_feeds(tmp_path):
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title><entry><title>Refund news</title>
    <link href="https://example.com/a"/><updated>2026-10-20T10:00:00Z</updated></entry></feed>"""
    assert parse_feed(atom, "a")[0].link == "https://example.com/a"
    (tmp_path / "ok.xml").write_bytes(atom)
    (tmp_path / "feeds.txt").write_text("ok.xml\nmissing.xml\n")
    items, errors = scan(tmp_path / "feeds.txt", ["refund"], NOW)
    assert len(items) == 1 and len(errors) == 1


# --- leak check & approval gate ----------------------------------------------------------

def test_leak_check_catches_identifiers_and_copied_phrases():
    source = "the client works night shifts at the port and has three kids"
    prints = fingerprints([source])
    assert leak_check("General advice about forms.", prints) == []
    assert any("PHONE" in i for i in leak_check("Call 052-123-4567", prints))
    assert any("copied" in i for i in leak_check(f"Story: {source}.", prints))


def make_draft(tmp_path, body="General advice about missing forms and what to do next."):
    notes = [note(i, summary="the client works night shifts at the port and has three kids") for i in range(10)]
    kept, held = build_topics(notes, {"Status": list(range(10))})
    md = assemble("Biz", "2026-10-26", [Article("Status", body)], [], held)
    meta = build_meta("Biz", NOW, notes, kept, held, "demo", [])
    return save_draft(tmp_path, "2026-10-26", md, meta)[0]


def tick(path):
    path.write_text(path.read_text().replace("[ ]", "[x]"))


def test_approval_requires_ticked_checklist(tmp_path):
    md = make_draft(tmp_path)
    with pytest.raises(ApprovalError, match="unticked"):
        approve(md, "Dani", NOW)
    tick(md)
    meta = approve(md, "Dani", NOW)
    assert meta["status"] == "approved" and meta["approved_by"] == "Dani"
    approved = (tmp_path / "newsletter-2026-10-26.approved.md").read_text()
    assert "REVIEW" not in approved and "General advice" in approved
    with pytest.raises(ApprovalError, match="Already approved"):
        approve(md, "Dani", NOW)


def test_approval_blocks_verify_markers(tmp_path):
    md = make_draft(tmp_path, body="Deadline is [VERIFY: six years?] for claims.")
    tick(md)
    with pytest.raises(ApprovalError, match="VERIFY"):
        approve(md, "Dani", NOW)


def test_approval_rechecks_text_edited_by_reviewer(tmp_path):
    md = make_draft(tmp_path)
    tick(md)
    md.write_text(md.read_text() + "\nFor example the client works night shifts at the port and has three kids.\n")
    with pytest.raises(ApprovalError, match="Leak check"):
        approve(md, "Dani", NOW)
    assert json.loads(md.with_suffix(".meta.json").read_text())["status"] == "draft"


def test_approved_text_goes_to_webhook(tmp_path, monkeypatch):
    sent = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        sent.update(json.loads(req.data))
        return Resp()

    monkeypatch.setattr("call_insights.draft.urllib.request.urlopen", fake_urlopen)
    md = make_draft(tmp_path)
    tick(md)
    meta = approve(md, "Dani", NOW, webhook="https://hook.example")
    assert meta["webhook_status"] == 200 and sent["approved_by"] == "Dani" and "REVIEW" not in sent["markdown"]


def test_meta_file_holds_no_call_text(tmp_path):
    md = make_draft(tmp_path)
    assert "night shifts" not in md.with_suffix(".meta.json").read_text()


# --- end to end ---------------------------------------------------------------------------

def test_cli_demo_end_to_end(tmp_path, capsys):
    rc = main(["draft", str(SAMPLES / "notes.jsonl"), "--feeds", str(SAMPLES / "feeds.txt"),
               "--keywords", "tax,refund", "--demo", "--now", "2026-10-26T09:00", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "13 calls" in out and "hold" in out and "Nothing is sent" in out
    md = tmp_path / "newsletter-2026-10-26.md"
    assert main(["approve", str(md), "--by", "Dani"]) == 1  # demo text is full of [VERIFY] and boxes are unticked
