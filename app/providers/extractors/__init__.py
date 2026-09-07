"""Extractors package for source-specific HTML extraction."""
from app.providers.extractors.cnmc import CNMCNewsExtractor
from app.providers.extractors.european_commission import EuropeanCommissionExtractor
from app.providers.extractors.competition_appeal_tribunal import CompetitionAppealTribunalExtractor
from app.providers.extractors.curia import CuriaCaseLawExtractor

__all__ = [
    "CNMCNewsExtractor",
    "EuropeanCommissionExtractor",
    "CompetitionAppealTribunalExtractor",
    "CuriaCaseLawExtractor",
]

