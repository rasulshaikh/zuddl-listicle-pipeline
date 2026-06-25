# Architecture — Listicle Generation Pipeline

## What success and failure look like

A "95% ready" draft is not a quality opinion, so the pipeline encodes it as checks.

**Success (all must hold):**
- **Structure is identical every run** — same sections, same order, same table shape.
- **Zero invented facts** — every price and rating in the prose traces to a research record that a human approved.
- **On-brand voice** — reads like a practitioner, no AI-tell phrases, varied sentence length.
- **Links present and valid** — tools, pricing, and ratings are hyperlinked and resolve.
- **SEO basics** — primary keyword in the H1, title, and meta description; meta ≤160 chars.
- **Cheap and fast enough to run at volume** — a handful of API calls, single human pass.

**Failure modes the design targets (in priority order):**
1. **Hallucinated pricing/ratings** — the credibility/legal killer for comparison content.
2. **Structural drift** — chat UIs give 7 bullets here, 3 there, a missing FAQ next time.
3. **Generic AI voice** — doesn't rank, doesn't convert.
4. **Broken or stale links and prices.**

## Why this design

**Separate grounding from writing.** Research (the only stage that touches the web) produces a structured `ResearchBundle`; generation only writes the *connective* prose (intro, FAQ, metadata). The per-tool bullets, prices, ratings, and buyer's-guide dimensions are facts that live in the bundle and are rendered deterministically. The writer is never handed a blank slate to "recall" a price — that is the core defense against hallucination, enforced by the `facts_traceable` QA check, which fails the build if any number in the draft is absent from the bundle.

**Skeleton in code, not in a prompt.** Assembly is plain templating, so structure can't drift — every article comes out in the exact house format. The LLM fills sections; code arranges them.

**Decompose generation into small, swappable calls.** Intro, FAQ, and metadata are separate prompts, so one weak section is regenerated in isolation rather than re-rolling a 1,700-word monolith.

**Two human gates, the early one load-bearing.** Gate 1 (verify research) is where the highest-risk artifact — the facts — gets a 30-second human check before anything is written; catching a wrong price here is nearly free, catching it post-generation means re-running everything. Gate 2 is a lighter editorial pass on tone and the intro hook. If only one gate were allowed, it would be gate 1.

**An LLM-client interface with a mock implementation.** Live calls (web-search-grounded) and a fixture-backed mock sit behind one interface, which makes the pipeline testable, demoable with zero spend, and runnable offline.

## Tradeoffs made

- **Determinism over flair.** A fixed template guarantees consistency but means every article shares a shape — accepted, because consistency is the product.
- **Anthropic web search over a dedicated SEO/reviews API.** One vendor, one key, citations included, no scraping infrastructure — at the cost of less structured review data than a paid G2/Capterra feed (which has no clean public API anyway).
- **Facts as bullet phrases in research, not free-form generation.** Slightly less "creative" phrasing of strengths/gaps, in exchange for traceability and balance.
- **Human-in-the-loop over fully autonomous.** Slower per article, but the verification gate is what makes the output trustworthy enough to publish under the brand.

## What breaks at scale, and what I'd add with more time

Several scale concerns are **already addressed**: batch mode turns a spreadsheet into many
drafts with a review-queue gate, each bundle carries a facts-freshness timestamp, brand-safety
checks guard competitor comparisons, an LLM editorial review scores each draft, and per-run
token/search usage is logged. The rest is future work:

- **Review data is the fragile dependency.** G2/Capterra block scrapers and have no public API, so ratings come via web search + the human gate. At volume I'd add a cached, periodically-refreshed datastore of tool facts so every listicle reads from one verified source instead of re-fetching.
- **Prices drift; drafts have a shelf life.** Needs a re-crawl cadence and a "facts older than N days" flag.
- **Template sameness is an SEO risk.** 200 near-identical structures can read as thin to search engines. I'd add controlled variation (rotating intro patterns, ordering logic, dimension sets) and a near-duplicate detector across the published set.
- **Web search quality, rate limits, and cost** grow with volume; I'd add caching of tool profiles, retries with backoff, and a per-run cost budget.
- **Link rot and moved pricing pages** — the link checker flags broken links today; at scale I'd schedule it across the whole library, not just per-run.
- **Thin niches** with few tools or sparse review data degrade quality; the pipeline should detect low-confidence research and route those to a human earlier.
- **Tests & observability:** a fixture-based regression suite (the mock client already enables it), plus per-run logging of cost, search count, and QA outcomes.
