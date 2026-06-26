"""Streamlit front door for the listicle pipeline.

A thin presentation layer - it reuses the exact same pipeline functions the CLI
uses (research.run / generate.run / assemble.run / qa.run + the editorial judge),
so there is zero duplicated logic. The UI's job is to make the two human gates
operable by a non-engineer:

  Gate 1  -> an editable table of the researched facts (fix any wrong price/rating)
  Gate 2  -> the rendered draft + QA checklist + editorial score, with a download

Run:  pip install -r requirements-app.txt  &&  streamlit run app.py
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv

from pipeline import assemble, generate, qa, research
from pipeline.llm import LiveAnthropicClient, LiveOpenAIClient, MockClient

load_dotenv()

ROOT = Path(__file__).resolve().parent
HOUSE = yaml.safe_load((ROOT / "config" / "house_style.yaml").read_text())
FIXTURES = ROOT / "fixtures" / "event_registration_software"
ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "➖"}
DEFAULT_MODEL = {"anthropic": "claude-sonnet-4-6", "openai": "gpt-5.1-mini"}
FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n\n", re.DOTALL)

st.set_page_config(page_title="Listicle Pipeline", layout="wide")
st.title("Listicle pipeline")
st.caption("research → **verify facts (gate 1)** → generate → QA → **review (gate 2)** → publish")


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Inputs")
    primary = st.text_input("Primary keyword", "event registration and ticketing software")
    secondary_raw = st.text_area(
        "Secondary keywords (one per line)",
        "registration flow builder\nevent ticketing and discounting\n"
        "CRM integration for events\nbranded event registration pages\n"
        "approval flows\non-site check-in",
        height=140,
    )
    audience = st.text_input("Audience", "B2B event teams")
    year = st.number_input("Year", 2024, 2030, 2026)
    count = st.number_input("Number of tools", 3, 12, 6)
    house = st.text_input("House product (always #1)", "Zuddl")
    st.divider()
    mock = st.toggle("Mock mode (offline, no API)", value=True)
    provider = st.radio("Provider", ["anthropic", "openai"], horizontal=True, disabled=mock)
    model = st.text_input("Model", DEFAULT_MODEL[provider], disabled=mock)
    api_key_input = st.text_input(
        "API key (optional)", type="password", disabled=mock,
        help="Paste your own key to run live mode on your own account. Leave blank to "
             "use the server's ANTHROPIC_API_KEY / OPENAI_API_KEY, if one is set.",
    )
    st.caption("Mock uses bundled fixtures. Live needs an API key above, or "
               "ANTHROPIC_API_KEY / OPENAI_API_KEY set on the server - and for "
               "Anthropic, web search enabled in the Console.")


def make_client():
    if mock:
        return MockClient(FIXTURES)
    key_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    key = api_key_input.strip() or os.environ.get(key_name, "")
    if not key:
        st.error(f"No API key. Paste one in the sidebar, set {key_name} on the server, "
                 "or switch to mock mode.")
        st.stop()
    if provider == "openai":
        return LiveOpenAIClient(model=model, house_style=HOUSE, api_key=key)
    return LiveAnthropicClient(model=model, house_style=HOUSE, api_key=key)


def build_input() -> dict:
    return {
        "primary_keyword": primary.strip(),
        "category_label": primary.strip(),
        "audience": audience.strip(),
        "year": int(year),
        "tool_count": int(count),
        "house_product": house.strip(),
        "secondary_keywords": [s.strip() for s in secondary_raw.splitlines() if s.strip()],
    }


# --------------------------------------------------------------------------- #
# Stage 1 - research
# --------------------------------------------------------------------------- #
if st.button("① Research", type="primary"):
    st.session_state.pop("result", None)
    with st.status("Gathering tools + grounded facts...", expanded=True) as status:
        try:
            client = make_client()
            st.session_state.bundle = research.run(
                client, build_input(), HOUSE, "mock" if mock else "live",
                "" if mock else model, on_progress=status.write,
            )
            st.session_state.usage = getattr(client, "usage", None)
            status.update(label="Research complete.", state="complete")
        except Exception as e:  # surface live API / Console errors cleanly
            status.update(label="Research failed.", state="error")
            st.error(f"Research failed: {e}")


# --------------------------------------------------------------------------- #
# Gate 1 - verify the facts
# --------------------------------------------------------------------------- #
if "bundle" in st.session_state:
    b = st.session_state.bundle
    st.subheader("Gate 1 · verify the facts")
    st.caption("Fix any wrong pricing or ratings before generating. This is the human "
               "checkpoint that keeps a hallucinated number from ever reaching a draft.")

    df = pd.DataFrame([{
        "name": t.name, "is_house": t.is_house, "pricing": t.pricing,
        "g2_rating": t.g2_rating or "", "capterra_rating": t.capterra_rating or "",
        "best_for": t.best_for,
    } for t in b.tools])
    edited = st.data_editor(
        df, disabled=["name", "is_house"], hide_index=True,
        use_container_width=True, key="facts_editor",
        column_config={"best_for": st.column_config.TextColumn(width="large")},
    )

    with st.expander("Sources gathered during research"):
        for t in b.tools:
            st.markdown(f"**{t.name}** - " + (", ".join(t.sources) if t.sources else "_none_"))

    if st.button("② Approve & generate", type="primary"):
        edits = {row["name"]: row for _, row in edited.iterrows()}
        for t in b.tools:                      # merge human edits back into the bundle
            e = edits.get(t.name)
            if e is not None:
                t.pricing = e["pricing"]
                t.g2_rating = e["g2_rating"] or None
                t.capterra_rating = e["capterra_rating"] or None
                t.best_for = e["best_for"]
        with st.status("Generating sections, assembling, running QA + editorial review...",
                        expanded=True) as status:
            try:
                client = make_client()
                sections = generate.run(client, b, HOUSE, on_progress=status.write)
                status.write("Assembling draft...")
                md = assemble.run(b, sections, HOUSE)
                status.write("Running QA...")
                report = qa.run(md, b, sections, HOUSE)
                status.write("Scoring editorial review...")
                editorial = client.score_editorial(md, b)
                st.session_state.result = {
                    "md": md, "report": report, "editorial": editorial, "sections": sections,
                }
                status.update(label="Generation complete.", state="complete")
            except Exception as e:
                status.update(label="Generation failed.", state="error")
                st.error(f"Generation failed: {e}")


# --------------------------------------------------------------------------- #
# Gate 2 - review & publish
# --------------------------------------------------------------------------- #
if "result" in st.session_state:
    res = st.session_state.result
    st.divider()
    st.subheader("Gate 2 · review & publish")

    left, right = st.columns([2, 1])
    with right:
        rep = res["report"]
        if rep.hard_fail:
            st.error("Hard checks failed - fix before publishing.")
        else:
            st.success("All hard checks passed.")
        st.metric("Editorial score", f'{res["editorial"]["score"]}/100')
        st.caption(res["editorial"]["verdict"])
        for issue in res["editorial"].get("issues", []):
            st.caption("• " + issue)
        st.markdown("**QA**")
        for c in rep.checks:
            st.write(f'{ICON[c.status]} {c.name} - {c.detail}')
        st.caption(f'{rep.word_count} words · ~{rep.read_minutes} min read')

    with left:
        st.download_button("⬇ Download draft.md", res["md"],
                           file_name=(res["sections"].slug or "draft") + ".md")
        st.markdown(FRONTMATTER_RE.sub("", res["md"], count=1))
