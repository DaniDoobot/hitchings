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
        """Resolve adapter for a Source model instance using its config or metadata."""
        config = source.config or {}
        adapter_code = config.get("adapter") or config.get("adapter_code")

        if adapter_code:
            return cls.get_adapter(str(adapter_code))

        # Fallback resolution by canonical source name or URL
        src_name = source.name.lower()
        src_url = (source.url or "").lower()

        if "kluwer" in src_name or "wolterskluwer" in src_url:
            return cls.get_adapter("kluwer_competition")
        elif "chillin" in src_name or "chillingcompetition" in src_url:
            return cls.get_adapter("chillin_competition")
        elif "almac" in src_name or "almacendederecho" in src_url:
            return cls.get_adapter("almacen_derecho")

        raise DirectWebUnknownAdapterError(
            f"Unable to resolve direct web adapter for Source id={source.id} name='{source.name}'"
        )

    @classmethod
    def list_adapters(cls) -> list[str]:
        """List all registered adapter codes."""
        return sorted(cls._adapters.keys())
