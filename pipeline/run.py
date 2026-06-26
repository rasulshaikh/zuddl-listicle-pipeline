"""Listicle pipeline CLI.

Two commands mirror the two human gates:

  research   stage 1 -> writes research.json, then STOPS for human review (gate 1)
  generate   stages 2-4 -> writes draft.md + qa_report.md, STOPS for review (gate 2)
  all        runs both back-to-back (skips the pause; for mock demos / CI)
  batch      runs many keyword sets from a CSV -> drafts + a triage SUMMARY.md

Examples
  python -m pipeline.run research --input config/categories/event_registration.yaml --mock
  python -m pipeline.run generate --research output/<slug>/research.json --mock
  python -m pipeline.run all --input config/categories/event_registration.yaml --mock
  python -m pipeline.run batch --csv config/batch_example.csv --mock
  python -m pipeline.run research --input config/categories/event_registration.yaml --provider openai
"""
from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from . import assemble, generate, qa, research
from .llm import LiveAnthropicClient, LiveOpenAIClient, MockClient
from .schema import GeneratedSections, ResearchBundle

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURES = ROOT / "fixtures" / "event_registration_software"


def _load_yaml(p: str) -> dict:
    return yaml.safe_load(Path(p).read_text())


def _house_style() -> dict:
    return _load_yaml(ROOT / "config" / "house_style.yaml")


def _resolve_model(provider: str, model: str | None) -> str:
    if model:
        return model
    return "gpt-5.1-mini" if provider == "openai" else "claude-sonnet-4-6"


def _client(mock: bool, provider: str, model: str, house_style: dict, fixtures: str):
    if mock:
        return MockClient(fixtures)
    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            sys.exit("ERROR: OPENAI_API_KEY not set. Add it to .env or run with --mock.")
        return LiveOpenAIClient(model=model, house_style=house_style, api_key=key)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ERROR: ANTHROPIC_API_KEY not set. Add it to .env or run with --mock.")
    return LiveAnthropicClient(model=model, house_style=house_style, api_key=key)


def _outdir(slug_or_cat: str, override: str | None) -> Path:
    d = Path(override) if override else ROOT / "output" / slug_or_cat.replace(" ", "-")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _print_usage(client):
    """Live client only - shows token + web-search spend for the run."""
    u = getattr(client, "usage", None)
    if not u:
        return
    print(f"\n  usage: {u['calls']} API calls · {u['input_tokens']:,} in / "
          f"{u['output_tokens']:,} out tokens · {u['web_searches']} web searches")


# --------------------------------------------------------------------------- #
def do_research(args) -> ResearchBundle:
    inp, hs = _load_yaml(args.input), _house_style()
    mode = "mock" if args.mock else "live"
    model = _resolve_model(args.provider, args.model)
    client = _client(args.mock, args.provider, model, hs, args.fixtures)

    print(f"[1/1] Researching {inp['tool_count']} {inp['category_label']} ({mode}, {args.provider})...")
    bundle = research.run(client, inp, hs, mode, "" if args.mock else model)

    out = _outdir(inp["category_label"], args.out)
    (out / "research.json").write_text(bundle.model_dump_json(indent=2))

    print(f"\n  {'TOOL':<14}{'PRICING':<26}{'G2':<8}SOURCES")
    for t in bundle.tools:
        star = "*" if t.is_house else " "
        print(f" {star}{t.name:<13}{t.pricing[:24]:<26}{(t.g2_rating or '-'):<8}{len(t.sources)}")
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
    model = _resolve_model(args.provider, args.model)
    client = _client(mode == "mock", args.provider, model, hs, args.fixtures)

    print(f"[1/3] Generating sections ({mode})...")
    sections = generate.run(client, bundle, hs, humanize=args.humanize)
    print("[2/3] Assembling draft...")
    md = assemble.run(bundle, sections, hs)
    print("[3/3] Running QA...")
    report = qa.run(md, bundle, sections, hs, check_links=args.check_links)
    editorial = None if getattr(args, "no_review", False) else client.score_editorial(md, bundle)

    out = _outdir(sections.slug or bundle.category_label, args.out)
    (out / "draft.md").write_text(md)
    _write_report(out / "qa_report.md", report, sections, editorial)

    print(f"\n  QA  ({report.word_count} words · ~{report.read_minutes} min read)")
    for c in report.checks:
        mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "skip"}[c.status]
        print(f"   [{mark}] {c.name:<26} {c.detail}")
    if editorial:
        print(f"\n  editorial review: {editorial['score']}/100 - {editorial['verdict']}")
        for issue in editorial.get("issues", []):
            print(f"    · {issue}")
    print(f"\n  -> {out/'draft.md'}\n  -> {out/'qa_report.md'}")
    _print_usage(client)

    if report.hard_fail:
        print("\n  GATE 2: hard checks FAILED - fix before publishing. (exit 1)")
        return 1
    print("\n  GATE 2 (human): skim draft.md for tone + the intro hook, then publish.")
    return 0


