"""Stage 1 - Research & intel gathering.

Discovers the tool set, gathers grounded facts for each, derives the buyer's
guide dimensions, and returns a ResearchBundle. This is the only stage that
touches the open web. Its output is what a human reviews at gate 1.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Callable

from .llm import LLMClient
from .schema import Dimension, ResearchBundle, ToolProfile

MAX_WORKERS = 4


def _research_one(client: LLMClient, name: str, category: str, audience: str, is_house: bool) -> ToolProfile:
    """Research + parse one tool. Any failure (API call or schema) becomes a
    placeholder instead of sinking the whole run."""
    try:
        raw = client.research_tool(name, category, audience, is_house=is_house)
        return ToolProfile(**raw)
    except Exception as e:
        print(f"  ! could not research {name}: {e} - inserting placeholder")
        return ToolProfile(name=name, is_house=is_house,
                            gaps=["RESEARCH FAILED - fill in manually"])


def run(client: LLMClient, inp: dict, house_style: dict, mode: str, model: str,
        on_progress: Callable[[str], None] | None = None) -> ResearchBundle:
    house = inp["house_product"]
    category = inp["category_label"]
    audience = inp["audience"]
    count = inp["tool_count"]
    secondary_kws = inp.get("secondary_keywords", [])
    notify = on_progress or (lambda _msg: None)

    notify("Discovering tools...")
    names = client.discover_tools(category, audience, count, house)
    notify(f"Found {len(names)} tools - researching in parallel...")

    profiles: list[ToolProfile | None] = [None] * len(names)
    completed = 0

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(names) + 1)) as pool:
        tool_futures = {
            pool.submit(_research_one, client, name, category, audience, name == house): i
            for i, name in enumerate(names)
        }
        dims_future = pool.submit(client.derive_dimensions, category, audience, secondary_kws)

        for future in as_completed(tool_futures):
            i = tool_futures[future]
            profiles[i] = future.result()
            completed += 1
            notify(f"Researched {names[i]} ({completed}/{len(names)})")

        try:
            dims = [Dimension(**d) for d in dims_future.result()]
        except Exception as e:
            print(f"  ! could not derive dimensions: {e} - continuing without them")
            dims = []
        notify("Derived comparison dimensions.")

    return ResearchBundle(
        primary_keyword=inp["primary_keyword"],
        secondary_keywords=secondary_kws,
        category_label=category,
        audience=audience,
        year=inp["year"],
        tools=profiles,
        dimensions=dims,
        mode=mode,
        model=model,
        researched_at=date.today().isoformat(),
    )
