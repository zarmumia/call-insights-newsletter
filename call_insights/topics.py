"""Find the questions clients keep asking, and keep only those common enough to publish.

The threshold is the core privacy rule: a topic becomes an article only when it
came up in at least `min_calls` different calls, so no article can describe one
person's case. Topics below the threshold are reported to the reviewer, never
written.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

from .claude import ClaudeClient, ask_json
from .notes import Note


@dataclass
class Topic:
    label: str
    calls: int
    common_documents: list[str] = field(default_factory=list)
    upset_share: float = 0.0

    def to_dict(self) -> dict:
        return {"label": self.label, "calls": self.calls, "common_documents": self.common_documents,
                "upset_share": self.upset_share}


class TopicFinder(Protocol):
    def assign(self, notes: list[Note]) -> dict[str, list[int]]:
        """Map topic label -> indexes of the notes that belong to it."""
        ...


def build_topics(notes: list[Note], assignment: dict[str, list[int]], min_calls: int = 10,
                 max_topics: int = 5) -> tuple[list[Topic], list[Topic]]:
    """Return (topics to write about, topics held back because too few calls)."""
    topics = []
    for label, idxs in assignment.items():
        members = [notes[i] for i in sorted(set(idxs))]
        if not members:
            continue
        doc_counts = Counter(d.lower() for n in members for d in set(n.missing_documents))
        # A document is "common" only if it recurs too, so a rare one can't single anyone out.
        common = [d for d, c in doc_counts.most_common() if c >= max(3, min_calls // 3)]
        upset = sum(n.sentiment == "upset" for n in members) / len(members)
        topics.append(Topic(label=label, calls=len(members), common_documents=common,
                            upset_share=round(upset, 2)))
    topics.sort(key=lambda t: t.calls, reverse=True)
    kept = [t for t in topics if t.calls >= min_calls][:max_topics]
    held = [t for t in topics if t not in kept]
    return kept, held


# --- offline demo engine -------------------------------------------------------------

DEMO_RULES: dict[str, list[str]] = {
    "Missing Form 106 from a second employer": ["106", "second employer"],
    "Why is my refund taking so long?": ["status", "delay", "taking so long", "still waiting", "arrive"],
    "Donation receipts and the tax credit": ["donation", "receipt", "charity"],
    "Pension withdrawals and tax": ["pension"],
    "Moving abroad mid-year": ["abroad", "relocat"],
}


class DemoTopicFinder:
    """Keyword rules, just enough to demo the pipeline. NOT meant for production."""

    def __init__(self, rules: dict[str, list[str]] | None = None):
        self.rules = rules or DEMO_RULES

    def assign(self, notes: list[Note]) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for i, note in enumerate(notes):
            low = note.text.lower()
            for label, words in self.rules.items():  # first matching rule wins
                if any(w in low for w in words):
                    out.setdefault(label, []).append(i)
                    break
        return out


# --- Claude engine ---------------------------------------------------------------------

CLUSTER_PROMPT = """You group customer-service call notes from a {business} by the underlying question.
Each line is one call: "<number>. <what the client wanted> | <documents still missing>".
Return ONLY JSON: {{"topics": [{{"label": "...", "calls": [<numbers>]}}]}}
Rules:
- A label names the question as a client would ask it, e.g. "Why is my refund taking so long?"
- Every call belongs to at most one topic. Leave out calls that fit no recurring question.
- Merge near-duplicates; prefer fewer, broader topics (at most {max_topics} plus "other" calls left out).
- Write labels in the language of the notes.

Calls:
{lines}"""


def validate_assignment(data: Any, n_notes: int) -> dict[str, list[int]]:
    if not isinstance(data, dict) or not isinstance(data.get("topics"), list):
        raise ValueError('Expected {"topics": [...]}')
    seen: set[int] = set()
    out: dict[str, list[int]] = {}
    for t in data["topics"]:
        label = t.get("label") if isinstance(t, dict) else None
        calls = t.get("calls") if isinstance(t, dict) else None
        if not isinstance(label, str) or not label.strip() or not isinstance(calls, list):
            raise ValueError("Each topic needs a label and a list of calls")
        idxs = []
        for c in calls:
            if not isinstance(c, int) or not 1 <= c <= n_notes:
                raise ValueError(f"Call number out of range: {c}")
            if c - 1 in seen:
                raise ValueError(f"Call {c} assigned to two topics")
            seen.add(c - 1)
            idxs.append(c - 1)
        out[label.strip()] = idxs
    return out


class ClaudeTopicFinder:
    def __init__(self, client: ClaudeClient, business: str = "small service business", max_topics: int = 5):
        self.client, self.business, self.max_topics = client, business, max_topics

    def assign(self, notes: list[Note]) -> dict[str, list[int]]:
        lines = "\n".join(
            f"{i + 1}. {n.client_intent} | {', '.join(n.missing_documents) or '-'}" for i, n in enumerate(notes)
        )
        prompt = CLUSTER_PROMPT.format(business=self.business, max_topics=self.max_topics, lines=lines)
        return ask_json(self.client, prompt, lambda d: validate_assignment(d, len(notes)), max_tokens=3000)
