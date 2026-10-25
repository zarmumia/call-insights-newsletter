"""Load call notes: the JSON output of ai-call-summary, or anything shaped like it."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .redact import redact


@dataclass
class Note:
    call_id: str
    summary: str
    client_intent: str
    missing_documents: list[str] = field(default_factory=list)
    sentiment: str = "neutral"

    @property
    def text(self) -> str:
        docs = ", ".join(self.missing_documents)
        return f"{self.client_intent}. {self.summary} {docs}".strip()


def _records(path: Path) -> list[dict]:
    if path.is_dir():
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(path.glob("*.json"))]
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("["):
        return list(json.loads(raw))
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _note(rec: dict, fallback_id: str) -> Note | None:
    # ai-call-summary --webhook payloads wrap the fields in {"summary": {...}, "note": "..."}
    if isinstance(rec.get("summary"), dict):
        rec = {**rec["summary"], "call_id": rec.get("call_id", rec["summary"].get("call_id"))}
    summary, intent = rec.get("summary"), rec.get("client_intent")
    if not isinstance(summary, str) or not isinstance(intent, str) or not summary.strip():
        return None
    docs = rec.get("missing_documents") or []
    return Note(
        call_id=str(rec.get("call_id") or fallback_id),
        summary=redact(summary.strip())[0],
        client_intent=redact(intent.strip())[0],
        missing_documents=[redact(str(d).strip())[0] for d in docs if str(d).strip()],
        sentiment=str(rec.get("sentiment", "neutral")),
    )


def load_notes(path: str | Path) -> tuple[list[Note], int]:
    """Return valid notes and how many records were skipped as malformed.

    Notes with the same call_id are counted once, so a duplicated export can't
    push a topic over the publishing threshold.
    """
    notes: dict[str, Note] = {}
    skipped = 0
    for i, rec in enumerate(_records(Path(path))):
        note = _note(rec, fallback_id=f"call-{i + 1}") if isinstance(rec, dict) else None
        if note is None:
            skipped += 1
        else:
            notes.setdefault(note.call_id, note)
    return list(notes.values()), skipped
