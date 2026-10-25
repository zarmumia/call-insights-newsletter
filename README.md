# Call Insights Newsletter

**What clients keep asking on the phone, turned into a newsletter they actually want to read. A person approves every issue.**

> About this repo: a working demo from the Zarmumia portfolio. It shows the approach, not a finished product: rules a business owner can read, privacy first, and building on the tools a business already has. In client work it is adapted to the specific business, sources and mailing tool.
>
> Companion to [ai-call-summary](https://github.com/zarmumia/ai-call-summary), which turns each call into a structured CRM note. This tool reads those notes.

Small service businesses answer the same questions every day and still struggle to write a newsletter. The content is already there, in the calls. This tool finds the questions that keep coming up, drafts a short article for each, adds relevant news from the field, and hands everything to a person who checks and approves it. Nothing goes out without that approval.

Example run on the fictional sample data (a tax-refund office):

```
$ python -m call_insights draft samples/notes.jsonl --feeds samples/feeds.txt \
    --keywords tax,refund --business "Example Tax Refunds" --demo --now 2026-10-26T09:00

[engine: demo · 41 calls analyzed · 1 malformed skipped]
  write    13 calls  Missing Form 106 from a second employer
  write    11 calls  Why is my refund taking so long?
  write    10 calls  Donation receipts and the tax credit
  hold      4 calls  Pension withdrawals and tax  (below 10)
  hold      3 calls  Moving abroad mid-year  (below 10)
  news    2 item(s)

Draft ready for review: drafts/newsletter-2026-10-26.md
Nothing is sent until someone runs `approve`.
```

## How it works

1. **Call notes in.** The JSON output of `ai-call-summary` (a folder, a `.jsonl` file or webhook payloads). Notes are redacted again on load, and duplicated exports are counted once.
2. **Find recurring questions.** Claude groups the calls by the question behind them. The output is validated: every call number must exist, and no call can sit in two topics.
3. **Publishing threshold.** A topic becomes an article only if it came up in at least `--min-calls` different calls (default 10). Smaller topics are listed for the reviewer and never written, so an article can't point at one person's case. A missing document is mentioned only if it recurs too.
4. **Write from aggregates.** The writer never sees a call. Per topic it gets the question, the number of calls, the common documents and the share of upset callers, and writes a general, practical answer. Anything that depends on current law and isn't certain is marked `[VERIFY: ...]`.
5. **News from the field.** RSS/Atom feeds you choose, filtered by keywords and date. Each item gets one sentence in our own words and a link to the source. Nothing is copied.
6. **Human approval.** The draft opens with a review checklist. `approve` refuses while any box is unticked, any `[VERIFY]` remains, or the leak check fails. The leak check runs again on the text as the reviewer edited it.
7. **Send.** `--webhook` posts the approved text to Make / Zapier / n8n or your mailing tool.

## Quick start

```bash
# Offline demo, no API key needed (keyword rules and placeholder text, for trying the pipeline)
python -m call_insights draft samples/notes.jsonl --feeds samples/feeds.txt \
    --keywords tax,refund --business "Example Tax Refunds" --demo --now 2026-10-26T09:00

# Real drafts with Claude
export ANTHROPIC_API_KEY=...
python -m call_insights draft notes/ --feeds feeds.txt --keywords tax,refund \
    --business "Your business" --language Hebrew

# After review: tick the boxes, resolve every [VERIFY], then
python -m call_insights approve drafts/newsletter-2026-10-26.md --by "Dani" --webhook https://your-hook-url
```

No dependencies beyond Python 3.10+. The model is configurable with `ANTHROPIC_MODEL`.

## Why it's built this way

- **Patterns, not people.** The threshold and the aggregates-only writer mean an article describes what many clients ask, never what one client said.
- **The draft is the product, the approval is the gate.** AI writes a first version fast. A person who knows the field decides what goes out, and the tool enforces that.
- **Checked twice.** Identifier patterns and phrases copied from call notes are blocked at approval, including in text the reviewer added.
- **No call text at rest.** The draft's metadata stores hashed fingerprints of the notes for the leak check, not the notes.
- **Works with the tools you already have.** Notes in as JSON, newsletter out as a webhook.

## Privacy and limits

- Redaction is pattern-based (emails, phones, ID, card and bank numbers, titled names). It does not catch every name or every combination of details. That is why the threshold, the aggregates-only writer and the human review exist. Treat them as layers, not as a guarantee.
- The privacy rules for customer data differ by country (for example GDPR in the EU, the Privacy Protection Law in Israel). Check what applies to your business before using real call data.
- Articles are general information, not personal tax or legal advice. The reviewer is responsible for the facts.

## Tests

```bash
pip install pytest && pytest
```

The tests cover redaction, loading and de-duplicating notes, the publishing threshold, validating messy model output, the writer never seeing call text, the press filter, the leak check, and every way the approval gate can refuse, using a mocked API.

---

Built by Fanny Rabinovitch · [Zarmumia](https://github.com/zarmumia): operations, automation and AI workflows for small and growing businesses. Sample calls and news items are fictional.
