# Alignment to the brief

A direct map from what Zuddl asked for to where it lives in this system.

## Task requirements

| Requirement | How it's met | Where |
| --- | --- | --- |
| Accept primary KW **and** secondary KWs as input | One YAML per listicle; the `{category}` placeholder swap = one new file | `config/categories/*.yaml` |
| Gather category tools + comparison intel | Tool discovery + per-tool grounded research via Claude's web search (pricing, G2/Capterra, strengths, gaps, source URLs) | `pipeline/research.py`, `pipeline/llm.py` |
| Identify human intervention point(s) | Two gates: **(1) verify research** before any writing, **(2) editorial sign-off** before publish | `pipeline/run.py` (the `research` and `generate` commands stop at each) |
| Consistent, publishable drafts | Deterministic template (structure can't drift) + hyperlinking + SEO metadata | `pipeline/assemble.py` |
| Title / formatting / structure / flow | House title pattern, fixed section order, prose-first body | `assemble.py`, `config/house_style.yaml` |
| Hyperlinking | Tool names, pricing, and ratings linked from the research bundle | `assemble.py` |
| QA to humanise the writing | Banned AI-phrase scan + sentence-length variety + optional LLM editing pass | `pipeline/qa.py`, `--humanize` |

## "What we're looking for"

| Signal | Evidence |
| --- | --- |
| Problem breakdown + guardrails | `ARCHITECTURE.md` (failure modes ranked); the **fact-traceability** hard check that blocks any price/rating not present in the human-approved bundle (`qa.py`) |
| Pragmatic tool choices | Single vendor (Anthropic web search) = one key, citations, no scraping infra; deterministic templating instead of prompt-only structure |
| Error handling for real constraints | Retry/backoff on 429 + 5xx/overloaded; a clear, actionable error when web search isn't enabled in the Console; per-tool research failure degrades to a flagged placeholder instead of crashing the run (`llm.py`, `research.py`) |
| Assessment of what breaks at scale | Dedicated section in `ARCHITECTURE.md` (review-data fragility, price staleness, template-sameness SEO risk, cost/rate limits) |
| Engineering rigor | Offline-runnable via a mock client; committed `pytest` suite that proves the guardrail; usage/cost logging per run |

## How the output matches the reference listicle

The reference (`zuddl.com/blog/best-mobile-event-apps-b2b-conferences`) and `samples/sample-draft.md` share the same anatomy: SEO frontmatter → scene-setting intro that frames "basic tool vs operational layer" → linked quick-comparison table → per-tool sections (one-liner, **What it does well**, **Where it has gaps**, **Pricing**, **Ratings**, **Best for**) → buyer's-guide with failure-mode lines → FAQ → CTA. The house product is placed first and still lists an honest gap — the credibility pattern the reference uses.

## Beyond the ask (elite extensions)

| Extra | Why it matters |
| --- | --- |
| **Batch mode** (`batch --csv`) | The brief's real pain is *high-volume* content. One spreadsheet → many drafts + a triage `SUMMARY.md`; the human gate becomes a review queue, so only WARN/FAIL rows need attention. |
| **Streamlit UI** (`app.py`) | A human-friendly front door for the content team: edit researched facts in a table (gate 1), then review the draft + QA + editorial score (gate 2). Thin layer over the same pipeline functions — no duplicated logic. |
| **Multi-provider** (`--provider`) | One `LLMClient` interface, two transports — Anthropic (Claude) and OpenAI (GPT, Responses API web_search). Prompt logic is shared, so swapping providers changes neither the pipeline nor the prompts. Proves the abstraction earns its keep. |
| **LLM editorial review** | A rubric score + concrete fixes at gate 2 — automated senior-editor judgment, not just regex. |
| **Brand-safety checks** | For a vendor publishing competitor comparisons: every competitor critique must be sourced, and risky absolute claims ("the only", "guaranteed") are flagged. |
| **Facts freshness** | Prices and ratings drift; each research bundle is date-stamped and QA warns when it goes stale. |
| **CI + tests** | GitHub Actions runs the `pytest` suite and an offline pipeline smoke test on every push. |
| **Cost observability** | Per-run token and web-search counts, so spend is visible when running at volume. |
| **Generality proof** | Three category inputs shipped (registration, mobile apps, lead capture) — the "swap the placeholder" claim, demonstrated. |

