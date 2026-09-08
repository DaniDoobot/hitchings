"""Unit tests for the v4 baseline backfill planner (Bloque 7G.1)."""

import pytest
from app.core.config import get_settings
from scripts.plan_v4_backfill import (
    calculate_historical_costs,
    calculate_token_model_costs,
    build_v4_backfill_plan,
)


def test_planner_pricing_settings():
    """Verify that settings provide the official configured rates for Gemini 3.8 Flash."""
    settings = get_settings()
    assert settings.GEMINI_INPUT_USD_PER_MILLION_TOKENS == 0.75
    assert settings.GEMINI_OUTPUT_USD_PER_MILLION_TOKENS == 3.75


def test_calculate_historical_costs_monotonic():
    """Verify historical cost estimation scales monotonically across scenarios."""
    synthetic_pending = [
        {"source_name": "CNMC - Noticias", "content_chars": 3500, "sufficiency": "full"},
        {"source_name": "Competition Appeal Tribunal - Judgments", "content_chars": 25000, "sufficiency": "full"},
        {"source_name": "Competition Appeal Tribunal - Judgments", "content_chars": 200, "sufficiency": "partial"},
        {"source_name": "Court of Justice of the European Union - Case Law", "content_chars": 50000, "sufficiency": "full"},
        {"source_name": "European Commission - Competition Policy", "content_chars": 4000, "sufficiency": "full"},
    ]

    res = calculate_historical_costs(synthetic_pending)
    assert "low" in res and "expected" in res and "high" in res
    assert 0 < res["low"] < res["expected"] < res["high"]
    assert res["calls_low"][0] == 5  # 5 triage calls


def test_calculate_token_model_costs_monotonic_and_scaling():
    """Verify token-model estimation scales with text size and doubles under 2027 rates."""
    synthetic_pending = [
        {"source_name": "CNMC - Noticias", "content_chars": 3500, "sufficiency": "full"},
        {"source_name": "Competition Appeal Tribunal - Judgments", "content_chars": 70000, "sufficiency": "full"},
    ]

    res_current = calculate_token_model_costs(synthetic_pending, input_rate_usd=0.75, output_rate_usd=3.75)
    res_2027 = calculate_token_model_costs(synthetic_pending, input_rate_usd=1.50, output_rate_usd=7.50)

    assert 0 < res_current["low"] < res_current["expected"] < res_current["high"]
    assert pytest.approx(res_2027["low"], rel=1e-5) == res_current["low"] * 2.0
    assert pytest.approx(res_2027["expected"], rel=1e-5) == res_current["expected"] * 2.0
    assert pytest.approx(res_2027["high"], rel=1e-5) == res_current["high"] * 2.0


def test_insufficient_entries_skip_deep_in_both_models():
    """Ensure entries marked insufficient do not contribute to deep analysis costs."""
    normal_entry = [{"source_name": "CNMC - Noticias", "content_chars": 200, "sufficiency": "full"}]
    insufficient_entry = [{"source_name": "CNMC - Noticias", "content_chars": 200, "sufficiency": "insufficient"}]

    hist_norm = calculate_historical_costs(normal_entry)
    hist_insuf = calculate_historical_costs(insufficient_entry)
    assert hist_norm["expected"] > hist_insuf["expected"]
    assert hist_insuf["calls_expected"][1] == 0  # 0 deep calls

    tok_norm = calculate_token_model_costs(normal_entry, 0.75, 3.75)
    tok_insuf = calculate_token_model_costs(insufficient_entry, 0.75, 3.75)
    assert tok_norm["expected"] > tok_insuf["expected"]
