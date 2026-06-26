"""Stage 2 - Generation.

Produces only the *connective* editorial content: the intro hook, the FAQ, and
the SEO metadata. The per-tool bullets, pricing, ratings and the buyer's-guide
dimensions are facts that already live in the (human-approved) bundle, so they
are NOT regenerated here - that is the guardrail against hallucinated facts.
"""
from __future__ import annotations

from .llm import LLMClient
from .schema import GeneratedSections, ResearchBundle


def run(client: LLMClient, bundle: ResearchBundle, house_style: dict, humanize: bool = False) -> GeneratedSections:
    dims = [{"name": d.name, "why": d.why} for d in bundle.dimensions]
    top_tools = [t.name for t in bundle.tools[:3]]

    intro = client.write_section("intro", {
        "category": bundle.category_label, "audience": bundle.audience,
        "year": bundle.year, "dimensions": dims,
    })["markdown"]

    faq = client.write_section("faq", {
        "category": bundle.category_label, "audience": bundle.audience,
        "top_tools": top_tools,
    })["markdown"]

    meta = client.write_section("seo_meta", {
        "category": bundle.category_label, "audience": bundle.audience,
        "year": bundle.year, "count": len(bundle.tools),
    })

    if humanize:
        banned = house_style.get("banned_phrases", [])
        intro = client.edit_humanize(intro, banned)
        faq = client.edit_humanize(faq, banned)

    return GeneratedSections(
        title=meta["title"],
        meta_description=meta["meta_description"],
        slug=meta["slug"],
        intro_md=intro,
        faq_md=faq,
    )
