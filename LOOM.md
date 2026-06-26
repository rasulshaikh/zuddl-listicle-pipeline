# Loom shot-list (15 min, timed to the brief)

Record in this order. Commands assume `--mock` so it runs offline with no spend;
mention that dropping `--mock` does the same thing live.

## 1 · Problem diagnosis (3 min)
- The brief's real pain: high-volume listicles, and "LLM chat UI gives inconsistent results."
- Name the three failure modes that actually bite for *comparison* content:
  1. hallucinated pricing/ratings (credibility + legal),
  2. structural drift (different shape every run),
  3. AI voice (won't rank/convert).
- State the thesis: separate grounding from writing, make structure code (not a prompt),
  and forbid the writer from inventing facts.

## 2 · System walkthrough (5 min)
- Show the architecture diagram / `ARCHITECTURE.md`: research → gate 1 → generate →
  assemble → QA → gate 2.
- Open `pipeline/schema.py` — the fact-carrying bundle; "frozen after gate 1."
- `pipeline/llm.py` — live (web-search grounded) vs mock behind one interface; show the
  retry/backoff and the editorial-judge method.
- `pipeline/assemble.py` — deterministic template = consistent structure.
- `pipeline/qa.py` — point at `facts_traceable` (the guardrail) and the brand-safety checks.

## 3 · Live demo (5 min)

**Tip:** you can drive this segment either in the terminal (below) or in the Streamlit UI
(`streamlit run app.py`) — the UI makes the two gates more visual for a non-engineer
audience. Pick one and rehearse it.

- Gate 1:
  `python -m pipeline.run research --input config/categories/event_registration.yaml --mock`
  Open `output/.../research.json`, point out pricing/ratings/sources a human verifies here.
- Gate 2:
  `python -m pipeline.run generate --research output/.../research.json --mock`
  Show the all-green QA, the editorial review score, and open `draft.md` next to the
  reference Zuddl post — same anatomy.
- Scale:
  `python -m pipeline.run batch --csv config/batch_example.csv --mock`
  Open `output/batch_*/SUMMARY.md` — the triage queue. "This is the high-volume answer."

## 4 · Troubleshooting (2 min)
- Break a fact on purpose to show the guardrail fire:
  `pytest -q tests/test_pipeline.py::test_facts_guardrail_blocks_hallucinated_numbers -q`
  (or live-edit a rating into the draft and re-run QA).
- Show it FAILs `facts_traceable`, sets hard_fail, and `generate` would exit 1 — a
  hallucinated number cannot reach publish.
- Mention the other real-world failure handled: web search disabled in the Console →
  a clear, actionable error instead of a stack trace.
