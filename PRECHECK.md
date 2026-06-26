# Pre-flight checklist (before you record / submit)

**Golden rule:** do the slow, flaky parts (anything that hits web search) *before* you
hit record. Live research makes 7+ searches and takes a couple of minutes — pre-bake it
so on camera you only run the fast, reliable steps. `--mock` is your panic button: the
whole pipeline runs offline and identical, so you're never stuck.

---

## T-5 min · set up and verify (off camera)

```bash
pip install -r requirements.txt

# the pipeline auto-loads .env (python-dotenv); confirm the key is set:
grep ANTHROPIC_API_KEY .env
```

- [ ] Enable **Web search** in the Claude Console (workspace capability settings). The API
      `web_search` tool is **off by default** and research will return a 400 without it.
- [ ] Offline smoke test (no spend, no network):

```bash
python -m pytest -q                                                    # -> 4 passed
python -m pipeline.run all --input config/categories/event_registration.yaml --mock   # -> all green
```

## T-3 min · one cheap live call to confirm key + web search actually work

```bash
# (a) basic call — tests key + model access:
python -c "import yaml; from pipeline.llm import LiveAnthropicClient; \
hs=yaml.safe_load(open('config/house_style.yaml')); \
c=LiveAnthropicClient('claude-sonnet-4-6', hs); \
print(c._complete('Reply with OK only.', use_search=False))"

# (b) web search — tests the Console toggle (a 400 here = search still off):
python -c "import yaml; from pipeline.llm import LiveAnthropicClient; \
hs=yaml.safe_load(open('config/house_style.yaml')); \
c=LiveAnthropicClient('claude-sonnet-4-6', hs); \
print(c._complete('Search the web: what is the latest iPhone? One line.', use_search=True)[:200])"
```

If the model string is rejected (404), pick one your key has access to and pass it later
with `--model` (e.g. `--model claude-opus-4-8`).

**Using OpenAI instead of Anthropic?** Set `OPENAI_API_KEY` and add `--provider openai` to
every command (default model `gpt-5.1-mini`). Quick check:

```bash
python -c "import yaml; from pipeline.llm import LiveOpenAIClient; \
hs=yaml.safe_load(open('config/house_style.yaml')); \
c=LiveOpenAIClient('gpt-5.1-mini', hs); \
print(c._complete('Search the web: latest iPhone? One line.', use_search=True)[:200])"
```

If your account only has older models, use `--model gpt-4o` and the client's
`web_search_preview` fallback (set `web_search_type='web_search_preview'` when constructing
the client, or just use a GPT-5.x model which supports `web_search`).

## T-2 min · pre-bake the slow part so the demo is instant

```bash
# Live research now (the slow, searchy step). Leaves research.json ready to open on camera.
python -m pipeline.run research --input config/categories/event_registration.yaml
```

Optionally pre-run a **live** batch once too, so you have a real `SUMMARY.md` to show:
`python -m pipeline.run batch --csv config/batch_example.csv`.

---

## On camera · the sequence (all fast now)

1. **Gate 1.** Open the pre-baked `output/event-registration-and-ticketing-software/research.json`.
   Point at pricing / ratings / **sources** a human verifies here.
2. **Gate 2 (live, fast — no search):**
   ```bash
   python -m pipeline.run generate --research output/event-registration-and-ticketing-software/research.json
   ```
   Show the all-green QA, the editorial score, then open `draft.md` next to the real
   Zuddl post — same anatomy.
3. **Scale:**
   ```bash
   python -m pipeline.run batch --csv config/batch_example.csv --mock
   ```
   Open `output/batch_*/SUMMARY.md` — the triage queue. "This is the high-volume answer."
4. **Troubleshooting (deterministic, won't flake)** — show the guardrail catch a hallucination:
   ```bash
   python - <<'PY'
   import yaml, json
   from pathlib import Path
   from pipeline.schema import ResearchBundle, GeneratedSections
   from pipeline import assemble, qa
   hs = yaml.safe_load(open('config/house_style.yaml'))
   b = ResearchBundle.model_validate_json(Path('output/event-registration-and-ticketing-software/research.json').read_text())
   s = json.loads(Path('fixtures/event_registration_software/sections.json').read_text())
   sec = GeneratedSections(title=s['seo_meta']['title'], meta_description=s['seo_meta']['meta_description'],
                           slug=s['seo_meta']['slug'], intro_md=s['intro']['markdown'], faq_md=s['faq']['markdown'])
   md = assemble.run(b, sec, hs).replace('more than a sign-up form.',
        'more than a sign-up form. Rated 4.9/5 from $4,999/year.', 1)
   r = qa.run(md, b, sec, hs)
   print([(c.name, c.status, c.detail) for c in r.checks if c.name == 'facts_traceable'])
   print('hard_fail =', r.hard_fail)
   PY
   ```
   Prints `facts_traceable ... fail ... ['$4,999', '4.9/5']` and `hard_fail = True`.
   Narrate: "a fabricated number can't reach publish."

---

## If something breaks live (safety net)

- Any live error → the client auto-retries; if it persists, add `--mock` and say mock
  proves the mechanics.
- A `web_search` 400 → that's the Console toggle. The clear error message is itself a nice
  "good error handling" beat — enable it and rerun.
- Keep the pre-baked `output/` from your successful live run open in a tab as a fallback.

## Two pro tips
- Rehearse the exact command order once so there's no fumbling.
- `--mock` runs everything offline and identical — it's the panic button if a live call stalls.
