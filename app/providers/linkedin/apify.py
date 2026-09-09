"""Apify LinkedIn discovery provider using Actor sync dataset items API (Bloque 9C)."""

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


def normalize_apify_actor_id(actor_id: str) -> str:
    """Normalize Apify actor ID so that username/actor becomes username~actor for URL paths.

    Apify REST API v2 uses /v2/acts/{username}~{actorName}/run-sync-get-dataset-items
    when referencing store actors, rather than slashes which break REST path routing.
    """
    cleaned = actor_id.strip()
    if "/" in cleaned:
        return cleaned.replace("/", "~")
    return cleaned


class ApifyLinkedInProvider(BaseLinkedInProvider):
    """Fallback provider for LinkedIn post discovery using Apify Actors."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def provider_name(self) -> str:
        return "apify"

    def discover_posts(
        self,
        target_url: str,
        client: httpx.Client,
        limit: int = 5,
        entity_name: Optional[str] = None,
        entity_id: Optional[uuid.UUID] = None,
    ) -> list[LinkedInDiscoveredPost]:
        """Fetch posts for a target LinkedIn URL via Apify Actor."""
        token = self.settings.apify_token
        if not token:
            raise LinkedInAuthError(
                "Apify API token is not configured (APIFY_API_TOKEN is empty). Fail-closed."
            )

        actor_id = normalize_apify_actor_id(self.settings.APIFY_LINKEDIN_ACTOR_ID)
        base_endpoint = self.settings.APIFY_LINKEDIN_ENDPOINT.rstrip("/")
        endpoint = f"{base_endpoint}/{actor_id}/run-sync-get-dataset-items"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "HITCHINGS/1.0",
        }
        # harvestapi/linkedin-profile-posts input schema (100% no-cookies, no reactions/comments)
        payload = {
            "targetUrls": [target_url],
            "maxPosts": limit,
            "scrapeReactions": False,
            "scrapeComments": False,
        }

        logger.info(
            "Apify discovery fallback dispatching target_url=%s (actor=%s, limit=%d)",
            target_url,
            actor_id,
            limit,
        )

        try:
            response = client.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=self.settings.LINKEDIN_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException as exc:
            raise LinkedInTimeoutError(f"Apify request timed out for {target_url}: {exc}") from exc
        except (httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise LinkedInRecoverableError(f"Apify network error for {target_url}: {exc}") from exc
        except Exception as exc:
            raise LinkedInRecoverableError(f"Apify unexpected transport error: {exc}") from exc

        # Authentication / Authorization errors: FAIL CLOSED
        if response.status_code in (401, 403):
            raise LinkedInAuthError(
                f"Apify authentication error (HTTP {response.status_code}). Check API token."
            )

        # Rate limits / Quota
        if response.status_code == 429:
            raise LinkedInQuotaExceededError(
                f"Apify rate limit / quota exceeded (HTTP 429)."
            )

        # Server errors (5xx)
        if response.status_code >= 500:
            raise LinkedInRecoverableError(
                f"Apify server error (HTTP {response.status_code}): {response.text[:200]}"
            )

        # Other client errors (4xx)
        if response.status_code >= 400:
            raise LinkedInRecoverableError(
                f"Apify client error (HTTP {response.status_code}): {response.text[:200]}"
            )

        try:
            data = response.json()
        except Exception as exc:
            raise LinkedInRecoverableError(f"Apify invalid JSON response: {exc}") from exc

        return self._parse_response(data, target_url, limit, entity_name, entity_id)

    def _parse_response(
        self,
        data: Any,
        target_url: str,
        limit: int,
        entity_name: Optional[str],
        entity_id: Optional[uuid.UUID],
    ) -> list[LinkedInDiscoveredPost]:
        """Parse raw Apify response dataset items into normalized LinkedInDiscoveredPost objects."""
        items: list[dict[str, Any]] = []
        if isinstance(data, list):
            items = [d for d in data if isinstance(d, dict)]
        elif isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                items = [d for d in data["items"] if isinstance(d, dict)]
            else:
                items = [data]

        posts: list[LinkedInDiscoveredPost] = []
        for item in items[:limit]:
            # Skip nested reaction or comment items if present in dataset
            item_type = item.get("type")
            if item_type and item_type not in ("post", "feed", "share"):
                continue

            post_url = item.get("linkedinUrl") or item.get("url") or item.get("postUrl")
            if not post_url or not isinstance(post_url, str):
                continue

            post_id = str(item.get("id") or item.get("urn") or item.get("post_id") or "")
            text = item.get("content") or item.get("text") or item.get("postContent") or ""

            # Author extraction (supports both harvestapi dict format and string format)
            author_val = item.get("author")
            if isinstance(author_val, dict):
                author = (
                    author_val.get("name")
                    or author_val.get("publicIdentifier")
                    or entity_name
                    or "LinkedIn Author"
                )
                author_profile_url = author_val.get("linkedinUrl") or target_url
            else:
                author = str(
                    author_val
                    or item.get("authorName")
                    or item.get("authorFullName")
                    or entity_name
                    or "LinkedIn Author"
                )
                author_profile_url = item.get("authorProfileUrl") or item.get("authorUrl") or target_url

            # Publication timestamp (supports harvestapi postedAt dict and ISO strings)
            published_at = None
            posted_at_val = item.get("postedAt")
            if isinstance(posted_at_val, dict):
                date_str = posted_at_val.get("date")
            else:
                date_str = posted_at_val or item.get("publishedAt") or item.get("date")

            if date_str and isinstance(date_str, str):
                try:
                    clean_date = date_str.replace("Z", "+00:00")
                    published_at = datetime.fromisoformat(clean_date)
                except Exception:
                    published_at = None

            # Engagement metrics (supports harvestapi engagement dict and scalar fields)
            eng_val = item.get("engagement")
            if isinstance(eng_val, dict):
                engagement = {
                    "likes": eng_val.get("likes"),
                    "comments": eng_val.get("comments"),
                    "reposts": eng_val.get("shares") or eng_val.get("reposts"),
                }
            else:
                engagement = {
                    "likes": item.get("likesCount") or item.get("numLikes"),
                    "comments": item.get("commentsCount") or item.get("numComments"),
                    "reposts": item.get("sharesCount") or item.get("repostsCount"),
                }

            # Privacy & Data Minimization: strictly whitelist useful metadata fields
            sanitized_meta = {
                "provider": self.provider_name,
                "actor_id": self.settings.APIFY_LINKEDIN_ACTOR_ID,
                "item_type": item.get("type"),
                "actor_run_id": item.get("actorRunId"),
            }
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
