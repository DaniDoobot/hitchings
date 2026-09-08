"""Vertex AI provider for HITCHINGS AI analysis pipeline.

Uses google-genai SDK configured for Vertex AI (not Gemini Developer API).
Authentication via Application Default Credentials (ADC).
No API keys, no service account JSON files.

Setup:
    gcloud auth application-default login
"""

import json
import logging
import time
from typing import Any, Optional

from app.core.config import Settings, get_settings
from app.models.analysis import AnalysisPromptVersion
from app.models.entry import Entry
from app.providers.ai.base import BaseAIProvider, AIProviderResult
from app.schemas.analysis import (
    AIAnalysisResponsePayload,
    AIAnalysisTopicItem,
    DeepAnalysisResult,
    TriageAnalysisResult,
)

logger = logging.getLogger(__name__)

# Thinking budget tokens per level.
# These are heuristic starting points; adjust as needed.
THINKING_BUDGET_MAP: dict[str, int] = {
    "none": 0,
    "low": 512,
    "medium": 4096,
    "high": 16384,
}


class AnalysisInputTooLarge(ValueError):
    """Raised when entry content exceeds the configured maximum input character limit.

    Deliberate non-truncation: we prefer an explicit error over silent data loss.
    Future chunking strategy will be designed separately.
    """


