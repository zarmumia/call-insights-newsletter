"""Assemble the draft, check it for leaks, and gate publishing on a human approval.

A draft is two files:
- `newsletter-<date>.md`: the text, which the reviewer edits freely. It opens
  with a review checklist; every box must be ticked before approval.
- `newsletter-<date>.meta.json`: status, topics, and fingerprints of the source
  notes. Fingerprints are hashes, so the meta file holds no call text.

`approve` re-reads the (possibly edited) text, runs the leak check again, and
only then records who approved it and sends it on.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import datetime
from pathlib import Path

from .notes import Note
from .redact import find_identifiers
from .topics import Topic
from .writer import Article, PressNote

SHINGLE = 8  # words; a copied run this long from a call note counts as a leak

CHECKLIST = [
    "Every [VERIFY] item is checked and the marker removed",
    "No article describes a specific client or case",
    "The news items are summarized in our own words and link to the source",
    "Tone and facts are right for our clients",
]
REVIEW_START, REVIEW_END = "<!-- REVIEW", "-->"


class ApprovalError(RuntimeError):
    pass


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def fingerprints(texts: list[str], n: int = SHINGLE) -> set[str]:
    out = set()
    for t in texts:
        w = _words(t)
        for i in range(len(w) - n + 1):
            out.add(hashlib.sha256(" ".join(w[i:i + n]).encode()).hexdigest()[:16])
    return out


def leak_check(text: str, source_prints: set[str]) -> list[str]:
    """Problems that block approval: identifier patterns, or text copied from a call note."""
    issues = [f"Looks like an identifier ({label})" for label in find_identifiers(text)]
    copied = fingerprints([text]) & source_prints
    if copied:
        issues.append(f"{len(copied)} phrase(s) of {SHINGLE}+ words copied from call notes")
    return issues


def assemble(business: str, date: str, articles: list[Article], press: list[PressNote],
             held_back: list[Topic]) -> str:
    lines = [REVIEW_START, "Review before approving. Tick every box: [ ] -> [x].", ""]
    lines += [f"[ ] {c}" for c in CHECKLIST]
    if held_back:
        lines += ["", "Held back (too few calls to publish without pointing at someone):"]
        lines += [f"- {t.label}: {t.calls} calls" for t in held_back]
    lines += [REVIEW_END, "", f"# {business} - client update, {date}", ""]
    for a in articles:
        lines += [f"## {a.title}", "", a.body, ""]
    if press:
        lines += ["## In the news", ""]
        lines += [f"- **[{p.item.title}]({p.item.link})** ({p.item.source}): {p.why_it_matters}" for p in press]
        lines.append("")
    return "\n".join(lines)


def split_review(md: str) -> tuple[str, str]:
    """Return (review block, publishable text)."""
    start = md.find(REVIEW_START)
    if start == -1:
        return "", md.strip()
    end = md.find(REVIEW_END, start)
    if end == -1:
        raise ApprovalError("Review block is not closed")
    return md[start:end], (md[:start] + md[end + len(REVIEW_END):]).strip()


def save_draft(out_dir: str | Path, date: str, md: str, meta: dict) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path, meta_path = out / f"newsletter-{date}.md", out / f"newsletter-{date}.meta.json"
    md_path.write_text(md, encoding="utf-8")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return md_path, meta_path


def build_meta(business: str, now: datetime, notes: list[Note], topics: list[Topic], held: list[Topic],
               engine: str, press_errors: list[str]) -> dict:
    return {
        "status": "draft",
        "business": business,
        "created_at": now.isoformat(),
        "engine": engine,
        "calls_analyzed": len(notes),
        "topics": [t.to_dict() for t in topics],
        "held_back": [t.to_dict() for t in held],
        "press_errors": press_errors,
        "source_fingerprints": sorted(fingerprints([n.text for n in notes])),
        "approved_by": None,
        "approved_at": None,
    }


def approve(md_path: str | Path, by: str, now: datetime, webhook: str | None = None) -> dict:
    md_path = Path(md_path)
    meta_path = md_path.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("status") == "approved":
        raise ApprovalError(f"Already approved by {meta['approved_by']}")
    if not by.strip():
        raise ApprovalError("An approver name is required")

    review, body = split_review(md_path.read_text(encoding="utf-8"))
    if "[ ]" in review:
        raise ApprovalError("Review checklist has unticked boxes")
    if "[VERIFY" in body:
        raise ApprovalError("Draft still contains [VERIFY] markers")
    issues = leak_check(body, set(meta.get("source_fingerprints", [])))
    if issues:
        raise ApprovalError("Leak check failed: " + "; ".join(issues))

    meta.update(status="approved", approved_by=by.strip(), approved_at=now.isoformat(),
                content_sha256=hashlib.sha256(body.encode()).hexdigest())
    if webhook:
        payload = {"business": meta["business"], "approved_by": meta["approved_by"], "markdown": body}
        req = urllib.request.Request(webhook, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            meta["webhook_status"] = r.status
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.with_name(md_path.stem + ".approved.md").write_text(body + "\n", encoding="utf-8")
    return meta
