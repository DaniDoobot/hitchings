"""Bright Data LinkedIn discovery provider using Web Scraper / Dataset API (Bloque 9C)."""

import logging
import uuid
from datetime import datetime
from typing import Any, Optional
import httpx

from app.core.config import get_settings
from app.providers.linkedin.base import (
    BaseLinkedInProvider,
    LinkedInDiscoveredPost,
    LinkedInAuthError,
    LinkedInRecoverableError,
    LinkedInTimeoutError,
    LinkedInQuotaExceededError,
)

logger = logging.getLogger(__name__)


def resolve_discover_by(
    target_url: str,
    entity_type: Optional[str] = None,
) -> str:
    """Determine the Bright Data discover_by parameter based on entity type and target URL.

    Returns:
        'company_url' for organizations / companies
        'profile_url' for individuals / persons
    """
    if entity_type:
        norm_type = entity_type.strip().lower()
        if norm_type in ("organization", "institution", "company"):
            return "company_url"
        if norm_type in ("person", "individual"):
            return "profile_url"

    clean_url = target_url.strip().lower()
    if "/company/" in clean_url:
        return "company_url"
    return "profile_url"


def build_discovery_payload(
    target_url: str,
    entity_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Build the discovery input payload for Bright Data dataset API.

    Rules:
    - organization: [{"url": target_url}]
    - person: [{"url": target_url, "only_authored_posts": True}]
    """
    clean_url = target_url.strip()
    discover_by = resolve_discover_by(clean_url, entity_type)
    if discover_by == "company_url":
        return [{"url": clean_url}]
    return [{"url": clean_url, "only_authored_posts": True}]


class BrightDataLinkedInProvider(BaseLinkedInProvider):
    """Primary provider for LinkedIn post discovery using Bright Data's Web Scraper API."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.last_http_status: Optional[int] = None

    @property
    def provider_name(self) -> str:
        return "brightdata"

    def discover_posts(
        self,
        target_url: str,
        client: httpx.Client,
        limit: int = 5,
        entity_name: Optional[str] = None,
        entity_id: Optional[uuid.UUID] = None,
        entity_type: Optional[str] = None,
    ) -> list[LinkedInDiscoveredPost]:
        """Fetch posts for a target LinkedIn URL via Bright Data Dataset API."""
        token = self.settings.brightdata_token
        if not token:
            raise LinkedInAuthError(
                "Bright Data API token is not configured (BRIGHTDATA_API_TOKEN is empty). Fail-closed."
            )

        endpoint = self.settings.BRIGHTDATA_LINKEDIN_ENDPOINT
        dataset_id = self.settings.BRIGHTDATA_LINKEDIN_DATASET_ID
        discover_by = resolve_discover_by(target_url, entity_type)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "HITCHINGS/1.0",
        }
        params = {
            "dataset_id": dataset_id,
            "discover_by": discover_by,
        }
        payload = build_discovery_payload(target_url, entity_type)

        logger.info(
            "Bright Data discovery dispatching target_url=%s (dataset=%s, discover_by=%s, limit=%d)",
            target_url,
            dataset_id,
            discover_by,
            limit,
        )

        try:
            response = client.post(
                endpoint,
                params=params,
                json=payload,
                headers=headers,
                timeout=self.settings.LINKEDIN_TIMEOUT_SECONDS,
            )
            self.last_http_status = response.status_code
        except httpx.TimeoutException as exc:
            self.last_http_status = None
            raise LinkedInTimeoutError(f"Bright Data request timed out for {target_url}: {exc}") from exc
        except (httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            self.last_http_status = None
            raise LinkedInRecoverableError(f"Bright Data network error for {target_url}: {exc}") from exc
        except Exception as exc:
            self.last_http_status = None
            raise LinkedInRecoverableError(f"Bright Data unexpected transport error: {exc}") from exc

        # Authentication / Authorization errors: FAIL CLOSED, NEVER FALLBACK
        if response.status_code in (401, 403):
            raise LinkedInAuthError(
                f"Bright Data authentication error (HTTP {response.status_code}). Check API token."
            )

        # Rate limits / Quota
        if response.status_code == 429:
            raise LinkedInQuotaExceededError(
                f"Bright Data rate limit / quota exceeded (HTTP 429)."
            )

        # Server errors (5xx)
        if response.status_code >= 500:
            raise LinkedInRecoverableError(
                f"Bright Data server error (HTTP {response.status_code}): {response.text[:200]}"
            )

        # Other client errors (4xx)
        if response.status_code >= 400:
            raise LinkedInRecoverableError(
                f"Bright Data client error (HTTP {response.status_code}): {response.text[:200]}"
            )

        try:
            data = response.json()
        except Exception as exc:
            raise LinkedInRecoverableError(f"Bright Data invalid JSON response: {exc}") from exc

        return self._parse_response(
            data,
            target_url,
            limit,
            entity_name,
            entity_id,
            http_status=response.status_code,
        )

    def _parse_response(
        self,
        data: Any,
        target_url: str,
        limit: int,
        entity_name: Optional[str],
        entity_id: Optional[uuid.UUID],
        http_status: Optional[int] = None,
    ) -> list[LinkedInDiscoveredPost]:
        """Parse raw Bright Data response records into normalized LinkedInDiscoveredPost objects."""
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [d for d in data if isinstance(d, dict)]
        elif isinstance(data, dict):
            if "data" in data and isinstance(data["data"], list):
                items = [d for d in data["data"] if isinstance(d, dict)]
            else:
                items = [data]

        posts: list[LinkedInDiscoveredPost] = []
        for item in items[:limit]:
            post_url = item.get("url") or item.get("post_url")
            if not post_url or not isinstance(post_url, str):
                continue

            post_id = str(item.get("id") or item.get("post_id") or "")
            text = item.get("post_text") or item.get("text") or ""
            raw_author = item.get("author") or item.get("user_id")
            author = str(raw_author).strip() if raw_author else ""
            author_profile_url = item.get("use_url") or target_url

            # Parse publication timestamp safely
            published_at = None
            date_str = item.get("date_posted") or item.get("date")
            if date_str and isinstance(date_str, str):
                try:
                    clean_date = date_str.replace("Z", "+00:00")
                    published_at = datetime.fromisoformat(clean_date)
                except Exception:
                    published_at = None

            # Engagement metrics
            engagement = {
                "likes": item.get("num_likes"),
                "comments": item.get("num_comments"),
                "reposts": item.get("num_reposts"),
            }

            # Privacy & Data Minimization: strictly whitelist useful metadata fields
            sanitized_meta = {
                "provider": self.provider_name,
                "dataset_id": self.settings.BRIGHTDATA_LINKEDIN_DATASET_ID,
                "account_type": item.get("account_type"),
                "post_type": item.get("post_type"),
                "user_followers": item.get("user_followers"),
                "http_status": http_status or self.last_http_status or 200,
            }
            # Clean None values from metadata
            sanitized_meta = {k: v for k, v in sanitized_meta.items() if v is not None}

            post = LinkedInDiscoveredPost(
                provider=self.provider_name,
                provider_item_id=post_id if post_id else None,
                linkedin_post_url=post_url.strip(),
                author_name=author.strip(),
                author_profile_url=author_profile_url.strip() if author_profile_url else None,
                author_entity_id=entity_id,
                text=text.strip() if text else None,
                published_at=published_at,
                engagement=engagement,
                raw_metadata=sanitized_meta,
            )
            posts.append(post)

        return posts
