"""Pydantic data models for Direct Web Sources (Bloque 9B)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class DiscoveredItem(BaseModel):
    """An article reference discovered via RSS, Atom, sitemap, or listing page."""

    url: str
    title: str
    external_id: str
    published_at: Optional[datetime] = None
    author: Optional[str] = None
    excerpt: Optional[str] = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class DirectWebArticle(BaseModel):
    """A fully extracted and normalized article from a direct reference web source."""

    external_id: str
    title: str
    url: str
    canonical_url: str
    content: str
    published_at: Optional[datetime] = None
    author: Optional[str] = None
    excerpt: Optional[str] = None
    language: str = "en"
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
