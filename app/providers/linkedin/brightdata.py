"""Bright Data LinkedIn discovery provider using Web Scraper / Dataset API (Bloque 9C)."""

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Optional
import httpx

from app.core.config import get_settings
from app.providers.linkedin.base import (
    BaseLinkedInProvider,
    LinkedInDiscoveredPost,
    LinkedInAuthError,
    LinkedInRecoverableError,
    LinkedInTimeoutError,
    LinkedInSnapshotTimeoutError,
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

    def __init__(
        self,
        poll_interval: Optional[float] = None,
        max_poll_attempts: Optional[int] = None,
        poll_timeout: Optional[float] = None,
        backoff_factor: Optional[float] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.settings = get_settings()
        self.last_http_status: Optional[int] = None
        self.poll_interval = (
            poll_interval
            if poll_interval is not None
            else getattr(self.settings, "BRIGHTDATA_POLL_INTERVAL_SECONDS", 2.0)
        )
        self.max_poll_attempts = (
            max_poll_attempts
            if max_poll_attempts is not None
            else getattr(self.settings, "BRIGHTDATA_POLL_MAX_ATTEMPTS", 40)
        )
        self.poll_timeout = (
            poll_timeout
            if poll_timeout is not None
            else getattr(self.settings, "BRIGHTDATA_POLL_TIMEOUT_SECONDS", 300.0)
        )
        self.backoff_factor = (
            backoff_factor
            if backoff_factor is not None
            else getattr(self.settings, "BRIGHTDATA_POLL_BACKOFF_FACTOR", 1.5)
        )
        self._sleep_fn = sleep_fn or time.sleep

    @property
    def provider_name(self) -> str:
        return "brightdata"

    @staticmethod
    def _resolve_base_api_url(endpoint: str) -> str:
        """Derive base dataset v3 URL from configured endpoint."""
        clean = endpoint.rstrip("/")
        if clean.endswith("/scrape"):
            return clean[:-7]
        if clean.endswith("/trigger"):
            return clean[:-8]
        return clean

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

        base_url = self._resolve_base_api_url(self.settings.BRIGHTDATA_LINKEDIN_ENDPOINT)
        endpoint = f"{base_url}/trigger"
        dataset_id = self.settings.BRIGHTDATA_LINKEDIN_DATASET_ID
        discover_by = resolve_discover_by(target_url, entity_type)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "HITCHINGS/1.0",
        }
        params = {
            "dataset_id": dataset_id,
            "type": "discover_new",
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

        # Check if response initiated an asynchronous snapshot
        snapshot_id: Optional[str] = None
        if isinstance(data, dict) and "snapshot_id" in data and data["snapshot_id"]:
            snapshot_id = str(data["snapshot_id"]).strip()

        if snapshot_id:
            logger.info("Bright Data snapshot created: snapshot_id=%s for target_url=%s", snapshot_id, target_url)
            data = self._poll_and_download_snapshot(
                snapshot_id=snapshot_id,
                client=client,
                headers=headers,
                target_url=target_url,
            )

        return self._parse_response(
            data,
            target_url,
            limit,
            entity_name,
            entity_id,
            http_status=self.last_http_status or response.status_code,
        )

    def _poll_and_download_snapshot(
        self,
        snapshot_id: str,
        client: httpx.Client,
        headers: dict[str, str],
        target_url: str,
    ) -> Any:
        """Poll progress endpoint until snapshot is ready, then download JSON data."""
        base_url = self._resolve_base_api_url(self.settings.BRIGHTDATA_LINKEDIN_ENDPOINT)
        progress_url = f"{base_url}/progress/{snapshot_id}"
        snapshot_url = f"{base_url}/snapshot/{snapshot_id}"

        poll_start = time.time()
        current_interval = self.poll_interval
        max_interval = getattr(self.settings, "BRIGHTDATA_POLL_MAX_INTERVAL_SECONDS", 10.0)
        snapshot_ready = False

        for attempt in range(1, self.max_poll_attempts + 1):
            elapsed = time.time() - poll_start
            if elapsed > self.poll_timeout:
                raise LinkedInSnapshotTimeoutError(
                    f"Bright Data polling timed out after {elapsed:.1f}s ({attempt - 1} attempts) for snapshot_id={snapshot_id}"
                )

            logger.info(
                "Bright Data polling snapshot_id=%s (attempt %d/%d, elapsed %.1fs)",
                snapshot_id,
                attempt,
                self.max_poll_attempts,
                elapsed,
            )

            try:
                prog_resp = client.get(
                    progress_url,
                    headers=headers,
                    timeout=self.settings.LINKEDIN_TIMEOUT_SECONDS,
                )
                self.last_http_status = prog_resp.status_code
            except httpx.TimeoutException as exc:
                logger.warning("Bright Data progress poll request timed out on attempt %d: %s", attempt, exc)
            except Exception as exc:
                logger.warning("Bright Data progress poll request error on attempt %d: %s", attempt, exc)
            else:
                if prog_resp.status_code in (401, 403):
                    raise LinkedInAuthError(
                        f"Bright Data authentication error during poll (HTTP {prog_resp.status_code}). Check API token."
                    )
                if prog_resp.status_code == 429:
                    raise LinkedInQuotaExceededError("Bright Data rate limit exceeded during poll (HTTP 429).")
                if prog_resp.status_code == 200:
                    try:
                        prog_json = prog_resp.json()
                    except Exception:
                        prog_json = {}
                    status = str(prog_json.get("status") or "").strip().lower()
                    logger.info("Bright Data snapshot_id=%s current status: %s", snapshot_id, status)
                    if status in ("ready", "completed", "done"):
                        snapshot_ready = True
                        break
                    elif status in ("failed", "error"):
                        raise LinkedInRecoverableError(f"Bright Data snapshot {snapshot_id} failed with status: {status}")
                elif prog_resp.status_code >= 500:
                    logger.warning("Bright Data progress poll returned server error HTTP %d", prog_resp.status_code)
                elif prog_resp.status_code >= 400:
                    raise LinkedInRecoverableError(
                        f"Bright Data progress poll failed (HTTP {prog_resp.status_code}): {prog_resp.text[:200]}"
                    )

            self._sleep_fn(current_interval)
            current_interval = min(current_interval * self.backoff_factor, max_interval)

        if not snapshot_ready:
            raise LinkedInSnapshotTimeoutError(
                f"Bright Data snapshot_id={snapshot_id} did not complete within {self.max_poll_attempts} attempts."
            )

        logger.info("Bright Data downloading snapshot: snapshot_id=%s", snapshot_id)
        try:
            snap_resp = client.get(
                snapshot_url,
                params={"format": "json"},
                headers=headers,
                timeout=self.settings.LINKEDIN_TIMEOUT_SECONDS,
            )
            self.last_http_status = snap_resp.status_code
        except httpx.TimeoutException as exc:
            raise LinkedInTimeoutError(f"Bright Data snapshot download timed out: {exc}") from exc
        except Exception as exc:
            raise LinkedInRecoverableError(f"Bright Data snapshot download error: {exc}") from exc

        if snap_resp.status_code in (401, 403):
            raise LinkedInAuthError(f"Bright Data auth error downloading snapshot (HTTP {snap_resp.status_code}).")
        if snap_resp.status_code == 429:
            raise LinkedInQuotaExceededError("Bright Data rate limit exceeded downloading snapshot (HTTP 429).")
        if snap_resp.status_code >= 400:
            raise LinkedInRecoverableError(
                f"Bright Data snapshot download returned HTTP {snap_resp.status_code}: {snap_resp.text[:200]}"
            )

        logger.info("Bright Data snapshot download completed: snapshot_id=%s", snapshot_id)
        try:
            return snap_resp.json()
        except Exception as exc:
            raise LinkedInRecoverableError(f"Bright Data invalid snapshot JSON: {exc}") from exc

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
