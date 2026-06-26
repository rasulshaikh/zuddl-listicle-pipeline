"""Stage 1 - Research & intel gathering.

Discovers the tool set, gathers grounded facts for each, derives the buyer's
guide dimensions, and returns a ResearchBundle. This is the only stage that
touches the open web. Its output is what a human reviews at gate 1.
"""
from __future__ import annotations

from datetime import date

from .llm import LLMClient
from .schema import Dimension, ResearchBundle, ToolProfile


def run(client: LLMClient, inp: dict, house_style: dict, mode: str, model: str) -> ResearchBundle:
    house = inp["house_product"]
    category = inp["category_label"]
    audience = inp["audience"]
    count = inp["tool_count"]

    names = client.discover_tools(category, audience, count, house)

    profiles: list[ToolProfile] = []
    for name in names:
        raw = client.research_tool(name, category, audience, is_house=(name == house))
        try:
            profiles.append(ToolProfile(**raw))
        except Exception as e:  # one bad tool shouldn't sink the run
            print(f"  ! could not parse profile for {name}: {e} - inserting placeholder")
            profiles.append(ToolProfile(name=name, is_house=(name == house),
                                        gaps=["RESEARCH FAILED - fill in manually"]))

    dims = [Dimension(**d) for d in client.derive_dimensions(
        category, audience, inp.get("secondary_keywords", []))]

    return ResearchBundle(
        primary_keyword=inp["primary_keyword"],
        secondary_keywords=inp.get("secondary_keywords", []),
        category_label=category,
        audience=audience,
        year=inp["year"],
        tools=profiles,
        dimensions=dims,
        mode=mode,
        model=model,
        researched_at=date.today().isoformat(),
    )
