"""Deterministic mock AI provider for testing without external LLM calls."""

import time
from typing import Optional, Any

from app.models.analysis import AnalysisPromptVersion
from app.models.entry import Entry
from app.schemas.analysis import (
    AIAnalysisResponsePayload,
    AIAnalysisTopicItem,
    GroundingEvidence,
    KeyPointV3,
)
from app.providers.ai.base import BaseAIProvider, AIProviderResult


class MockAIProvider(BaseAIProvider):
    """Deterministic, configurable mock provider for local test suites."""

    def __init__(
        self,
        model: str = "mock-analyst-v1",
        latency_ms: int = 15,
        force_failure: bool = False,
        error_type: str = "MockAIError",
        error_message: str = "Simulated AI provider error",
        fixed_score: Optional[int] = None,
        fixed_topics: Optional[list[AIAnalysisTopicItem]] = None,
        invalid_format: bool = False,
    ) -> None:
        self.model_name = model
        self.latency_ms = latency_ms
        self.force_failure = force_failure
        self.error_type = error_type
        self.error_message = error_message
        self.fixed_score = fixed_score
        self.fixed_topics = fixed_topics
        self.invalid_format = invalid_format

    @property
    def provider_name(self) -> str:
        return "mock"

    async def analyze(
        self,
        prompt_version: AnalysisPromptVersion,
        entry: Entry,
        matrix_snapshot: dict[str, Any],
        extra_call_metadata: Optional[dict[str, Any]] = None,
        triage_result: Optional[dict[str, Any]] = None,
    ) -> AIProviderResult:
        """Simulate analysis deterministically based on entry text and snapshot."""
        start_time = time.monotonic()

        text_content = f"{entry.title or ''} {entry.excerpt or ''} {entry.content or ''}".strip().lower()
        input_text = f"{prompt_version.system_prompt}\n{prompt_version.user_prompt_template}\n{text_content}"
        input_chars = len(input_text)
        input_tokens = max(1, input_chars // 4)

        # 1. Simulated failure
        if self.force_failure:
            latency = int((time.monotonic() - start_time) * 1000) or self.latency_ms
            return AIProviderResult(
                success=False,
                provider_name=self.provider_name,
                model=self.model_name,
                input_chars=input_chars,
                output_chars=0,
                input_tokens=input_tokens,
                output_tokens=0,
                estimated_cost_usd=0.0,
                latency_ms=latency,
                error_type=self.error_type,
                error_message=self.error_message,
                call_metadata={"mock": True, "forced_failure": True},
            )

        # 2. Simulated schema / formatting error
        if self.invalid_format:
            latency = int((time.monotonic() - start_time) * 1000) or self.latency_ms
            return AIProviderResult(
                success=False,
                provider_name=self.provider_name,
                model=self.model_name,
                input_chars=input_chars,
                output_chars=40,
                input_tokens=input_tokens,
                output_tokens=10,
                estimated_cost_usd=0.0,
                latency_ms=latency,
                error_type="ValidationError",
                error_message="Provider returned malformed JSON payload incompatible with schema",
                raw_response={"corrupt": True},
                call_metadata={"mock": True, "invalid_format": True},
            )

        # 3. Deterministic relevance score
        if self.fixed_score is not None:
            score = self.fixed_score
        else:
            high_relevance_keywords = ["merger", "concentrac", "antitrust", "competencia", "cartel", "abuso de posición", "artículo 101", "artículo 102", "judgment"]
            medium_relevance_keywords = ["subvencion", "ayuda de estado", "state aid", "regulatorio", "consulta pública"]

            if any(kw in text_content for kw in high_relevance_keywords):
                score = 85
            elif any(kw in text_content for kw in medium_relevance_keywords):
                score = 55
            else:
                score = 25

        # 4. Deterministic topic classification
        topics: list[AIAnalysisTopicItem] = []
        if self.fixed_topics is not None:
            topics = self.fixed_topics
        else:
            active_topics = matrix_snapshot.get("topics", [])
            matched_topics: list[str] = []

            # Check matching topic keywords from snapshot
            for t in active_topics:
                code = t.get("code")
                keywords = t.get("keywords") or []
                if any(kw.lower() in text_content for kw in keywords):
                    matched_topics.append(code)

            # Fallback if score is relevant or uncertain but no keyword matched
            if not matched_topics and score >= 40 and active_topics:
                matched_topics.append(active_topics[0].get("code"))

            for idx, code in enumerate(matched_topics[:3]):
                topics.append(
                    AIAnalysisTopicItem(
                        topic_code=code,
                        confidence=round(0.85 - (idx * 0.1), 2),
                        is_primary=(idx == 0),
                        rationale=f"Deterministically identified relevance for topic '{code}'.",
                    )
                )

            # Ensure max 1 primary topic for generated default topics
            primary_count = sum(1 for t in topics if t.is_primary)
            if primary_count > 1:
                first_found = False
                for t in topics:
                    if t.is_primary:
                        if not first_found:
                            first_found = True
                        else:
                            t.is_primary = False
            elif primary_count == 0 and topics:
                topics[0].is_primary = True

        # Construct payload with grounding evidence
        confidence = round(score / 100.0, 2)
        summary = f"Executive summary for: {(entry.title or 'Publication')[:120]}."
        key_points = [
            f"Content length analyzed: {len(entry.content or '')} characters.",
            f"Source domain: {entry.url[:60] if entry.url else 'N/A'}.",
            f"Evaluated relevance score: {score}/100.",
        ]
        reason = f"Deterministic evaluation score of {score} based on regulatory keyword analysis."

        # Verbatim quote for strict grounding validation in v3/v6 prompts
        if entry.title and entry.title.strip():
            evidence_quote = entry.title.strip()[:40].strip()
            source_field = "title"
        elif entry.content and entry.content.strip():
            evidence_quote = entry.content.strip()[:40].strip()
            source_field = "content"
        else:
            evidence_quote = "Publication"
            source_field = "title"

        evidence_list = [GroundingEvidence(source_field=source_field, quote=evidence_quote)]
        key_point_items = [
            KeyPointV3(point=kp, evidence=evidence_list)
            for kp in key_points
        ]

        payload = AIAnalysisResponsePayload(
            relevance_score=score,
            confidence=confidence,
            topics=topics,
            summary=summary,
            key_points=key_points,
            reason=reason,
            evidence=evidence_list,
            summary_evidence=evidence_list,
            key_point_items=key_point_items,
        )

        output_chars = len(summary) + len(reason) + sum(len(kp) for kp in key_points)
        output_tokens = max(1, output_chars // 4)

        # Cost estimation: $1.00 / 1M input tokens, $3.00 / 1M output tokens
        cost = round((input_tokens * 0.000001) + (output_tokens * 0.000003), 6)
        latency = int((time.monotonic() - start_time) * 1000) or self.latency_ms

        raw_resp = {
            "model": self.model_name,
            "mock": True,
            "result": payload.model_dump(),
        }

        return AIProviderResult(
            success=True,
            payload=payload,
            raw_response=raw_resp,
            provider_name=self.provider_name,
            model=self.model_name,
            input_chars=input_chars,
            output_chars=output_chars,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            latency_ms=latency,
            call_metadata={"pipeline": "mock-v1", "deterministic": True},
        )
