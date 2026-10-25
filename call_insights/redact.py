"""Identifier patterns, shared by the input cleaner and the final leak check.

Notes coming from ai-call-summary are already redacted. We redact again on load
(defense in depth: notes may come from other tools) and scan the finished draft
with the same patterns before anyone can approve it.
"""
from __future__ import annotations

import re

PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("PHONE", re.compile(r"(?:\+\d{1,3}[-\s]?|0)(?:[2-9]\d?)[-\s]?\d{3}[-\s]?\d{4}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("ID", re.compile(r"\b\d{8,10}\b")),
    # Honorific + capitalised name: "Mr. Cohen", "Mrs Levi"
    ("NAME", re.compile(r"\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")),
]


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Return the redacted text and a count of what was removed."""
    counts: dict[str, int] = {}
    for label, pattern in PATTERNS:
        text, n = pattern.subn(f"[{label}]", text)
        if n:
            counts[label] = n
    return text, counts


def find_identifiers(text: str) -> list[str]:
    """Labels of identifier patterns found in text (used by the leak check)."""
    return [label for label, pattern in PATTERNS if pattern.search(text)]
