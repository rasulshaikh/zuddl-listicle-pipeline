# Listicle Generation Pipeline

Turns a primary keyword + secondary keywords into a ~95% publish-ready software
listicle (à la "8 Best Mobile Event Apps for B2B Conferences") with consistent
structure, grounded facts, hyperlinks, and an automated QA pass.

Research → **[human gate 1]** → generate → assemble → QA → **[human gate 2]** → publish.

## Why it's built this way (the 30-second version)

The hard part of a comparison listicle isn't the writing — it's (1) not hallucinating
prices/ratings, (2) producing the *same* structure every time, and (3) not sounding
like AI. So: research is grounded with web search and **frozen behind a human check**,
the article skeleton is **code (not a prompt)**, the writer is **forbidden from inventing
facts** (QA fails the build if a number isn't traceable), and a humanization pass strips
AI-tell phrases. See `ARCHITECTURE.md` for the design and `ALIGNMENT.md` for a
requirement-by-requirement map to the brief.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env     # add the key for whichever provider you'll use:
#   ANTHROPIC_API_KEY=sk-ant-...   (Claude)   and/or   OPENAI_API_KEY=sk-...   (GPT)
```

> **Live mode gotchas:** for **Anthropic**, an org admin must enable **web search** in the
> Claude Console or research 400s. For **OpenAI**, web search needs a supported model
> (GPT-5.x use `web_search`; older models like `gpt-4o` need `web_search_preview`).
> Mock mode needs neither a key nor the network.

## Run it

**Mock mode** (fixtures, no API spend, no network — start here):

```bash
# One-shot demo (skips the pauses):
python -m pipeline.run all --input config/categories/event_registration.yaml --mock

# Or the real gated flow:
python -m pipeline.run research --input config/categories/event_registration.yaml --mock
#   -> writes output/<cat>/research.json, then STOPS  (GATE 1: review the facts)
python -m pipeline.run generate --research output/<cat>/research.json --mock
#   -> writes output/<slug>/draft.md + qa_report.md  (GATE 2: skim tone, publish)

# High volume: many listicles from one spreadsheet -> drafts + a triage summary:
python -m pipeline.run batch --csv config/batch_example.csv --mock
#   -> output/batch_<ts>/SUMMARY.md flags which drafts need a human (gate 2 at scale)
```

**Live mode** — drop `--mock`. Pick a provider with `--provider` (default `anthropic`):

```bash
# Anthropic (Claude):
python -m pipeline.run research --input config/categories/event_registration.yaml

# OpenAI (GPT) — uses the Responses API web_search tool:
python -m pipeline.run research --input config/categories/event_registration.yaml --provider openai
python -m pipeline.run generate --research output/<cat>/research.json --provider openai
```

Flags: `--provider {anthropic,openai}` · `--model` (defaults: `claude-sonnet-4-6` /
`gpt-5.1-mini`; pass any model your key has) · `--mock` (fixtures) · `--humanize` (extra
LLM editing pass) · `--check-links` (verify links) · `--no-review` (skip editorial) · `--out DIR`.

## App (optional UI for the content team)

A thin Streamlit front door over the same pipeline — for non-engineers who'd rather click
than run commands. It makes the two gates operable: an editable table of researched facts
(gate 1) and the rendered draft + QA checklist + editorial score (gate 2).

```bash
pip install -r requirements-app.txt
streamlit run app.py
```

Toggle **Mock mode** in the sidebar to demo offline; untoggle to run live. It reuses
`research.run` / `generate.run` / `assemble.run` / `qa.run` directly — no duplicated logic.

## Make a new listicle

Copy `config/categories/event_registration.yaml`, change the keywords, audience, count,
and `house_product`, and run. That's the whole interface — one YAML per article. The
placeholder swap from the brief ("Top X **{category}** Software") = one new file.

## The two human gates

| Gate | When | What the human does | Cost of a miss here |
| --- | --- | --- | --- |
| 1 — verify research | after `research` | confirm the tool list; fix any wrong price/rating in `research.json` | ~30s |
| 2 — editorial sign-off | after `generate` | skim `draft.md` for tone + the intro hook | minutes |

Gate 1 is the important one: facts are the biggest risk, and they're cheapest to fix
before any prose is written.

## QA checks

Hard (fail the build, exit 1): structure present · tool count = headings = table rows ·
table/body consistency · **facts traceable** (no price/rating that isn't in the bundle) ·
meta ≤160 chars · primary keyword in title + H1.
Soft (warn at gate 2): secondary-keyword coverage · AI-tell phrases · sentence-length
variety · link health · title length · **competitor gaps sourced** · no risky absolute
claims · facts freshness.
Plus an **LLM editorial review** at gate 2 — a rubric score (hook, differentiation,
balance, fluff, scannability) with concrete fixes. Skip with `--no-review`.

## Repo map

```
config/
  house_style.yaml            voice, banned phrases, brand, CTA  (retune without code)
  categories/*.yaml           one file per listicle (the standard input)
  batch_example.csv           many keyword sets for the batch command
pipeline/
  schema.py                   data contracts (the fact-carrying bundle)
  llm.py                      LLMClient: Anthropic + OpenAI transports + Mock (fixtures)
  research.py                 stage 1 — grounded intel  -> ResearchBundle
  generate.py                 stage 2 — intro / FAQ / SEO metadata only
  assemble.py                 stage 3 — deterministic template + hyperlinking
  qa.py                       stage 4 — structural + fact + brand-safety + humanization
  run.py                      CLI: research / generate / all / batch  (the two gates)
app.py                        optional Streamlit UI over the same pipeline (the two gates, visual)
tests/test_pipeline.py        offline regression tests (incl. the guardrail)  -> pytest -q
.github/workflows/ci.yml      CI: runs pytest + an offline pipeline smoke test
fixtures/                     synthetic sample data for mock runs
samples/                      example draft.md + qa_report a reviewer can read as-is
output/                       generated drafts + batch summaries (gitignored)
ARCHITECTURE.md               1-page: criteria, design, tradeoffs, what breaks at scale
ALIGNMENT.md                  requirement-by-requirement map to the brief
LOOM.md                       timed shot-list for the walkthrough video
PRECHECK.md                   pre-record / pre-flight checklist for the live demo
```