def _write_report(path: Path, report, sections: GeneratedSections, editorial=None):
    lines = [f"# QA report - {sections.title}", "",
             f"- words: {report.word_count}  ·  read time: ~{report.read_minutes} min",
             f"- hard fail: {report.hard_fail}", "", "| check | status | detail |",
             "| --- | --- | --- |"]
    for c in report.checks:
        lines.append(f"| {c.name} | {c.status.upper()} | {c.detail} |")
    if editorial:
        lines += ["", f"## Editorial review - {editorial['score']}/100",
                  f"_{editorial['verdict']}_", ""]
        lines += [f"- {i}" for i in editorial.get("issues", [])] or ["- (no issues flagged)"]
    path.write_text("\n".join(lines) + "\n")


def do_all(args) -> int:
    bundle = do_research(args)
    print("\n--- (gate 1 auto-approved in `all` mode) ---\n")
    return do_generate(args, bundle=bundle)


def do_batch(args) -> int:
    """High-volume path: one row per listicle. Produces drafts + a triage summary.

    The human gate becomes a *review queue* - instead of pausing on every article,
    the run flags which drafts have warnings/failures so a human triages only those.
    """
    hs = _house_style()
    mode = "mock" if args.mock else "live"
    rows = list(csv.DictReader(Path(args.csv).open()))
    if not rows:
        sys.exit("ERROR: CSV has no rows.")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(args.out) if args.out else ROOT / "output" / f"batch_{stamp}"
    base.mkdir(parents=True, exist_ok=True)
    model = _resolve_model(args.provider, args.model)
    client = _client(args.mock, args.provider, model, hs, args.fixtures)

    summary = []
    for i, row in enumerate(rows, 1):
        inp = {
            "primary_keyword": row["primary_keyword"],
            "category_label": row.get("category_label") or row["primary_keyword"],
            "audience": row["audience"],
            "year": int(row["year"]),
            "tool_count": int(row["tool_count"]),
            "house_product": row["house_product"],
            "secondary_keywords": [s.strip() for s in row.get("secondary_keywords", "").split("|") if s.strip()],
        }
        print(f"[{i}/{len(rows)}] {inp['category_label']} ({mode})...")
        try:
            bundle = research.run(client, inp, hs, mode, "" if args.mock else model)
            sections = generate.run(client, bundle, hs, humanize=args.humanize)
            md = assemble.run(bundle, sections, hs)
            report = qa.run(md, bundle, sections, hs, check_links=args.check_links)
            d = base / (sections.slug or inp["category_label"].replace(" ", "-"))
            d.mkdir(parents=True, exist_ok=True)
            (d / "draft.md").write_text(md)
            (d / "research.json").write_text(bundle.model_dump_json(indent=2))
            _write_report(d / "qa_report.md", report, sections)
            warns = sum(c.status == "warn" for c in report.checks)
            fails = sum(c.status == "fail" for c in report.checks)
            status = "FAIL" if report.hard_fail else ("WARN" if warns else "PASS")
            summary.append((inp["primary_keyword"], len(bundle.tools), report.word_count,
                            status, fails, warns, d.name))
        except Exception as e:
            summary.append((inp["primary_keyword"], 0, 0, "ERROR", 1, 0, str(e)[:48]))

    lines = ["# Batch run summary", "",
             f"- generated: {stamp}  ·  rows: {len(rows)}  ·  mode: {mode}", "",
             "| keyword | tools | words | QA | hard fails | warnings | output |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for kw, ntools, w, st, f, wn, name in summary:
        lines.append(f"| {kw} | {ntools} | {w} | {st} | {f} | {wn} | {name} |")
    (base / "SUMMARY.md").write_text("\n".join(lines) + "\n")

    print(f"\n  {'KEYWORD':<46}{'QA':<6}WORDS")
    for kw, ntools, w, st, f, wn, name in summary:
        print(f"   {kw[:44]:<46}{st:<6}{w}")
    print(f"\n  -> {base/'SUMMARY.md'}")
    _print_usage(client)
    print("  Review queue (gate 2 at scale): triage any WARN / FAIL / ERROR rows.")
    return 1 if any(s[3] in ("FAIL", "ERROR") for s in summary) else 0


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

    pb = sub.add_parser("batch", help="many keyword sets from a CSV -> drafts + SUMMARY.md")
    pb.add_argument("--csv", required=True)
    pb.add_argument("--out")
    pb.set_defaults(func=do_batch)

    for x in (pr, pg, pa, pb):
        x.add_argument("--mock", action="store_true", help="use fixtures, no API/network")
        x.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic",
                       help="LLM provider (default anthropic)")
        x.add_argument("--model", default=None, help="model override (default depends on provider)")
        x.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
        x.add_argument("--humanize", action="store_true", help="extra LLM editing pass")
        x.add_argument("--check-links", action="store_true", help="verify links resolve")
        x.add_argument("--no-review", action="store_true", help="skip the LLM editorial review")

    args = p.parse_args()
    rc = args.func(args)
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
