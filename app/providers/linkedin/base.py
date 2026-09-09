"""Provider-agnostic abstraction and normalized models for LinkedIn discovery (Bloque 9C)."""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
import httpx


# ==============================================================================
# 1. NORMALIZED POST MODEL
# ==============================================================================

@dataclass
class LinkedInDiscoveredPost:
    """Normalized representation of a post discovered via external providers."""

    provider: str
    provider_item_id: Optional[str]
    linkedin_post_url: str
    author_name: str
    author_profile_url: Optional[str] = None
    author_entity_id: Optional[uuid.UUID] = None
    text: Optional[str] = None
    published_at: Optional[datetime] = None
    engagement: Optional[dict[str, Any]] = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


# ==============================================================================
# 2. EXCEPTION HIERARCHY
# ==============================================================================

class LinkedInProviderError(Exception):
    """Base exception for LinkedIn external provider errors."""
    pass


class LinkedInAuthError(LinkedInProviderError):
    """Authentication or authorization failure (401, 403, invalid/missing API key).
    
    CRITICAL: This error is NOT eligible for automatic fallback.
    If credentials are invalid, the system must fail closed and report configuration.
    """
    pass


class LinkedInRecoverableError(LinkedInProviderError):
    """Temporary or recoverable provider failure eligible for fallback (timeout, 5xx, rate limit)."""
    pass


class LinkedInTimeoutError(LinkedInRecoverableError):
    """Provider request timed out."""
    pass


class LinkedInQuotaExceededError(LinkedInRecoverableError):
    """Provider quota or rate limit exceeded (HTTP 429)."""
    pass


# ==============================================================================
# 3. BASE PROVIDER CONTRACT
# ==============================================================================

class BaseLinkedInProvider(ABC):
    """Abstract interface for external LinkedIn discovery providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Identifier of the provider (e.g. 'brightdata', 'apify')."""
        pass

    @abstractmethod
    def discover_posts(
        self,
        target_url: str,
        client: httpx.Client,
        limit: int = 5,
        entity_name: Optional[str] = None,
        entity_id: Optional[uuid.UUID] = None,
    ) -> list[LinkedInDiscoveredPost]:
        """Fetch posts for a target LinkedIn profile or company URL.
        
        Args:
            target_url: Canonical LinkedIn profile or company page URL.
            client: Synchronous HTTPX client for outbound requests.
            limit: Maximum number of posts to retrieve.
            entity_name: Display name of the tracked entity for fallback author attribution.
            entity_id: UUID of the tracked entity.
            
        Returns:
            List of normalized LinkedInDiscoveredPost records.
            
        Raises:
            LinkedInAuthError: On 401/403 credentials error.
            LinkedInRecoverableError: On timeout, 5xx, network error, or rate limits.
        """
        pass
