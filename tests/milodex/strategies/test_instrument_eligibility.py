"""ADR 0016 instrument-eligibility enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from milodex.strategies.instrument_eligibility import (
    FORBIDDEN_ETP_SYMBOLS,
    InstrumentEligibilityError,
    reject_ineligible_instruments,
)
from milodex.strategies.loader import resolve_universe_ref

CONFIGS = Path(__file__).resolve().parents[3] / "configs"

# Real manifest ids verified at source (configs/universe_*.yaml).
REAL_MANIFEST_IDS = [
    "universe.index_etfs.v1",
    "universe.sector_etfs_spdr.v1",
    "universe.curated_largecap.v2",
    "universe.sp100_liquid.v1",
    "universe.spy_only.v1",
    "universe.gem_quartet.v1",
    "universe.phase1.curated.v1",
    "universe.liquid_etf_core.v1",
]


def test_forbidden_leveraged_etf_raises():
    with pytest.raises(InstrumentEligibilityError, match="TQQQ"):
        reject_ineligible_instruments(["SPY", "TQQQ"], source="test")


def test_forbidden_volatility_etp_raises_case_insensitive():
    with pytest.raises(InstrumentEligibilityError, match="UVXY"):
        reject_ineligible_instruments(["uvxy"], source="test")


def test_allowed_plain_etfs_and_stocks_pass():
    # Must not raise — covers distinctive tickers across the 7 manifests,
    # including near-collisions with denylist entries (SHY vs SH, SLV vs SVXY).
    reject_ineligible_instruments(
        ["SPY", "QQQ", "XLB", "XLRE", "TLT", "GLD", "SMH", "SOXX", "SLV", "SHY", "AAPL"],
        source="test",
    )


def test_empty_input_no_raise():
    reject_ineligible_instruments([], source="test")


def test_error_is_value_error_subclass():
    # Glob loaders catch ValueError; the eligibility error must remain catchable
    # there (documented silent-skip behaviour) — assert the inheritance.
    assert issubclass(InstrumentEligibilityError, ValueError)


def test_liquid_etf_core_resolves_to_17():
    symbols = resolve_universe_ref("universe.liquid_etf_core.v1", CONFIGS / "_dummy.yaml")
    assert len(symbols) == 17
    assert "SPY" in symbols and "GLD" in symbols and "TLT" in symbols
    assert not (set(symbols) & FORBIDDEN_ETP_SYMBOLS)


@pytest.mark.parametrize("manifest_id", REAL_MANIFEST_IDS)
def test_all_real_manifests_pass_eligibility(manifest_id):
    # resolve_universe_ref now runs the guard internally; every shipped manifest
    # must resolve without raising (regression guard for the denylist).
    symbols = resolve_universe_ref(manifest_id, CONFIGS / "_dummy.yaml")
    assert symbols  # non-empty


def test_resolve_universe_ref_rejects_forbidden_manifest(tmp_path):
    (tmp_path / "universe_bad_v1.yaml").write_text(
        'universe:\n  id: "universe.bad.v1"\n  etfs: ["SPY", "SQQQ"]\n  stocks: []\n',
        encoding="utf-8",
    )
    with pytest.raises(InstrumentEligibilityError, match="SQQQ"):
        resolve_universe_ref("universe.bad.v1", tmp_path / "_dummy.yaml")
