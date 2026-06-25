"""LLM access layer.

Two implementations behind one interface:
  - LiveAnthropicClient: real Claude calls. Research uses the server-side
    web_search tool so facts are grounded + carry citations.
  - MockClient: reads fixtures from disk. Lets you run the whole pipeline with
    zero API spend and no network — for tests, demos, and offline development.

Keeping this behind an interface is what makes the system testable and what
lets the rest of the pipeline stay ignorant of *how* text gets produced.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Protocol


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def extract_json(text: str):
    """Pull the first JSON object/array out of a model response.

    Models sometimes wrap JSON in ```fences``` or add a sentence of preamble
    even when told not to. We scan for the first balanced {...} or [...].
    """
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = next((i for i, c in enumerate(text) if c in "{["), -1)
    if start == -1:
        raise ValueError(f"No JSON found in response: {text[:200]!r}")
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            esc = (c == "\\") and not esc
            if c == '"' and not esc:
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("Unbalanced JSON in response")


class LLMClient(Protocol):
    def discover_tools(self, category: str, audience: str, count: int, house: str) -> List[str]: ...
    def research_tool(self, name: str, category: str, audience: str, is_house: bool) -> dict: ...
    def derive_dimensions(self, category: str, audience: str, secondary_kws: List[str]) -> List[dict]: ...
    def write_section(self, task: str, context: dict) -> dict: ...
    def edit_humanize(self, text: str, banned: List[str]) -> str: ...
    def score_editorial(self, markdown: str, bundle) -> dict: ...


# --------------------------------------------------------------------------- #
# live client
# --------------------------------------------------------------------------- #
class LiveAnthropicClient:
    WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 6}

    def __init__(self, model: str, house_style: dict, api_key: str | None = None):
        import anthropic  # lazy: mock runs don't need the SDK installed
        self._anthropic = anthropic
        self.model = model
        self.house = house_style
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "web_searches": 0}

    # --- low-level calls ---------------------------------------------------- #
    def _complete(self, prompt: str, *, use_search: bool, max_tokens: int = 2048) -> str:
        """One completion. Loops on pause_turn so long searches don't get cut off."""
        tools = [self.WEB_SEARCH_TOOL] if use_search else []
        messages = [{"role": "user", "content": prompt}]
        for _ in range(4):
            resp = self._create(max_tokens=max_tokens, tools=tools, messages=messages)
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        raise RuntimeError("web_search did not converge after repeated pause_turn")

    def _create(self, *, max_tokens, tools, messages):
        """messages.create with retry/backoff on transient errors + usage tracking.

        Handles the real-world constraints this tool will hit at volume:
          - 429 rate limits and 5xx/overloaded -> exponential backoff
          - 400 'web search not enabled' -> a clear, actionable error
        """
        import time
        A = self._anthropic
        delay, resp = 2.0, None
        for attempt in range(5):
            try:
                resp = self.client.messages.create(
                    model=self.model, max_tokens=max_tokens, tools=tools, messages=messages,
                )
                break
            except A.RateLimitError:
                if attempt == 4:
                    raise
                time.sleep(delay); delay *= 2
            except A.APIStatusError as e:
                code = getattr(e, "status_code", None)
                if code == 400 and "web_search" in str(e).lower():
                    raise RuntimeError(
                        "web_search returned 400 — an org admin must enable Web Search in "
                        "the Claude Console before research can run."
                    ) from e
                if code in (500, 503, 529) and attempt < 4:
                    time.sleep(delay); delay *= 2
                else:
                    raise
        u = getattr(resp, "usage", None)
        if u:
            self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
            self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
            stu = getattr(u, "server_tool_use", None)
            if stu:
                self.usage["web_searches"] += getattr(stu, "web_search_requests", 0) or 0
        self.usage["calls"] += 1
        return resp

    def _voice_rules(self) -> str:
        v = self.house.get("voice", [])
        banned = ", ".join(self.house.get("banned_phrases", []))
        return ("Write in this voice: " + " ".join(v) +
                f"\nNever use these AI-tell phrases: {banned}.")

    # --- interface ---------------------------------------------------------- #
    def discover_tools(self, category, audience, count, house) -> List[str]:
        prompt = (
            f"Search the web for the most credible {category} used by {audience} in "
            f"{self.house.get('year', '')}. Return ONLY a JSON array of the {count} best "
            f'tool names as strings, most authoritative first. Always include "{house}". '
            "Use real product names only, no descriptions."
        )
        names = extract_json(self._complete(prompt, use_search=True))
        names = [str(n).strip() for n in names]
        if house not in names:
            names.insert(0, house)
        # house product leads the list
        names = [house] + [n for n in names if n != house]
        return names[:count]

    def research_tool(self, name, category, audience, is_house) -> dict:
        schema = (
            '{"name": str, "one_liner": str, "url": str|null, "pricing": str, '
            '"pricing_url": str|null, "g2_rating": str|null, "g2_url": str|null, '
            '"capterra_rating": str|null, "capterra_url": str|null, '
            '"strengths": [str], "gaps": [str], "best_for": str, "sources": [str]}'
        )
        prompt = (
            f"Research {name} as a {category} for {audience}. Search the web for its "
            "current starting price, G2 rating, Capterra rating, product URL, and what "
            "reviewers say it does well and where it falls short.\n"
            f"Return ONLY a JSON object matching this schema: {schema}\n"
            "Rules: pricing as a short string like 'From $10,000/year' or "
            "'Custom pricing'. Ratings like '4.6/5'. Use null for anything you cannot "
            "verify — never guess a number. strengths: 3-5 concise factual bullet "
            "phrases. gaps: 1-3 honest limitations (yes, even for the vendor's own "
            "product). best_for: one sentence naming the team/use case. sources: the "
            "URLs you actually used.\n" + self._voice_rules()
        )
        data = extract_json(self._complete(prompt, use_search=True, max_tokens=2048))
        data["name"] = name
        data["is_house"] = is_house
        return data

    def derive_dimensions(self, category, audience, secondary_kws) -> List[dict]:
        prompt = (
            f"List the 8 dimensions on which {category} differ most for {audience}. "
            f"Lean on these themes where relevant: {', '.join(secondary_kws)}.\n"
            'Return ONLY a JSON array of objects: {"name": short label, '
            '"why": one sentence describing the failure mode if a team gets it wrong}.\n'
            + self._voice_rules()
        )
        return extract_json(self._complete(prompt, use_search=False))

    def write_section(self, task, context) -> dict:
        if task == "intro":
            prompt = (
                f"Write a 3-paragraph intro for a listicle about {context['category']} "
                f"for {context['audience']} ({context['year']}). Paragraph 1: a concrete, "
                "specific scene of the pain this software solves (no clichés). Paragraph 2: "
                "what this category actually does beyond the obvious. Paragraph 3: a "
                "transition that names the comparison dimensions: "
                f"{', '.join(d['name'] for d in context['dimensions'][:5])}. "
                "Return ONLY the markdown, no heading, no preamble.\n" + self._voice_rules()
            )
            return {"markdown": self._complete(prompt, use_search=False).strip()}
        if task == "faq":
            prompt = (
                f"Write 4 FAQ Q&As about {context['category']} for {context['audience']}. "
                f"Reference these tools where natural: {', '.join(context['top_tools'])}. "
                "Format each as '**Q: ...**' on its own line then the answer paragraph. "
                "Answers 2-3 sentences, specific, no fluff. Return ONLY markdown.\n"
                + self._voice_rules()
            )
            return {"markdown": self._complete(prompt, use_search=False).strip()}
        if task == "seo_meta":
            prompt = (
                f"Write SEO metadata for a listicle of the {context['count']} best "
                f"{context['category']} for {context['audience']} ({context['year']}).\n"
                'Return ONLY JSON: {"title": str (<=60 chars, include the count, the '
                'category, and the year), "meta_description": str (<=158 chars, mention '
                'the comparison angle), "slug": str (kebab-case, no year)}.'
            )
            return extract_json(self._complete(prompt, use_search=False, max_tokens=512))
        raise ValueError(f"unknown task: {task}")

    def edit_humanize(self, text, banned) -> str:
        prompt = (
            "Lightly edit this text to read like a human wrote it: vary sentence length, "
            "cut hedging and filler, and remove any of these phrases: "
            f"{', '.join(banned)}. Preserve every fact, number, link and the markdown "
            "structure exactly. Return ONLY the edited markdown.\n\n" + text
        )
        return self._complete(prompt, use_search=False, max_tokens=2048).strip()

    def score_editorial(self, markdown, bundle) -> dict:
        prompt = (
            "You are a skeptical senior B2B content editor. Score this listicle draft "
            "0-100 on: intro hook quality (no clichés), genuinely differentiated 'Best "
            "for' lines across tools, balanced and fair competitor coverage, absence of "
            "fluff, and scannability.\n"
            'Return ONLY JSON: {"score": int, "verdict": str (<=12 words), '
            '"issues": [str] (up to 4 concrete, actionable fixes; [] if none)}.\n\n'
            + markdown[:8000]
        )
        return extract_json(self._complete(prompt, use_search=False, max_tokens=600))


