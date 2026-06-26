"""Regression tests. Run: pytest -q

These exercise the whole pipeline offline via the MockClient (no API, no
network) and lock in the two properties that matter most: structure is always
valid, and a hallucinated number can never reach a published draft.
"""
import time
from pathlib import Path

import yaml

from pipeline import assemble, generate, qa, research
from pipeline.llm import MockClient, extract_json

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures" / "event_registration_software"


def _build():
    hs = yaml.safe_load((ROOT / "config" / "house_style.yaml").read_text())
    inp = yaml.safe_load((ROOT / "config" / "categories" / "event_registration.yaml").read_text())
    client = MockClient(FIX)
    bundle = research.run(client, inp, hs, "mock", "")
    sec = generate.run(client, bundle, hs)
    md = assemble.run(bundle, sec, hs)
    return hs, inp, bundle, sec, md


def test_pipeline_passes_all_hard_checks():
    hs, inp, bundle, sec, md = _build()
    report = qa.run(md, bundle, sec, hs)
    assert not report.hard_fail, [c for c in report.checks if c.status == "fail"]


def test_house_product_is_first_and_flagged():
    _, inp, bundle, _, _ = _build()
    assert len(bundle.tools) == inp["tool_count"]
    assert bundle.tools[0].name == inp["house_product"]
    assert bundle.tools[0].is_house


def test_facts_guardrail_blocks_hallucinated_numbers():
    hs, _, bundle, sec, md = _build()
    anchor = "more than a sign-up form."
    assert anchor in md
    tampered = md.replace(anchor, anchor + " Rated 4.9/5 and priced from $4,999/year.", 1)
    report = qa.run(tampered, bundle, sec, hs)
    assert report.hard_fail
    ft = next(c for c in report.checks if c.name == "facts_traceable")
    assert ft.status == "fail" and "4.9/5" in ft.detail


