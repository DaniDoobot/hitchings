"""Central adapter registry for Direct Web Sources (Bloque 9B)."""

from __future__ import annotations

from typing import Optional
from app.models.source import Source
from app.providers.direct_web.adapters.almacen_derecho import AlmacenDerechoAdapter
from app.providers.direct_web.adapters.chillin_competition import ChillinCompetitionAdapter
from app.providers.direct_web.adapters.kluwer_competition import KluwerCompetitionAdapter
from app.providers.direct_web.base import BaseWebSourceAdapter


class DirectWebUnknownAdapterError(Exception):
    """Raised when no registered direct web adapter matches the requested source or code."""
    pass


class DirectWebAdapterRegistry:
    """Central registry mapping adapter codes to concrete BaseWebSourceAdapter instances."""

    _adapters: dict[str, BaseWebSourceAdapter] = {
        "kluwer_competition": KluwerCompetitionAdapter(),
        "chillin_competition": ChillinCompetitionAdapter(),
        "almacen_derecho": AlmacenDerechoAdapter(),
    }

    @classmethod
    def get_adapter(cls, code: str) -> BaseWebSourceAdapter:
        """Get registered adapter by unique adapter code."""
        adapter = cls._adapters.get(code.strip().lower())
        if not adapter:
            raise DirectWebUnknownAdapterError(f"No direct web adapter registered for code '{code}'")
        return adapter

    @classmethod
    def get_adapter_for_source(cls, source: Source) -> BaseWebSourceAdapter:
        """Resolve adapter for a Source model instance using its explicit config.

        Fails closed with DirectWebUnknownAdapterError if config.adapter is missing or unknown.
        No heuristic name/URL guessing.
        """
        config = source.config or {}
        adapter_code = config.get("adapter") or config.get("adapter_code")

        if not adapter_code:
            raise DirectWebUnknownAdapterError(
                f"Source id={source.id} name='{source.name}' has no 'adapter' configured in source.config"
            )

        return cls.get_adapter(str(adapter_code))

    @classmethod
    def has_adapter_for_source(cls, source: Source) -> bool:
        """Check if source has an explicit registered direct web adapter."""
        try:
            cls.get_adapter_for_source(source)
            return True
        except Exception:
            return False

    @classmethod
    def list_adapters(cls) -> list[str]:
        """List all registered adapter codes."""
        return sorted(cls._adapters.keys())
