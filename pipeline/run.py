"""Listicle pipeline CLI.

Two commands mirror the two human gates:

  research   stage 1 -> writes research.json, then STOPS for human review (gate 1)
  generate   stages 2-4 -> writes draft.md + qa_report.md, STOPS for review (gate 2)
  all        runs both back-to-back (skips the pause; for mock demos / CI)

Examples
  python -m pipeline.run research --input config/categories/event_registration.yaml --mock
  python -m pipeline.run generate --research output/<slug>/research.json --mock
  python -m pipeline.run all --input config/categories/event_registration.yaml --mock
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from . import assemble, generate, qa, research
from .llm import LiveAnthropicClient, MockClient
from .schema import GeneratedSections, ResearchBundle

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURES = ROOT / "fixtures" / "event_registration_software"


def _load_yaml(p: str) -> dict:
    return yaml.safe_load(Path(p).read_text())


def _house_style() -> dict:
    return _load_yaml(ROOT / "config" / "house_style.yaml")


def _client(mock: bool, model: str, house_style: dict, fixtures: str):
    if mock:
        return MockClient(fixtures)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ERROR: ANTHROPIC_API_KEY not set. Add it to .env or run with --mock.")
    return LiveAnthropicClient(model=model, house_style=house_style, api_key=key)


def _outdir(slug_or_cat: str, override: str | None) -> Path:
    d = Path(override) if override else ROOT / "output" / slug_or_cat.replace(" ", "-")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _print_usage(client):
    """Live client only — shows token + web-search spend for the run."""
    u = getattr(client, "usage", None)
    if not u:
        return
    print(f"\n  usage: {u['calls']} API calls · {u['input_tokens']:,} in / "
          f"{u['output_tokens']:,} out tokens · {u['web_searches']} web searches")


# --------------------------------------------------------------------------- #
def do_research(args) -> ResearchBundle:
    inp, hs = _load_yaml(args.input), _house_style()
    mode = "mock" if args.mock else "live"
    client = _client(args.mock, args.model, hs, args.fixtures)

    print(f"[1/1] Researching {inp['tool_count']} {inp['category_label']} ({mode})...")
    bundle = research.run(client, inp, hs, mode, "" if args.mock else args.model)

    out = _outdir(inp["category_label"], args.out)
    (out / "research.json").write_text(bundle.model_dump_json(indent=2))

    print(f"\n  {'TOOL':<14}{'PRICING':<26}{'G2':<8}SOURCES")
    for t in bundle.tools:
        star = "*" if t.is_house else " "
        print(f" {star}{t.name:<13}{t.pricing[:24]:<26}{(t.g2_rating or '—'):<8}{len(t.sources)}")
    print(f"\n  -> {out/'research.json'}")
    _print_usage(client)
    print("\n  GATE 1 (human): open research.json, verify pricing/ratings/tools, edit as")
    print("  needed, then:  python -m pipeline.run generate --research "
          f"{out/'research.json'}{' --mock' if args.mock else ''}")
    return bundle


def do_generate(args, bundle: ResearchBundle | None = None) -> int:
    hs = _house_style()
    if bundle is None:
        bundle = ResearchBundle.model_validate_json(Path(args.research).read_text())
    mode = bundle.mode
    client = _client(mode == "mock", args.model, hs, args.fixtures)

    print(f"[1/3] Generating sections ({mode})...")
    sections = generate.run(client, bundle, hs, humanize=args.humanize)
    print("[2/3] Assembling draft...")
    md = assemble.run(bundle, sections, hs)
    print("[3/3] Running QA...")
    report = qa.run(md, bundle, sections, hs, check_links=args.check_links)

    out = _outdir(sections.slug or bundle.category_label, args.out)
    (out / "draft.md").write_text(md)
    _write_report(out / "qa_report.md", report, sections)

    print(f"\n  QA  ({report.word_count} words · ~{report.read_minutes} min read)")
    for c in report.checks:
        mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "skip"}[c.status]
        print(f"   [{mark}] {c.name:<26} {c.detail}")
    print(f"\n  -> {out/'draft.md'}\n  -> {out/'qa_report.md'}")
    _print_usage(client)

    if report.hard_fail:
        print("\n  GATE 2: hard checks FAILED — fix before publishing. (exit 1)")
        return 1
    print("\n  GATE 2 (human): skim draft.md for tone + the intro hook, then publish.")
    return 0


def _write_report(path: Path, report, sections: GeneratedSections):
    lines = [f"# QA report — {sections.title}", "",
             f"- words: {report.word_count}  ·  read time: ~{report.read_minutes} min",
             f"- hard fail: {report.hard_fail}", "", "| check | status | detail |",
             "| --- | --- | --- |"]
    for c in report.checks:
        lines.append(f"| {c.name} | {c.status.upper()} | {c.detail} |")
    path.write_text("\n".join(lines) + "\n")


def do_all(args) -> int:
    bundle = do_research(args)
    print("\n--- (gate 1 auto-approved in `all` mode) ---\n")
    return do_generate(args, bundle=bundle)


# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(prog="pipeline.run")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = dict()

    pr = sub.add_parser("research", help="stage 1 -> research.json (gate 1)")
    pr.add_argument("--input", required=True)
    pr.add_argument("--out")
    pr.set_defaults(func=do_research)

    pg = sub.add_parser("generate", help="stages 2-4 -> draft.md + qa (gate 2)")
    pg.add_argument("--research", required=True)
    pg.add_argument("--out")
    pg.set_defaults(func=do_generate)

    pa = sub.add_parser("all", help="research + generate back-to-back")
    pa.add_argument("--input", required=True)
    pa.add_argument("--out")
    pa.set_defaults(func=do_all)

    for x in (pr, pg, pa):
        x.add_argument("--mock", action="store_true", help="use fixtures, no API/network")
        x.add_argument("--model", default="claude-sonnet-4-6")
        x.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
        x.add_argument("--humanize", action="store_true", help="extra LLM editing pass")
        x.add_argument("--check-links", action="store_true", help="verify links resolve")

    args = p.parse_args()
    rc = args.func(args)
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
