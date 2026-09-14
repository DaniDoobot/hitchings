"""Extractors package for source-specific HTML extraction."""
from app.providers.extractors.cnmc import CNMCNewsExtractor
from app.providers.extractors.european_commission import EuropeanCommissionExtractor
from app.providers.extractors.competition_appeal_tribunal import CompetitionAppealTribunalExtractor
from app.providers.extractors.curia import CuriaCaseLawExtractor

from app.providers.extractors.cma import CMAExtractor
from app.providers.extractors.autorite_concurrence import (
    AutoriteConcurrenceExtractor,
    ADLCDiscoveryMetrics,
    ADLC_SOURCE_NAME,
    ADLC_BASE_URL,
    classify_official_act_id,
)

__all__ = [
    "CNMCNewsExtractor",
    "EuropeanCommissionExtractor",
    "CompetitionAppealTribunalExtractor",
    "CuriaCaseLawExtractor",
    "CMAExtractor",
    "AutoriteConcurrenceExtractor",
    "ADLCDiscoveryMetrics",
    "ADLC_SOURCE_NAME",
    "ADLC_BASE_URL",
    "classify_official_act_id",
]

