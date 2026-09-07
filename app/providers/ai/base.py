"""Base abstraction and dataclasses for AI analysis providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any

from app.models.analysis import AnalysisPromptVersion
from app.models.entry import Entry
from app.schemas.analysis import AIAnalysisResponsePayload


@dataclass
class AIProviderResult:
    """Standardized response container returned by any AI provider."""

    success: bool
    payload: Optional[AIAnalysisResponsePayload] = None
    raw_response: Optional[dict[str, Any]] = None
    provider_name: str = ""
    model: str = ""
    input_chars: int = 0
    output_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: int = 0
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    call_metadata: dict[str, Any] = field(default_factory=dict)


class BaseAIProvider(ABC):
    """Abstract base class for all AI analysis providers (Mock, OpenAI, Anthropic, etc.)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier (e.g. 'mock', 'openai', 'anthropic')."""
        pass

    @abstractmethod
    async def analyze(
        self,
        prompt_version: AnalysisPromptVersion,
        entry: Entry,
        matrix_snapshot: dict[str, Any],
    ) -> AIProviderResult:
        """Execute AI analysis on an entry using the given prompt version and matrix snapshot."""
        pass
