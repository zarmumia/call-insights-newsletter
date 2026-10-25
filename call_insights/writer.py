"""Write the articles and the press notes.

The writer never sees a single call. For each topic it gets only aggregates:
the question, how many calls raised it, which documents recur, and how many
callers were upset. It writes a general, helpful explanation, not a story
about anyone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .claude import ClaudeClient, ask_json
from .press import PressItem
from .topics import Topic


@dataclass
class Article:
    title: str
    body: str


@dataclass
class PressNote:
    item: PressItem
    why_it_matters: str


class Writer(Protocol):
    def article(self, topic: Topic) -> Article: ...
    def press_notes(self, items: list[PressItem]) -> list[PressNote]: ...


class DemoWriter:
    """Templates, so the pipeline runs offline. The text is a placeholder for the reviewer."""

    def article(self, topic: Topic) -> Article:
        docs = (f" The document that comes up most: {', '.join(topic.common_documents)}."
                if topic.common_documents else "")
        mood = (" Many of these callers were frustrated, so lead with what the reader can do today."
                if topic.upset_share >= 0.3 else "")
        body = (f"[VERIFY: demo placeholder - the Claude engine writes the real article.]\n\n"
                f"This question came up in {topic.calls} calls this period.{docs}{mood}\n\n"
                f"Explain the rule in plain words, list the steps the reader can take, "
                f"and say when it's worth calling us.")
        return Article(title=topic.label, body=body)

    def press_notes(self, items: list[PressItem]) -> list[PressNote]:
        return [PressNote(i, "[VERIFY: one sentence on why this matters to our clients.]") for i in items]


ARTICLE_PROMPT = """You write a short newsletter article for the clients of a {business}.
This question keeps coming up in customer calls: "{label}"
It came up in {calls} calls this period.{docs}{mood}

Write a helpful article that answers the question in general terms:
- 180-260 words, plain language, short paragraphs, no jargon without an explanation
- what the rule or process is, what the reader can check or do today, and when to contact us
- do NOT describe any individual case, client, number of days, amount or date you were not given
- do NOT promise outcomes or give personal tax or legal advice
- if a fact depends on the current law and you are not certain, write [VERIFY: ...] so the reviewer checks it
- language: {language}
Return ONLY JSON: {{"title": "...", "body": "..."}}"""

PRESS_PROMPT = """You prepare the "In the news" section of a newsletter for the clients of a {business}.
For each item below write ONE sentence, in your own words, on why it may matter to them.
Do not quote the article. If the excerpt is too thin to tell, write "[VERIFY: read the article]".
Language: {language}
Return ONLY JSON: {{"notes": ["...", "..."]}} with exactly {n} sentences in the same order.

{items}"""


def _validate_article(data: Any) -> Article:
    if not isinstance(data, dict):
        raise ValueError("Expected an object")
    title, body = data.get("title"), data.get("body")
    if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
        raise ValueError("Article needs a title and a body")
    words = len(body.split())
    if not 80 <= words <= 450:
        raise ValueError(f"Article length out of range: {words} words")
    return Article(title.strip(), body.strip())


class ClaudeWriter:
    def __init__(self, client: ClaudeClient, business: str = "small service business", language: str = "English"):
        self.client, self.business, self.language = client, business, language

    def article(self, topic: Topic) -> Article:
        docs = (f" Documents clients are most often missing: {', '.join(topic.common_documents)}."
                if topic.common_documents else "")
        mood = (f" About {round(topic.upset_share * 100)}% of these callers were upset."
                if topic.upset_share >= 0.3 else "")
        prompt = ARTICLE_PROMPT.format(business=self.business, label=topic.label, calls=topic.calls,
                                       docs=docs, mood=mood, language=self.language)
        return ask_json(self.client, prompt, _validate_article)

    def press_notes(self, items: list[PressItem]) -> list[PressNote]:
        if not items:
            return []
        listing = "\n".join(f"{n + 1}. {i.title} ({i.source}) - {i.excerpt}" for n, i in enumerate(items))
        prompt = PRESS_PROMPT.format(business=self.business, language=self.language, n=len(items), items=listing)

        def validate(data: Any) -> list[str]:
            notes = data.get("notes") if isinstance(data, dict) else None
            if not isinstance(notes, list) or len(notes) != len(items) or not all(
                    isinstance(s, str) and s.strip() for s in notes):
                raise ValueError("Expected one note per item")
            return [s.strip() for s in notes]

        return [PressNote(i, s) for i, s in zip(items, ask_json(self.client, prompt, validate), strict=True)]