def test_extract_json_handles_fences_and_preamble():
    assert extract_json('Here you go:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('[1, 2, 3]') == [1, 2, 3]


def test_research_failure_on_one_tool_falls_back_to_placeholder():
    hs = yaml.safe_load((ROOT / "config" / "house_style.yaml").read_text())
    inp = yaml.safe_load((ROOT / "config" / "categories" / "event_registration.yaml").read_text())

    class FlakyClient(MockClient):
        def research_tool(self, name, category, audience, is_house):
            if name == "Bizzabo":
                return {}                          # missing the required "name" field
            return super().research_tool(name, category, audience, is_house)

    bundle = research.run(FlakyClient(FIX), inp, hs, "mock", "")
    assert len(bundle.tools) == inp["tool_count"]
    placeholder = next(t for t in bundle.tools if t.name == "Bizzabo")
    assert placeholder.gaps == ["RESEARCH FAILED - fill in manually"]


def test_research_exception_on_one_tool_falls_back_to_placeholder():
    hs = yaml.safe_load((ROOT / "config" / "house_style.yaml").read_text())
    inp = yaml.safe_load((ROOT / "config" / "categories" / "event_registration.yaml").read_text())

    class ExplodingClient(MockClient):
        def research_tool(self, name, category, audience, is_house):
            if name == "Swoogo":
                raise RuntimeError("simulated API failure")
            return super().research_tool(name, category, audience, is_house)

    bundle = research.run(ExplodingClient(FIX), inp, hs, "mock", "")
    assert len(bundle.tools) == inp["tool_count"]
    placeholder = next(t for t in bundle.tools if t.name == "Swoogo")
    assert placeholder.gaps == ["RESEARCH FAILED - fill in manually"]
    others = [t for t in bundle.tools if t.name != "Swoogo"]
    assert all(t.gaps != ["RESEARCH FAILED - fill in manually"] for t in others)
    assert bundle.tools[0].name == inp["house_product"]
    assert bundle.tools[0].is_house
    assert [t.name for t in bundle.tools] == ["Zuddl", "Cvent", "Swoogo", "Eventbrite", "Splash", "Bizzabo"]


def test_dimensions_failure_falls_back_to_empty_list_without_sinking_run():
    hs = yaml.safe_load((ROOT / "config" / "house_style.yaml").read_text())
    inp = yaml.safe_load((ROOT / "config" / "categories" / "event_registration.yaml").read_text())

    class NoDimensionsClient(MockClient):
        def derive_dimensions(self, category, audience, secondary_kws):
            raise RuntimeError("simulated API failure")

    bundle = research.run(NoDimensionsClient(FIX), inp, hs, "mock", "")
    assert bundle.dimensions == []
    assert len(bundle.tools) == inp["tool_count"]
    assert all(t.gaps != ["RESEARCH FAILED - fill in manually"] for t in bundle.tools)


def test_research_parallel_results_placed_by_index_under_reversed_completion_order():
    """Regression for the ThreadPoolExecutor refactor in pipeline/research.py.

    MockClient calls are near-instant, so threads tend to finish in
    submission order by coincidence - that would let a by-index placement bug
    (e.g. writing results in completion order instead of using the
    future->index map) slip through undetected. Here every tool's
    research_tool call sleeps, with delays *reversed* relative to submission
    order (the first-submitted tool finishes last), so as_completed() yields
    futures in an order that is the opposite of `names`. If profiles[i] were
    ever assigned by completion order rather than the captured submission
    index, this would scramble bundle.tools.
    """
    hs = yaml.safe_load((ROOT / "config" / "house_style.yaml").read_text())
    inp = yaml.safe_load((ROOT / "config" / "categories" / "event_registration.yaml").read_text())
    expected_order = ["Zuddl", "Cvent", "Swoogo", "Eventbrite", "Splash", "Bizzabo"]

    class StaggeredClient(MockClient):
        def research_tool(self, name, category, audience, is_house):
            # Reverse delay schedule: first name in discover_tools() order
            # sleeps longest, last name returns first.
            delay = 0.05 * (len(expected_order) - expected_order.index(name))
            time.sleep(delay)
            return super().research_tool(name, category, audience, is_house)

    bundle = research.run(StaggeredClient(FIX), inp, hs, "mock", "")

    assert [t.name for t in bundle.tools] == expected_order
    for t in bundle.tools:
        assert t.gaps != ["RESEARCH FAILED - fill in manually"]
    assert bundle.tools[0].is_house
    assert all(not t.is_house for t in bundle.tools[1:])
    # dims_future still resolves correctly even though it was submitted
    # alongside (and outlasted by) the slowest per-tool future.
    assert len(bundle.dimensions) > 0


def test_generate_parallel_sections_placed_correctly_under_reversed_completion_order():
    """Regression for the ThreadPoolExecutor refactor in pipeline/generate.py.

    write_section("intro"|"faq"|"seo_meta") are independent futures; a naive
    refactor could accidentally cross-wire which .result() feeds `intro` vs
    `faq` vs `meta` (e.g. via copy-paste or variable reuse), and that bug
    would not necessarily show up just from running the happy path once if
    futures happen to finish in submission order. Here `seo_meta` is made the
    slowest and `intro` the fastest - completion order is the reverse of
    submission order - and each task's distinctive markdown content is
    checked against its own field.
    """
    hs = yaml.safe_load((ROOT / "config" / "house_style.yaml").read_text())
    inp = yaml.safe_load((ROOT / "config" / "categories" / "event_registration.yaml").read_text())
    client = MockClient(FIX)
    bundle = research.run(client, inp, hs, "mock", "")

    delays = {"intro": 0.0, "faq": 0.05, "seo_meta": 0.1}

    class StaggeredClient(MockClient):
        def write_section(self, task, context):
            time.sleep(delays.get(task, 0.0))
            return super().write_section(task, context)

    sec = generate.run(StaggeredClient(FIX), bundle, hs)

    fixture_intro = MockClient(FIX)._sections["intro"]["markdown"]
    fixture_faq = MockClient(FIX)._sections["faq"]["markdown"]
    fixture_meta = MockClient(FIX)._sections["seo_meta"]

    assert sec.intro_md == fixture_intro
    assert sec.faq_md == fixture_faq
    assert sec.title == fixture_meta["title"]
    assert sec.meta_description == fixture_meta["meta_description"]
    assert sec.slug == fixture_meta["slug"]
    # Cross-check: the three fixtures are textually distinct, so this also
    # rules out any result silently swapping into the wrong field.
    assert sec.intro_md != sec.faq_md
    assert sec.title not in sec.intro_md and sec.title not in sec.faq_md


def test_qa_hard_fails_on_missing_section():
    hs, _, bundle, sec, md = _build()
    tampered = md.replace("## Quick comparison", "## Snapshot", 1)
    report = qa.run(tampered, bundle, sec, hs)
    assert report.hard_fail
    sp = next(c for c in report.checks if c.name == "structure_present")
    assert sp.status == "fail"


def test_facts_guardrail_does_not_false_positive_on_trailing_comma():
    """Regression: PRICE_RE's old `[\\d,]+` character class swallowed a comma
    that immediately follows a price in ordinary prose (e.g. "...$10,000, with
    onboarding included"), producing a token like "$10,000," that can never
    match the bundle's "$10,000/year" - a false hallucination flag on a fact
    that was actually sourced and approved at gate 1.
    """
    hs, _, bundle, sec, md = _build()
    price = bundle.tools[0].pricing
    anchor = "more than a sign-up form."
    tampered = md.replace(anchor, anchor + f" Contracts run {price}, with onboarding included.", 1)
    report = qa.run(tampered, bundle, sec, hs)
    ft = next(c for c in report.checks if c.name == "facts_traceable")
    assert ft.status == "pass", ft.detail


def test_primary_kw_in_title_tolerates_ampersand_and_filler_words():
    """Regression: an LLM title that swaps "and" for "&" (or drops a filler
    word) to fit the <=60-char budget used to hard-fail primary_kw_in_title
    even though every substantive keyword term is present. The check now
    requires the keyword's significant words to appear in the title rather
    than an exact phrase match.
    """
    hs, _, bundle, sec, md = _build()
    sec.title = f"6 Best {bundle.primary_keyword.replace(' and ', ' & ').title()} (2026)"
    report = qa.run(md, bundle, sec, hs)
    pk_check = next(c for c in report.checks if c.name == "primary_kw_in_title")
    assert pk_check.status == "pass", pk_check.detail
