"""Stage 4 - QA & humanization checks.

Turns "is this draft good enough to publish?" into a checklist with pass/fail.
HARD checks (structure, fact-traceability, consistency, SEO basics) fail the
run with a non-zero exit code. SOFT checks (AI-tell phrases, sentence variety,
link health) surface as warnings for the human at gate 2.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import List

from .schema import GeneratedSections, ResearchBundle

PRICE_RE = re.compile(r"\$(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
RATING_RE = re.compile(r"\b\d(?:\.\d)?/5\b")
LINK_RE = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+)\)")
SENT_RE = re.compile(r"[^.!?]+[.!?]")


@dataclass
class Check:
    name: str
    status: str            # "pass" | "warn" | "fail" | "skip"
    detail: str = ""
    hard: bool = False


@dataclass
class Report:
    checks: List[Check] = field(default_factory=list)
    read_minutes: int = 0
    word_count: int = 0

    @property
    def hard_fail(self) -> bool:
        return any(c.status == "fail" and c.hard for c in self.checks)

    def add(self, *a, **k):
        self.checks.append(Check(*a, **k))


def run(md: str, bundle: ResearchBundle, sec: GeneratedSections,
        house_style: dict, check_links: bool = False) -> Report:
    r = Report()
    n = len(bundle.tools)
    body = md.lower()
    pk = bundle.primary_keyword.lower()

    r.word_count = len(re.findall(r"\w+", md))
    r.read_minutes = max(1, round(r.word_count / 200))

    # --- structure --------------------------------------------------------- #
    needed = ["## quick comparison", f"## the {n} best", "## what to look for",
              "## questions people ask"]
    missing = [s for s in needed if s not in body]
    r.add("structure_present", "pass" if not missing else "fail",
          "all sections present" if not missing else f"missing: {missing}", hard=True)

    # --- tool count == headings == table rows ------------------------------ #
    h3 = len(re.findall(r"^### ", md, flags=re.MULTILINE))
    table_rows = len(re.findall(r"^\| (?!Tool |[- ]+\|)", md, flags=re.MULTILINE))
    ok = (h3 == n == table_rows)
    r.add("tool_count_matches", "pass" if ok else "fail",
          f"{n} expected · {h3} H3 sections · {table_rows} table rows", hard=True)

    # --- every tool appears in both table and body ------------------------- #
    bad = [t.name for t in bundle.tools if f"### {t.name}".lower() not in body]
    r.add("table_body_consistency", "pass" if not bad else "fail",
          "every tool has a section" if not bad else f"no section for: {bad}", hard=True)

    # --- fact traceability: no invented prices/ratings --------------------- #
    known = " || ".join(x for t in bundle.tools for x in t.known_numbers())
    found = set(PRICE_RE.findall(md)) | set(RATING_RE.findall(md))
    untraceable = sorted(tok for tok in found if tok not in known)
    r.add("facts_traceable", "pass" if not untraceable else "fail",
          "all prices/ratings trace to the research bundle"
          if not untraceable else f"NOT in bundle (possible hallucination): {untraceable}",
          hard=True)

    # --- SEO basics -------------------------------------------------------- #
    # Token-based coverage (ignores stopwords + "event(s)", which is filler in
    # this domain) instead of an exact substring match: a long primary keyword
    # almost never survives verbatim once an LLM has to also fit count/year
    # into a <=60-char title, and "&" for "and" shouldn't count as missing.
    stop = {"for", "and", "the", "of", "in", "on", "to", "a", "an", "with", "event", "events"}

    def _covered(kw: str, text: str) -> bool:
        toks = [w for w in re.findall(r"[a-z]+", kw.lower()) if len(w) > 3 and w not in stop]
        return all(t in text for t in toks) if toks else True

    r.add("meta_description_length",
          "pass" if len(sec.meta_description) <= 160 else "fail",
          f"{len(sec.meta_description)} chars (<=160)", hard=True)
    r.add("title_length", "pass" if len(sec.title) <= 65 else "warn",
          f"{len(sec.title)} chars (<=60 ideal)")
    title_ok = _covered(bundle.primary_keyword, sec.title.lower())
    r.add("primary_kw_in_title", "pass" if title_ok else "fail",
          f'key terms from "{bundle.primary_keyword}" in title (got: "{sec.title}")', hard=True)
    h1_ok = f"# {sec.title}".lower() in body
    r.add("primary_kw_in_h1", "pass" if h1_ok else "fail",
          "H1 matches title" if h1_ok else f'no "# {sec.title}" heading found in body', hard=True)
    r.add("primary_kw_in_intro", "pass" if pk in sec.intro_md.lower() else "warn",
          "keyword used early")

    # --- secondary keyword coverage (the brief mandates secondary KWs) ----- #
    sec_kws = bundle.secondary_keywords
    if sec_kws:
        miss = [k for k in sec_kws if not _covered(k, body)]
        ratio = (len(sec_kws) - len(miss)) / len(sec_kws)
        r.add("secondary_kw_coverage", "pass" if ratio >= 0.6 else "warn",
              f"{len(sec_kws) - len(miss)}/{len(sec_kws)} covered"
              + (f"; missing: {miss}" if miss else ""))

    # --- humanization: AI-tell phrases ------------------------------------- #
    banned = [p for p in house_style.get("banned_phrases", []) if p.lower() in body]
    r.add("no_ai_tells", "pass" if not banned else "warn",
          "no banned phrases" if not banned else f"found: {banned}")

    # --- humanization: sentence-length variety ----------------------------- #
    lens = [len(s.split()) for s in SENT_RE.findall(sec.intro_md + " " + sec.faq_md)]
    sd = statistics.pstdev(lens) if len(lens) > 1 else 0
    r.add("sentence_variety", "pass" if sd >= 4 else "warn",
          f"stdev of sentence length = {sd:.1f} (>=4 reads natural)")

    # --- link health ------------------------------------------------------- #
    urls = sorted(set(LINK_RE.findall(md)))
    if not check_links:
        r.add("links_resolve", "skip", f"{len(urls)} links (run with --check-links)")
    else:
        broken = []
        try:
            import requests
            for u in urls:
                try:
                    if requests.head(u, timeout=10, allow_redirects=True).status_code >= 400:
                        broken.append(u)
                except Exception:
                    broken.append(u)
            r.add("links_resolve", "pass" if not broken else "warn",
                  f"{len(urls)} checked" if not broken else f"broken/unreachable: {broken}")
        except Exception as e:
            r.add("links_resolve", "skip", f"could not check ({e})")

    # --- brand safety: don't publish unsourced criticism of competitors --- #
    unsourced = [t.name for t in bundle.tools if not t.is_house and t.gaps and not t.sources]
    r.add("competitor_gaps_sourced", "pass" if not unsourced else "warn",
          "every competitor critique is sourced"
          if not unsourced else f"gaps without a source: {unsourced}")

    # --- brand safety: risky absolute claims stated as fact ---------------- #
    superl = sorted({m.group(0) for m in re.finditer(
        r"\b(the only|guaranteed|number one|unbeatable|world-class|the leading)\b", body)})
    r.add("no_unverified_superlatives", "pass" if not superl else "warn",
          "no risky absolute claims" if not superl else f"review these claims: {superl}")

    # --- facts freshness: prices/ratings drift ----------------------------- #
    if bundle.researched_at:
        try:
            age = (date.today() - date.fromisoformat(bundle.researched_at)).days
            max_age = house_style.get("facts_max_age_days", 30)
            r.add("facts_freshness", "pass" if age <= max_age else "warn",
                  f"researched {age} day(s) ago (refresh after {max_age})")
        except ValueError:
            pass

    return r
