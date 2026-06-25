"""Data contracts for the listicle pipeline.

Everything flows through these models. The most important rule the whole
system enforces lives here implicitly: the *writer* never invents a fact.
Pricing, ratings, strengths, gaps and source URLs are populated once, during
research, reviewed by a human, and then read-only for the rest of the run.
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class Dimension(BaseModel):
    """One buyer's-guide criterion + the failure mode that makes it matter."""
    name: str
    why: str


class ToolProfile(BaseModel):
    """All verifiable facts about a single tool. Filled by research, frozen after gate 1."""
    model_config = ConfigDict(extra="ignore")

    name: str
    is_house: bool = False
    one_liner: str = ""                       # 1-2 sentence positioning
    url: Optional[str] = None                 # product / category page
    pricing: str = "Pricing not found"
    pricing_url: Optional[str] = None
    g2_rating: Optional[str] = None           # e.g. "4.8/5"
    g2_url: Optional[str] = None
    capterra_rating: Optional[str] = None
    capterra_url: Optional[str] = None
    strengths: List[str] = Field(default_factory=list)   # 3-5 bullets
    gaps: List[str] = Field(default_factory=list)        # 1-3 bullets
    best_for: str = ""
    sources: List[str] = Field(default_factory=list)     # citation URLs from research

    def known_numbers(self) -> List[str]:
        """Every price/rating string a draft is allowed to mention for this tool."""
        out = [self.pricing]
        if self.g2_rating:
            out.append(self.g2_rating)
        if self.capterra_rating:
            out.append(self.capterra_rating)
        return [x for x in out if x]


class ResearchBundle(BaseModel):
    """Output of stage 1 and the artifact a human approves at gate 1."""
    model_config = ConfigDict(extra="ignore")

    primary_keyword: str
    secondary_keywords: List[str] = Field(default_factory=list)
    category_label: str                       # lowercase, used in section headers
    audience: str
    year: int
    tools: List[ToolProfile]
    dimensions: List[Dimension] = Field(default_factory=list)
    mode: str = "live"                        # "live" | "mock"
    model: str = ""

    def house(self) -> Optional[ToolProfile]:
        return next((t for t in self.tools if t.is_house), None)


class GeneratedSections(BaseModel):
    """Output of stage 2: the connective editorial prose + SEO metadata."""
    title: str
    meta_description: str
    slug: str
    intro_md: str
    faq_md: str