class VertexAIProvider(BaseAIProvider):
    """Google Vertex AI provider using Gemini models via the google-genai SDK.

    Responsibilities:
    - Build prompt content for triage or deep analysis stages.
    - Call Vertex AI with Structured Output for reliable JSON responses.
    - Extract real usage metadata (input/output/thought tokens).
    - Compute estimated cost from configurable pricing rates.
    - Return AIProviderResult without touching the database.

    Does NOT:
    - Persist anything to PostgreSQL (AnalysisService / AnalysisPipelineService do that).
    - Implement retries for non-transient errors (schema failures, auth, perms, model not found).
    - Fall back to mock or any other provider.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any = None  # Lazy-initialized on first call

    @property
    def provider_name(self) -> str:
        return "vertex_ai"

    def _get_client(self) -> Any:
        """Lazy-initialize the google-genai client configured for Vertex AI."""
        if self._client is None:
            try:
                from google import genai  # type: ignore[import-untyped]
            except ImportError as exc:
                raise RuntimeError(
                    "google-genai SDK is not installed. "
                    "Run: pip install google-genai>=1.16.0"
                ) from exc

            project = self._settings.VERTEX_AI_PROJECT
            if not project:
                raise RuntimeError(
                    "VERTEX_AI_PROJECT is not configured. "
                    "Set it in .env or as an environment variable before running real analysis calls. "
                    "Run 'gcloud config get-value project' to find your active GCP project."
                )
            location = self._settings.VERTEX_AI_LOCATION or "global"
            self._client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
            )
            logger.info(
                "VertexAI client initialized (project=%s, location=%s, model=%s)",
                project,
                location,
                self._settings.VERTEX_AI_MODEL,
            )
        return self._client

    def _thinking_budget(self, level: str) -> int:
        """Return thinking budget tokens for the given level string."""
        return THINKING_BUDGET_MAP.get(level.lower(), THINKING_BUDGET_MAP["low"])

    def _build_topics_block(self, snapshot: dict[str, Any]) -> str:
        """Format active topics from matrix snapshot into a readable block for the prompt."""
        topics = snapshot.get("topics", [])
        if not topics:
            return "(sin temas activos en la matriz)"
        lines: list[str] = []
        for t in topics:
            code = t.get("code", "")
            name = t.get("name", "")
            description = t.get("description") or ""
            keywords = ", ".join(t.get("keywords") or [])
            relevance_instructions = t.get("relevance_instructions") or ""
            line = f"  - {code} | {name}"
            if description:
                line += f"\n    Descripción: {description}"
            if relevance_instructions:
                line += f"\n    Criterio de relevancia: {relevance_instructions}"
            if keywords:
                line += f"\n    Palabras clave (señales auxiliares): {keywords}"
            lines.append(line)
        return "\n".join(lines)

    def _build_content_section(
        self,
        entry: Entry,
        max_chars: int,
        stage: str,
    ) -> tuple[str, int]:
        """Build the document content section for the prompt.

        Returns:
            (content_section_text, input_chars_count)

        Raises:
            AnalysisInputTooLarge: if content exceeds max_chars.
        """
        content = entry.content or ""
        title = entry.title or ""
        excerpt = entry.excerpt or ""

        if content:
            if len(content) > max_chars:
                raise AnalysisInputTooLarge(
                    f"Entry '{entry.id}' content length ({len(content)} chars) exceeds "
                    f"{stage} stage maximum ({max_chars} chars). "
                    f"Chunking is not yet implemented; please handle this entry manually."
                )
            section = f"Contenido completo:\n{content}"
        elif excerpt:
            section = (
                f"[AVISO: Contenido completo no disponible. Se proporciona extracto.]\n"
                f"Extracto: {excerpt}"
            )
        else:
            # No content, no excerpt — use title + raw_metadata if present
            meta_str = ""
            if entry.raw_metadata:
                try:
                    meta_str = json.dumps(entry.raw_metadata, ensure_ascii=False, indent=2)
                except Exception:
                    meta_str = str(entry.raw_metadata)
            section = (
                f"[AVISO: Contenido completo y extracto no disponibles.]\n"
                f"Título: {title}\n"
                f"Metadatos disponibles:\n{meta_str or '(ninguno)'}"
            )

        return section, len(section)

    def _build_triage_prompt(
        self,
        prompt_version: AnalysisPromptVersion,
        entry: Entry,
        snapshot: dict[str, Any],
    ) -> tuple[str, str, int]:
        """Build system prompt, user message, and input chars for TRIAGE stage.

        Returns:
            (system_text, user_text, input_chars)
        """
        max_chars = self._settings.ANALYSIS_TRIAGE_MAX_INPUT_CHARS
        content_section, content_chars = self._build_content_section(entry, max_chars, "triage")

        topics_block = self._build_topics_block(snapshot)

        topic_codes_list = ", ".join(t.get("code", "") for t in snapshot.get("topics", []))

        published_at_str = (
            entry.published_at.strftime("%Y-%m-%d") if entry.published_at else "Desconocida"
        )
        content_type = entry.content_type or "desconocido"

        user_text = (
            f"[MATRIZ HITCHINGS]\n"
            f"Nombre: {snapshot.get('name', '')}\n"
            f"Instrucciones de relevancia: {snapshot.get('relevance_instructions', '')}\n"
            f"Instrucciones de exclusión: {snapshot.get('exclusion_instructions', '')}\n\n"
            f"Temas disponibles (usa ÚNICAMENTE estos códigos en tu respuesta):\n"
            f"{topics_block}\n\n"
            f"Códigos permitidos: [{topic_codes_list}]\n\n"
            f"[DOCUMENTO A ANALIZAR]\n"
            f"Fuente: {entry.source.name if entry.source else 'Desconocida'}\n"
            f"Título: {entry.title or '(sin título)'}\n"
            f"Fecha de publicación: {published_at_str}\n"
            f"Tipo de contenido: {content_type}\n"
            f"URL: {entry.url or '(sin URL)'}\n\n"
            f"{content_section}"
        )

        input_chars = len(prompt_version.system_prompt) + len(user_text)
        return prompt_version.system_prompt, user_text, input_chars

    def _build_deep_prompt(
        self,
        prompt_version: AnalysisPromptVersion,
        entry: Entry,
        snapshot: dict[str, Any],
        triage_result: Optional[dict[str, Any]] = None,
    ) -> tuple[str, str, int]:
        """Build system prompt, user message, and input chars for DEEP ANALYSIS stage.

        Returns:
            (system_text, user_text, input_chars)
        """
        max_chars = self._settings.ANALYSIS_DEEP_MAX_INPUT_CHARS
        content_section, _ = self._build_content_section(entry, max_chars, "deep_analysis")

        triage_info = triage_result or {}
        primary_topic = triage_info.get("primary_topic_code") or "(sin tema principal)"
        secondary_topics = [
            c for c in (triage_info.get("topic_codes") or [])
            if c != triage_info.get("primary_topic_code")
        ]
        secondary_topics_str = ", ".join(secondary_topics) if secondary_topics else "(ninguno)"
        relevance_score = triage_info.get("relevance_score", "N/A")
        triage_reason = triage_info.get("reason", "")

        published_at_str = (
            entry.published_at.strftime("%Y-%m-%d") if entry.published_at else "Desconocida"
        )

        user_text = (
            f"[CLASIFICACIÓN DE TRIAGE]\n"
            f"Relevancia: {relevance_score}/100\n"
            f"Tema principal: {primary_topic}\n"
            f"Temas secundarios: {secondary_topics_str}\n"
            f"Motivo de relevancia: {triage_reason}\n\n"
            f"[DOCUMENTO]\n"
            f"Fuente: {entry.source.name if entry.source else 'Desconocida'}\n"
            f"Título: {entry.title or '(sin título)'}\n"
            f"Fecha de publicación: {published_at_str}\n"
            f"URL: {entry.url or '(sin URL)'}\n\n"
            f"{content_section}"
        )

        input_chars = len(prompt_version.system_prompt) + len(user_text)
        return prompt_version.system_prompt, user_text, input_chars

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate estimated cost in USD from configurable pricing rates."""
        input_cost = (input_tokens * self._settings.VERTEX_INPUT_USD_PER_MILLION_TOKENS) / 1_000_000
        output_cost = (output_tokens * self._settings.VERTEX_OUTPUT_USD_PER_MILLION_TOKENS) / 1_000_000
        return round(input_cost + output_cost, 8)

    def _extract_usage(self, response: Any) -> tuple[int, int, int]:
        """Extract token counts from response usage_metadata.

        Returns:
            (input_tokens, output_tokens_text, output_tokens_thoughts)

        Thought tokens are included in billable output tokens.
        """
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return 0, 0, 0

        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        # candidates_token_count includes generated text tokens
        output_text_tokens = getattr(usage, "candidates_token_count", 0) or 0
        # thoughts_token_count is present when thinking is enabled
        output_thought_tokens = getattr(usage, "thoughts_token_count", 0) or 0

        return input_tokens, output_text_tokens, output_thought_tokens

    async def analyze(
        self,
        prompt_version: AnalysisPromptVersion,
        entry: Entry,
        matrix_snapshot: dict[str, Any],
        extra_call_metadata: Optional[dict[str, Any]] = None,
        triage_result: Optional[dict[str, Any]] = None,
    ) -> AIProviderResult:
        """Execute a single AI call (triage or deep) against Vertex AI.

        Args:
            prompt_version: The versioned prompt to use (determines stage).
            entry: The Entry to analyze.
            matrix_snapshot: Current matrix snapshot dict.
            extra_call_metadata: Additional metadata to include in call_metadata (e.g. benchmark info).
            triage_result: For deep stage, the triage result dict to include in prompt context.

        Returns:
            AIProviderResult — success or failure, never raises (provider errors are captured).
        """
        start_time = time.monotonic()
        stage = prompt_version.stage

        # Determine thinking level and max output tokens by stage
        if stage == "triage":
            thinking_level = self._settings.ANALYSIS_TRIAGE_THINKING_LEVEL
            max_output_tokens = 512
        elif stage == "deep_analysis":
            thinking_level = self._settings.ANALYSIS_DEEP_THINKING_LEVEL
            max_output_tokens = 2048
        else:
            thinking_level = "low"
            max_output_tokens = 1024

        thinking_budget = self._thinking_budget(thinking_level)

        # Build prompt
        try:
            if stage == "triage":
                system_text, user_text, input_chars = self._build_triage_prompt(
                    prompt_version, entry, matrix_snapshot
                )
                response_schema = TriageAnalysisResult
            else:
                system_text, user_text, input_chars = self._build_deep_prompt(
                    prompt_version, entry, matrix_snapshot, triage_result=triage_result
                )
                response_schema = DeepAnalysisResult
        except AnalysisInputTooLarge as exc:
            latency = int((time.monotonic() - start_time) * 1000) or 1
            return AIProviderResult(
                success=False,
                provider_name=self.provider_name,
                model=self._settings.VERTEX_AI_MODEL,
                input_chars=0,
                error_type="AnalysisInputTooLarge",
                error_message=str(exc),
                latency_ms=latency,
                call_metadata={"stage": stage, "thinking_level": thinking_level},
            )

        # Execute Vertex AI call
        try:
            from google.genai import types as genai_types  # type: ignore[import-untyped]

            client = self._get_client()
            model_name = self._settings.VERTEX_AI_MODEL

            thinking_config = genai_types.ThinkingConfig(
                thinking_budget=thinking_budget,
            )

            generate_config = genai_types.GenerateContentConfig(
                system_instruction=system_text,
                response_mime_type="application/json",
                response_schema=response_schema,
                thinking_config=thinking_config,
                max_output_tokens=max_output_tokens,
                temperature=0.0,
            )

            response = client.models.generate_content(
                model=model_name,
                contents=user_text,
                config=generate_config,
            )

        except Exception as exc:
            latency = int((time.monotonic() - start_time) * 1000) or 1
            logger.error(
                "VertexAI call failed (stage=%s, entry=%s): %s: %s",
                stage, entry.id, type(exc).__name__, exc,
            )
            return AIProviderResult(
                success=False,
                provider_name=self.provider_name,
                model=self._settings.VERTEX_AI_MODEL,
                input_chars=input_chars,
                error_type=type(exc).__name__,
                error_message=str(exc),
                latency_ms=latency,
                call_metadata={
                    "stage": stage,
                    "thinking_level": thinking_level,
                    "thinking_budget": thinking_budget,
                    **(extra_call_metadata or {}),
                },
            )

        latency = int((time.monotonic() - start_time) * 1000) or 1

        # Extract usage
        input_tokens, output_text_tokens, output_thought_tokens = self._extract_usage(response)
        # Billable output = text + thought tokens
        total_output_tokens = output_text_tokens + output_thought_tokens

        # Extract parsed response
        try:
            parsed = response.parsed
        except Exception:
            parsed = None

        if parsed is None:
            # Try to get from text
            raw_text = ""
            try:
                raw_text = response.text or ""
            except Exception:
                pass
            logger.error(
                "VertexAI returned no parsed response (stage=%s, entry=%s). Raw text: %.200s",
                stage, entry.id, raw_text,
            )
            return AIProviderResult(
                success=False,
                provider_name=self.provider_name,
                model=self._settings.VERTEX_AI_MODEL,
                input_chars=input_chars,
                output_chars=len(raw_text),
                input_tokens=input_tokens,
                output_tokens=total_output_tokens,
                estimated_cost_usd=self._calculate_cost(input_tokens, total_output_tokens),
                latency_ms=latency,
                error_type="NoParsedResponse",
                error_message="Model returned no parseable structured output",
                raw_response={"text": raw_text[:2000] if raw_text else None},
                call_metadata={
                    "stage": stage,
                    "thinking_level": thinking_level,
                    "thinking_budget": thinking_budget,
                    "output_text_tokens": output_text_tokens,
                    "output_thought_tokens": output_thought_tokens,
                    "pricing_input_usd_per_million": self._settings.VERTEX_INPUT_USD_PER_MILLION_TOKENS,
                    "pricing_output_usd_per_million": self._settings.VERTEX_OUTPUT_USD_PER_MILLION_TOKENS,
                    "pricing_currency": "USD",
                    **(extra_call_metadata or {}),
                },
            )

        # Build AIProviderResult payload from parsed structured output
        estimated_cost = self._calculate_cost(input_tokens, total_output_tokens)
        output_chars = len(str(parsed))

        if stage == "triage":
            assert isinstance(parsed, TriageAnalysisResult)
            # Convert flat topic_codes list to AIAnalysisTopicItem list
            topics: list[AIAnalysisTopicItem] = []
            for code in parsed.topic_codes:
                topics.append(
                    AIAnalysisTopicItem(
                        topic_code=code,
                        is_primary=(code == parsed.primary_topic_code),
                        confidence=parsed.confidence,
                        rationale=None,  # triage doesn't produce per-topic rationale
                    )
                )

            payload = AIAnalysisResponsePayload(
                relevance_score=parsed.relevance_score,
                confidence=parsed.confidence,
                topics=topics,
                summary=None,       # triage doesn't produce summary
                key_points=[],      # triage doesn't produce key_points
                reason=parsed.reason,
            )

        else:
            assert isinstance(parsed, DeepAnalysisResult)
            # Deep analysis doesn't produce score/topics — those come from triage
            payload = AIAnalysisResponsePayload(
                relevance_score=0,    # placeholder; pipeline will preserve triage value
                confidence=None,
                topics=[],
                summary=parsed.summary,
                key_points=parsed.key_points,
                reason=None,
            )

        raw_resp: dict[str, Any] = {
            "model": self._settings.VERTEX_AI_MODEL,
            "stage": stage,
            "input_tokens": input_tokens,
            "output_text_tokens": output_text_tokens,
            "output_thought_tokens": output_thought_tokens,
        }

        call_meta: dict[str, Any] = {
            "stage": stage,
            "thinking_level": thinking_level,
            "thinking_budget": thinking_budget,
            "output_text_tokens": output_text_tokens,
            "output_thought_tokens": output_thought_tokens,
            "pricing_input_usd_per_million": self._settings.VERTEX_INPUT_USD_PER_MILLION_TOKENS,
            "pricing_output_usd_per_million": self._settings.VERTEX_OUTPUT_USD_PER_MILLION_TOKENS,
            "pricing_currency": "USD",
        }
        if extra_call_metadata:
            call_meta.update(extra_call_metadata)

        return AIProviderResult(
            success=True,
            payload=payload,
            raw_response=raw_resp,
            provider_name=self.provider_name,
            model=self._settings.VERTEX_AI_MODEL,
            input_chars=input_chars,
            output_chars=output_chars,
            input_tokens=input_tokens,
            output_tokens=total_output_tokens,
            estimated_cost_usd=estimated_cost,
            latency_ms=latency,
            call_metadata=call_meta,
        )
