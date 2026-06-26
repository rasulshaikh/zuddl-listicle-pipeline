"""Regression tests. Run: pytest -q

These exercise the whole pipeline offline via the MockClient (no API, no
network) and lock in the two properties that matter most: structure is always
valid, and a hallucinated number can never reach a published draft.
"""
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
                return {"missing": "the required name field"}
            return super().research_tool(name, category, audience, is_house)

    bundle = research.run(FlakyClient(FIX), inp, hs, "mock", "")
    assert len(bundle.tools) == inp["tool_count"]
    placeholder = next(t for t in bundle.tools if t.name == "Bizzabo")
    assert placeholder.gaps == ["RESEARCH FAILED — fill in manually"]


def test_qa_hard_fails_on_missing_section():
    hs, _, bundle, sec, md = _build()
    tampered = md.replace("## Quick comparison", "## Snapshot", 1)
    report = qa.run(tampered, bundle, sec, hs)
    assert report.hard_fail
    sp = next(c for c in report.checks if c.name == "structure_present")
    assert sp.status == "fail"
