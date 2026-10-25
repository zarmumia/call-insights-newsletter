"""CLI.

  python -m call_insights draft samples/notes.jsonl --feeds samples/feeds.txt --demo
  python -m call_insights approve drafts/newsletter-2026-10-26.md --by "Dani"
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from .claude import ClaudeClient
from .draft import ApprovalError, approve, assemble, build_meta, save_draft
from .notes import load_notes
from .press import PressItem, scan
from .topics import ClaudeTopicFinder, DemoTopicFinder, TopicFinder, build_topics
from .writer import ClaudeWriter, DemoWriter, Writer


def _now(value: str | None, tz: ZoneInfo) -> datetime:
    now = datetime.fromisoformat(value) if value else datetime.now(tz)
    return now if now.tzinfo else now.replace(tzinfo=tz)


def cmd_draft(a: argparse.Namespace) -> int:
    tz = ZoneInfo(a.tz)
    now = _now(a.now, tz)
    notes, skipped = load_notes(a.notes)
    use_demo = a.demo or not os.environ.get("ANTHROPIC_API_KEY")
    finder: TopicFinder
    writer: Writer
    if use_demo:
        finder, writer = DemoTopicFinder(), DemoWriter()
    else:
        client = ClaudeClient()
        finder = ClaudeTopicFinder(client, a.business, a.max_topics)
        writer = ClaudeWriter(client, a.business, a.language)

    topics, held = build_topics(notes, finder.assign(notes), a.min_calls, a.max_topics)
    articles = [writer.article(t) for t in topics]
    press: list[PressItem] = []
    errors: list[str] = []
    if a.feeds:
        keywords = [k.strip() for k in a.keywords.split(",") if k.strip()] if a.keywords else []
        press, errors = scan(a.feeds, keywords, now, days=a.days)
    notes_text = writer.press_notes(press)

    date = now.date().isoformat()
    md = assemble(a.business, date, articles, notes_text, held)
    meta = build_meta(a.business, now, notes, topics, held, "demo" if use_demo else "claude", errors)
    md_path, _ = save_draft(a.out, date, md, meta)

    print(f"[engine: {meta['engine']} · {len(notes)} calls analyzed"
          f"{f' · {skipped} malformed skipped' if skipped else ''}]")
    for t in topics:
        print(f"  write   {t.calls:>3} calls  {t.label}")
    for t in held:
        print(f"  hold    {t.calls:>3} calls  {t.label}  (below {a.min_calls})")
    print(f"  news    {len(press)} item(s)" + (f", {len(errors)} feed error(s)" if errors else ""))
    print(f"\nDraft ready for review: {md_path}")
    print("Nothing is sent until someone runs `approve`.")
    return 0


def cmd_approve(a: argparse.Namespace) -> int:
    try:
        meta = approve(a.draft, a.by, _now(None, ZoneInfo(a.tz)), a.webhook)
    except ApprovalError as e:
        print(f"Not approved: {e}", file=sys.stderr)
        return 1
    print(f"Approved by {meta['approved_by']} at {meta['approved_at']}")
    if "webhook_status" in meta:
        print(f"Webhook responded {meta['webhook_status']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="call_insights",
                                description="Recurring client questions -> newsletter draft -> human approval.")
    p.add_argument("--tz", default="Asia/Jerusalem", help="Business time zone")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("draft", help="Build a newsletter draft from call notes")
    d.add_argument("notes", help=".jsonl / .json file, or a folder of ai-call-summary --json outputs")
    d.add_argument("--business", default="Our team", help="Business name shown in the newsletter")
    d.add_argument("--min-calls", type=int, default=10, help="A topic needs at least this many calls")
    d.add_argument("--max-topics", type=int, default=5)
    d.add_argument("--feeds", help="Text file with one RSS/Atom URL (or local file) per line")
    d.add_argument("--keywords", help="Comma-separated words a news item must mention")
    d.add_argument("--days", type=int, default=30, help="News window in days")
    d.add_argument("--language", default="English", help="Language of the newsletter")
    d.add_argument("--out", default="drafts")
    d.add_argument("--now", help="Treat this ISO datetime as 'now'")
    d.add_argument("--demo", action="store_true", help="Offline engine, no API key")
    d.set_defaults(func=cmd_draft)

    ap = sub.add_parser("approve", help="Approve a reviewed draft and send it on")
    ap.add_argument("draft", help="Path to newsletter-<date>.md")
    ap.add_argument("--by", required=True, help="Name of the person approving")
    ap.add_argument("--webhook", help="POST the approved text to Make / Zapier / n8n / your mailing tool")
    ap.set_defaults(func=cmd_approve)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