# --------------------------------------------------------------------------- #
# mock client
# --------------------------------------------------------------------------- #
class MockClient:
    """Serves fixtures so the pipeline runs offline. Same interface as live."""

    def __init__(self, fixtures_dir: str | Path):
        self.dir = Path(fixtures_dir)
        self._research = json.loads((self.dir / "research.json").read_text())
        self._sections = json.loads((self.dir / "sections.json").read_text())
        self._by_name = {t["name"]: t for t in self._research["tools"]}

    def discover_tools(self, category, audience, count, house):
        names = [t["name"] for t in self._research["tools"]]
        return [house] + [n for n in names if n != house]

    def research_tool(self, name, category, audience, is_house):
        prof = dict(self._by_name[name])
        prof["is_house"] = is_house
        return prof

    def derive_dimensions(self, category, audience, secondary_kws):
        return self._research["dimensions"]

    def write_section(self, task, context):
        if task == "seo_meta":
            fixture = self._sections["seo_meta"]
            # Keep the curated metadata for the fixture's own category; derive a
            # coherent title/slug for any other keyword so batch rows stay valid.
            if context["category"].lower() == self._research["category_label"].lower():
                return fixture
            small = {"and", "or", "for", "the", "of", "in", "on", "to", "with"}
            cat, n, yr = context["category"], context["count"], context["year"]
            titled = " ".join(w if w in small else w.capitalize() for w in cat.split())
            meta = (f"Compare the {n} best {cat} for {context['audience']} in {yr} "
                    "on the criteria that matter most.")
            return {
                "title": f"{n} Best {titled} ({yr})"[:60],
                "meta_description": meta[:160],
                "slug": "best-" + re.sub(r"[^a-z0-9]+", "-", cat.lower()).strip("-"),
            }
        return self._sections[task]

    def edit_humanize(self, text, banned):
        return text  # mock: no-op

    def score_editorial(self, markdown, bundle):
        return {
            "score": 88,
            "verdict": "Publish-ready with minor polish.",
            "issues": [
                "Name a specific CRM in intro paragraph 2 to sharpen specificity.",
                "Differentiate the Cvent vs Bizzabo 'Best for' lines more on company size.",
            ],
        }
