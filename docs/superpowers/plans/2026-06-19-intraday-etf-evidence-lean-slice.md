# Intraday ETF Evidence — "Lean Slice" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the provider-agnostic, reusable subset of the "Liquid ETF Price-Action Evidence v1" lane — universe hardening, an intraday-aware data-readiness report, an IEX price-fidelity gate, and three deterministic baseline strategies — without touching `risk/`, `execution/`, `promotion/`, the event-store schema, or the frozen `StrategyContext`.

**Architecture:** Five independently-reviewable PRs on the `intraday-etf-evidence-lean-slice` worktree. PR1 makes ADR 0016's instrument whitelist real (it is prose-only today) at the universe-resolution chokepoint. PR2–PR4 build an intraday-aware readiness scanner + CLI + an IEX-vs-consolidated price-fidelity cross-check. PR5 adds three baseline strategies that auto-register and run through the existing walk-forward batch path. **Do NOT merge** — the operator reviews in a fresh session.

**Tech Stack:** Python 3.11+, pandas, pytest (xdist), ruff. yfinance (already a dep) for the consolidated daily reference. No new runtime dependencies.

---

## Context & non-negotiables (carry this — it is load-bearing)

Two grounded findings justify the lean scope (full citations in `docs/reviews/2026-06-18-intraday-etf-evidence-hardening-feedback.md`):

1. **"Price-action only" does NOT make IEX trustworthy.** IEX is ~2.5% of consolidated volume; it under-samples session high/low extremes and never sees the opening/closing auction (`docs/adr/0017-data-source-hierarchy.md:14,32,60`). Baselines cannot detect this — candidate and null run on the *same* biased bars, so the bias cancels in the comparison. **Every v1 verdict must be explicitly labeled IEX-exploratory / non-durable.** The PR4 fidelity gate is the only thing standing between an inward IEX price bias and a "passed" verdict.
2. **The binding constraint is the data provider.** The consolidated/SIP feed is deferred (ADR 0017); `alpaca_provider` is IEX-hardcoded. This slice is the tooling that becomes durable the moment real data exists and improves the *existing canaries'* evidence today.

**Explicitly OUT of this slice** (do not build): anything in `risk/`, `execution/`, `promotion/`; event-store migrations; changes to the frozen `StrategyContext` (`strategies/base.py`); random matched-exposure (E-PR2); the experiment registry (Workstream F); evidence-report composition (G); new candidate strategies (H); any live/micro_live promotion.

**Operator process:** spec → adversarial review of spec → fold → implement each PR with TDD → `/ponytail-review` each PR → `risk-invariant-reviewer` on PR1. No merge.

---

## Grounded facts (verified at source 2026-06-19, worktree HEAD b9f39f5)

- `resolve_universe_ref` (`src/milodex/strategies/loader.py:119-156`) globs `configs/universe_*.yaml`, matches on `universe.id`, returns `tuple(sorted(set(symbols)))` of the `etfs`+`stocks` union. **No eligibility check today.** Called by `loader.load` (only when `universe_ref` set, `:99-100`), `data fetch-universe` (`cli/commands/data.py:172`).
- Inline-universe configs return from `_load_universe` (`loader.py:491-515`, `:509`) and **bypass `resolve_universe_ref` entirely** — so an inline `universe: [TQQQ]` is unchecked by the ref-path guard.
- ADR 0016:42 claims "the config validator refuses any strategy instance whose declared universe contains an instrument outside the whitelist." This enforcement **does not exist in code**. PR1 makes it real (denylist-based).
- 7 universe manifests exist: `gem_quartet_v1, index_etfs_v1, sector_etfs_spdr_v1, sp100_liquid_v1, spy_only_v1, curated_largecap_v2, phase1_v1`.
- `bar_quality.py` (`src/milodex/data/bar_quality.py`, 281 lines) is **daily-shaped**: it collapses timestamps to `.dt.date` (`:106-109,127,142`). `_is_non_negative_finite` (`:263`) accepts `0.0` (zero-volume passes silently). `DataQualityIssue`/`DataQualityReport`/`DataQualitySeverity` are the issue/report scaffolding to reuse.
- `_session_intraday.py` exports the session primitives: `MARKET_OPEN_ET` (`:31`), `session_close_offset_minutes` (`:207`, 390 full / 210 half), `regular_session_bars` (`:216`), `is_half_day` (`:69`), `session_date_et` (`:74`), `_et_time_offset_minutes` (`:306`).
- `yahoo_provider.py` is **VIX-only by contract** (`:7-9`: "Do not extend this module to fetch arbitrary symbols"). The IEX fidelity gate gets its OWN module, not a violation of that guard. `_reshape` (`:111`) is generic and can be lifted/shared.
- `bench_unconditional_intraday_long.py` reads `context.universe` (`:69`) but trades only `universe_symbols[0]` (`:72`) — already works for any *single* symbol. Per design decision #2, "across 17 ETFs" = 17 single-symbol backtests via the batch path, **NOT new multi-symbol strategy logic.**
- `build_default_registry` (`loader.py:419`) auto-discovers every concrete `Strategy` subclass under `milodex.strategies` — **no allowlist, no loader edit** for a new strategy class. New strategy = class + config.
- CLI `data` command: `register` (`cli/commands/data.py:25`), dispatch in `run` (`:94-100`), `_run_fetch_universe` (`:169`) is the shape to mirror. `TIMEFRAME_CHOICES`, `parse_iso_date` come from `cli/_shared`.
- Test harness `tests/milodex/data/test_bar_quality.py` `_row` (`:17-34`) is day-granular — readiness tests need NEW sub-day fixtures.

**Test invocation on this worktree** (PYTHONPATH shadow — the `.venv` editable install points at the MAIN checkout):
```powershell
Set-Location 'C:\Users\zdm80\Milodex\.worktrees\intraday-etf-evidence-lean-slice'
$env:PYTHONPATH = 'C:\Users\zdm80\Milodex\.worktrees\intraday-etf-evidence-lean-slice\src'
& 'C:\Users\zdm80\Milodex\.venv\Scripts\python.exe' -m pytest <args>
```
Baseline: **2743 passed, 1 skipped, 4 xfailed**. A clean run shows `1 skipped` (design-system QML smoke), NOT `1 failed`.

---

## File structure map

**PR1 — C-light**
- Create: `configs/universe_liquid_etf_core_v1.yaml` — frozen 17-ETF manifest.
- Create: `src/milodex/strategies/instrument_eligibility.py` — ADR 0016 denylist + `reject_ineligible_instruments`.
- Modify: `src/milodex/strategies/loader.py` — call the guard in `resolve_universe_ref` AND `_load_universe`.
- Modify: `docs/adr/0016-phase1-instrument-whitelist.md` — enforcement-is-denylist note.
- Create: `tests/milodex/strategies/test_instrument_eligibility.py`.

**PR2 — D core readiness scanner**
- Create: `src/milodex/data/intraday_readiness.py` — `ReadinessReport`, `SymbolReadiness`, `scan_intraday_readiness`, content hash.
- Create: `tests/milodex/data/test_intraday_readiness.py` — sub-day fixtures.

**PR3 — D CLI**
- Modify: `src/milodex/cli/commands/data.py` — `data readiness` subparser + dispatch + `_run_readiness`.
- Create/extend: `tests/milodex/cli/commands/test_data_readiness.py`.

**PR4 — IEX price-fidelity gate**
- Create: `src/milodex/data/consolidated_reference.py` — generalized free daily OHLC fetch (Yahoo) + `cross_check_session_extremes`.
- Modify: `src/milodex/data/intraday_readiness.py` — optional `reference_daily_by_symbol` param; emit inward-bias issue + demote `feed_label`.
- Modify: `src/milodex/cli/commands/data.py` — `--cross-check-reference` flag wiring.
- Create: `tests/milodex/data/test_consolidated_reference.py`; extend readiness tests.

**PR5 — baselines as strategies**
- Create: `src/milodex/strategies/bench_no_trade.py` + `configs/bench_no_trade_spy_v1.yaml`.
- Create: `src/milodex/strategies/bench_time_of_day_null.py` + `configs/bench_time_of_day_null_spy_v1.yaml`.
- Create: `tests/milodex/strategies/test_bench_no_trade.py`, `test_bench_time_of_day_null.py`, and a per-symbol test for the existing unconditional-long.

---

## Task PR1: C-light — make ADR 0016 enforcement real

**Files:**
- Create: `configs/universe_liquid_etf_core_v1.yaml`
- Create: `src/milodex/strategies/instrument_eligibility.py`
- Modify: `src/milodex/strategies/loader.py` (`resolve_universe_ref` ~:151, `_load_universe` ~:509, imports ~:15)
- Modify: `docs/adr/0016-phase1-instrument-whitelist.md`
- Test: `tests/milodex/strategies/test_instrument_eligibility.py`

- [ ] **Step 1: Create the frozen universe manifest**

`configs/universe_liquid_etf_core_v1.yaml`:
```yaml
# Liquid ETF Core Universe — v1
#
# Frozen universe manifest (ADR 0015 frozen-manifest principle). Strategies
# reference this by id; they do not inline their own lists. Any membership
# change creates a new version — never edited in place.
#
# Purpose: the provider-agnostic price-action evidence lane (intraday ETF
# canaries + baselines) runs per-symbol across this set. 17 highly-liquid,
# plain-vanilla, survivorship-immune ETFs. Subject to ADR 0016 (now enforced
# by src/milodex/strategies/instrument_eligibility.py).

universe:
  id: "universe.liquid_etf_core.v1"
  version: 1
  slippage_pct: 0.0005  # 5 bps — conservative for the thinner sector/commodity ETFs intraday
  # Survivorship-immune: every member launched well before Phase 1 windows and
  # none has been delisted (broad index 1993-2000; Select Sector SPDRs 1998,
  # XLRE 2015, XLC 2018; TLT 2002; GLD 2004). See docs/RISK_POLICY.md
  # "Known Backtest Limitations and Biases".
  survivorship_corrected: true
  description: >
    Seventeen highly-liquid plain-vanilla ETFs: four broad-market index ETFs
    (SPY QQQ IWM DIA), the eleven GICS-sector Select Sector SPDRs, and two
    macro hedges (TLT long Treasuries, GLD gold). The reusable universe for
    the intraday price-action evidence lane — broad enough to test whether an
    intraday edge generalizes beyond SPY, all on instruments with clean
    corporate actions and reliable bar structure. Subject to ADR 0016.

  etfs:
    - "SPY"   # S&P 500
    - "QQQ"   # Nasdaq-100
    - "IWM"   # Russell 2000
    - "DIA"   # Dow Jones Industrial Average
    - "XLB"   # Materials
    - "XLC"   # Communication Services
    - "XLE"   # Energy
    - "XLF"   # Financials
    - "XLI"   # Industrials
    - "XLK"   # Technology
    - "XLP"   # Consumer Staples
    - "XLRE"  # Real Estate
    - "XLU"   # Utilities
    - "XLV"   # Health Care
    - "XLY"   # Consumer Discretionary
    - "TLT"   # 20+ Year Treasury
    - "GLD"   # Gold

  stocks: []
```

- [ ] **Step 2: Write the failing test for the denylist function**

`tests/milodex/strategies/test_instrument_eligibility.py`:
```python
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


def test_forbidden_leveraged_etf_raises():
    with pytest.raises(InstrumentEligibilityError, match="TQQQ"):
        reject_ineligible_instruments(["SPY", "TQQQ"], source="test")


def test_forbidden_volatility_etp_raises_case_insensitive():
    with pytest.raises(InstrumentEligibilityError, match="UVXY"):
        reject_ineligible_instruments(["uvxy"], source="test")


def test_allowed_plain_etfs_pass():
    # Must not raise — covers every distinctive ticker across the 7 manifests.
    reject_ineligible_instruments(
        ["SPY", "QQQ", "XLB", "XLRE", "TLT", "GLD", "SMH", "SOXX", "SLV", "SHY", "AAPL"],
        source="test",
    )


def test_empty_input_no_raise():
    reject_ineligible_instruments([], source="test")


def test_liquid_etf_core_resolves_to_17():
    symbols = resolve_universe_ref("universe.liquid_etf_core.v1", CONFIGS / "_dummy.yaml")
    assert len(symbols) == 17
    assert "SPY" in symbols and "GLD" in symbols and "TLT" in symbols
    # All members survive the eligibility guard.
    assert not (set(symbols) & FORBIDDEN_ETP_SYMBOLS)


@pytest.mark.parametrize(
    "manifest_id",
    [
        "universe.index_etfs.v1",
        "universe.sector_etfs_spdr.v1",
        "universe.curated_largecap.v2",
        "universe.sp100_liquid.v1",
        "universe.spy_only.v1",
        "universe.gem_quartet.v1",
        "universe.phase1.v1",
        "universe.liquid_etf_core.v1",
    ],
)
def test_all_real_manifests_pass_eligibility(manifest_id):
    # resolve_universe_ref now runs the guard internally; every shipped manifest
    # must resolve without raising (regression guard for the denylist).
    symbols = resolve_universe_ref(manifest_id, CONFIGS / "_dummy.yaml")
    assert symbols  # non-empty


def test_resolve_universe_ref_rejects_forbidden_manifest(tmp_path):
    (tmp_path / "universe_bad_v1.yaml").write_text(
        "universe:\n  id: \"universe.bad.v1\"\n  etfs: [\"SPY\", \"SQQQ\"]\n  stocks: []\n",
        encoding="utf-8",
    )
    with pytest.raises(InstrumentEligibilityError, match="SQQQ"):
        resolve_universe_ref("universe.bad.v1", tmp_path / "_dummy.yaml")
```
> Before wiring: verify the real manifest ids. `universe.curated_largecap.v2` / `universe.sp100_liquid.v1` / `universe.gem_quartet.v1` / `universe.phase1.v1` ids must match the `universe.id` field inside each file — open each and confirm the exact id string before running (the parametrize list must be exact or the guard test is vacuous).

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/milodex/strategies/test_instrument_eligibility.py -q`
Expected: FAIL — `ModuleNotFoundError: milodex.strategies.instrument_eligibility`.

- [ ] **Step 4: Implement `instrument_eligibility.py`**

`src/milodex/strategies/instrument_eligibility.py`:
```python
"""Phase-1 instrument eligibility — reject leveraged/inverse/volatility ETPs.

ADR 0016 declares the Phase-1 whitelist (long-only U.S. common stock + plain-
vanilla ETFs) and *claims* (ADR 0016:42) the universe-manifest load enforces it.
It did not: the prose described enforcement that no code implemented. This module
makes the enforcement real at the universe-resolution chokepoints in
``strategies/loader.py``.

Mechanism: a hardcoded curated DENYLIST of known leveraged (2x/3x/-1x/-3x),
inverse, and volatility ETPs. No instrument-type metadata exists on symbols in
Phase 1 (no SIP / reference feed — ADR 0017), so an allowlist-by-type is not
feasible yet.

# ponytail: curated denylist of well-known leveraged/inverse/vol ETPs — NOT
# exhaustive. A newly-launched leveraged ETP not on this list would pass. Upgrade
# path: replace with an instrument-type allowlist when a reference-data feed
# (SIP / Massive, ADR 0017) supplies asset_class / leverage metadata.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Known leveraged / inverse / volatility ETPs forbidden by ADR 0016. Uppercase;
#: matched case-insensitively. Curated, not exhaustive (see module docstring).
FORBIDDEN_ETP_SYMBOLS: frozenset[str] = frozenset(
    {
        # Leveraged long broad-index
        "SSO", "UPRO", "SPXL", "QLD", "TQQQ", "UDOW", "UWM", "URTY", "DDM",
        # Inverse / leveraged-inverse broad-index
        "SH", "SDS", "SPXU", "SPXS", "PSQ", "QID", "SQQQ", "DOG", "DXD",
        "SDOW", "RWM", "TWM", "TZA", "TNA",
        # Sector / thematic leveraged + inverse
        "SOXL", "SOXS", "FAS", "FAZ", "LABU", "LABD", "TECL", "TECS",
        "NUGT", "DUST", "JNUG", "JDST", "ERX", "ERY", "GUSH", "DRIP",
        "YINN", "YANG", "BOIL", "KOLD", "UCO", "SCO",
        # Leveraged bonds
        "TMF", "TMV", "TBT", "UBT", "TYD", "TYO",
        # Volatility ETPs (decay breaks naive backtest — ADR 0016:34)
        "VXX", "VIXY", "UVXY", "SVXY", "VIXM", "SVIX", "UVIX", "VXZ",
    }
)


class InstrumentEligibilityError(ValueError):
    """Raised when a universe contains an instrument forbidden by ADR 0016.

    Subclasses ``ValueError`` so existing config-load error handling (which
    catches ``ValueError``) treats an ineligible universe like any other
    invalid config.
    """


def reject_ineligible_instruments(symbols: Iterable[str], *, source: str) -> None:
    """Raise :class:`InstrumentEligibilityError` if any symbol is a forbidden ETP.

    ``source`` names the manifest / universe for the error message. Symbols are
    upper-cased and matched against :data:`FORBIDDEN_ETP_SYMBOLS`. Empty input is
    a no-op.
    """
    forbidden = sorted(
        s
        for s in {str(sym).strip().upper() for sym in symbols}
        if s in FORBIDDEN_ETP_SYMBOLS
    )
    if forbidden:
        msg = (
            f"{source}: universe contains instrument(s) forbidden by ADR 0016 "
            f"(leveraged/inverse/volatility ETP): {', '.join(forbidden)}. "
            f"Phase 1 trades long-only common stock and plain-vanilla ETFs only."
        )
        raise InstrumentEligibilityError(msg)
```

- [ ] **Step 5: Wire the guard into both universe-resolution paths in `loader.py`**

Add import near `loader.py:15` (after the `milodex.strategies.base` import):
```python
from milodex.strategies.instrument_eligibility import reject_ineligible_instruments
```
In `resolve_universe_ref`, replace the success return (`:151`):
```python
        reject_ineligible_instruments(symbols, source=str(manifest_path))
        return tuple(sorted(set(symbols)))
```
In `_load_universe`, the inline-universe return (`:509`) — close the inline hole:
```python
        resolved = tuple(symbol.strip().upper() for symbol in universe)
        reject_ineligible_instruments(resolved, source=str(path))
        return resolved, None
```
> Why both: ADR 0016:42's intended invariant is "no strategy's declared universe contains a forbidden instrument." Guarding only the ref path leaves a trivial bypass (`universe: [TQQQ]` inline). A half-real enforcement repeats exactly the failure this PR fixes. The full test suite + green baseline catch any existing config that would newly raise.

- [ ] **Step 6: Run the new test + a config-loading regression slice**

Run:
```
python -m pytest tests/milodex/strategies/test_instrument_eligibility.py tests/milodex/strategies/ tests/milodex/cli/ -q
```
Expected: PASS. If any existing manifest/config newly raises, a manifest contains a denied ticker — investigate (do NOT loosen the denylist to make a real violation pass; confirm the ticker against ADR 0016 first).

- [ ] **Step 7: Add the ADR 0016 enforcement note**

Append to `docs/adr/0016-phase1-instrument-whitelist.md` (new section before `## Links`):
```markdown
## Enforcement (added 2026-06-19)

The "Consequences" claim above — that the config validator refuses a universe
containing a forbidden instrument — described an *intended* invariant that no
code implemented until now. Enforcement is implemented in
`src/milodex/strategies/instrument_eligibility.py` and called from both
universe-resolution paths in `strategies/loader.py` (`resolve_universe_ref` for
`universe_ref` manifests and `_load_universe` for inline universes).

The mechanism is a **curated denylist** of known leveraged / inverse / volatility
ETPs, NOT the instrument-type allowlist the prose implies. No instrument-type
metadata exists on symbols in Phase 1 (no SIP / reference feed — ADR 0017), so a
type-based allowlist is not yet feasible. The denylist is deliberately
incomplete: a newly-launched leveraged ETP not on the list would pass. The
upgrade path to a true type allowlist opens when a reference-data feed supplies
asset-class / leverage metadata.
```

- [ ] **Step 8: Run full suite + commit**

Run the full suite (expect `2743+ passed, 1 skipped`). Then:
```bash
git add configs/universe_liquid_etf_core_v1.yaml src/milodex/strategies/instrument_eligibility.py src/milodex/strategies/loader.py docs/adr/0016-phase1-instrument-whitelist.md tests/milodex/strategies/test_instrument_eligibility.py
git commit -m "feat(strategies): enforce ADR 0016 instrument whitelist + liquid_etf_core universe"
```
Then dispatch `risk-invariant-reviewer` (universe-load hot path) and `/ponytail-review`.

---

## Task PR2: intraday-aware data-readiness scanner

**Files:**
- Create: `src/milodex/data/intraday_readiness.py`
- Test: `tests/milodex/data/test_intraday_readiness.py`

**Design:** A new module (NOT an edit to the daily-shaped `bar_quality.py`, which the backtest pre-flight depends on). Reuses `DataQualityIssue`/`DataQualitySeverity` from `bar_quality` and session primitives from `_session_intraday`. Per symbol it groups bars into ET sessions and checks intra-session completeness against an EXPECTED per-session bar grid.

> **Import-direction check (do at Step 4):** confirm `import milodex.strategies._session_intraday` does not trigger a heavy/cyclic `milodex.strategies.__init__`. Read `src/milodex/strategies/__init__.py` first. If it imports providers or is heavy, import the three helpers lazily *inside* `scan_intraday_readiness` rather than at module top. (`data/` importing `strategies/` is a known layering smell — flagged for the operator; the clean fix is lifting the pure-time helpers to a shared module, out of slice scope.)

- [ ] **Step 1: Write the failing test (clean intraday session passes; content hash stable)**

`tests/milodex/data/test_intraday_readiness.py`:
```python
"""Intraday-aware data-readiness scanner."""

from __future__ import annotations

from datetime import date

import pandas as pd

from milodex.data.intraday_readiness import scan_intraday_readiness
from milodex.data.models import BarSet


def _session_5min(day: str, *, n_bars: int = 78, volume: float = 1_000.0,
                  open_offset_skip: int = 0, zero_vol_bars: int = 0) -> list[dict]:
    """Build ``n_bars`` 5-min bars starting 9:30 ET on ``day`` (full session = 78)."""
    start = pd.Timestamp(f"{day} 09:30", tz="America/New_York")
    rows = []
    for i in range(open_offset_skip, n_bars):
        ts = (start + pd.Timedelta(minutes=5 * i)).tz_convert("UTC")
        vol = 0.0 if i < zero_vol_bars else volume
        rows.append({
            "timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": vol, "vwap": 100.2,
        })
    return rows


def _barset(rows: list[dict]) -> BarSet:
    return BarSet(pd.DataFrame(rows))


def test_clean_full_session_passes():
    report = scan_intraday_readiness(
        {"SPY": _barset(_session_5min("2025-06-17"))},
        timeframe_minutes=5,
        requested_start=date(2025, 6, 17),
        requested_end=date(2025, 6, 17),
        feed_label="fallback",
    )
    assert report.status == "pass"
    sr = report.per_symbol[0]
    assert sr.expected_bars == 78 and sr.observed_bars == 78
    assert sr.content_hash  # non-empty


def test_content_hash_is_order_and_dtype_invariant():
    rows = _session_5min("2025-06-17")
    shuffled = list(reversed(rows))
    df32 = pd.DataFrame(rows).astype({"open": "float32", "close": "float32"})
    h_plain = scan_intraday_readiness(
        {"SPY": _barset(rows)}, timeframe_minutes=5,
        requested_start=date(2025, 6, 17), requested_end=date(2025, 6, 17),
    ).per_symbol[0].content_hash
    h_shuffled = scan_intraday_readiness(
        {"SPY": _barset(shuffled)}, timeframe_minutes=5,
        requested_start=date(2025, 6, 17), requested_end=date(2025, 6, 17),
    ).per_symbol[0].content_hash
    h_f32 = scan_intraday_readiness(
        {"SPY": _barset(df32)}, timeframe_minutes=5,
        requested_start=date(2025, 6, 17), requested_end=date(2025, 6, 17),
    ).per_symbol[0].content_hash
    assert h_plain == h_shuffled == h_f32


def test_zero_volume_bars_warn():
    report = scan_intraday_readiness(
        {"SPY": _barset(_session_5min("2025-06-17", zero_vol_bars=3))},
        timeframe_minutes=5,
        requested_start=date(2025, 6, 17), requested_end=date(2025, 6, 17),
    )
    assert "intraday_zero_volume_bars" in report.to_dict()["issue_codes"]


def test_missing_session_open_bar_warns():
    # Drop the 9:30 bar (start at offset 1 = 9:35).
    report = scan_intraday_readiness(
        {"SPY": _barset(_session_5min("2025-06-17", open_offset_skip=1))},
        timeframe_minutes=5,
        requested_start=date(2025, 6, 17), requested_end=date(2025, 6, 17),
    )
    codes = report.to_dict()["issue_codes"]
    assert "intraday_missing_session_open_bar" in codes
    assert "intraday_session_coverage_below_threshold" in codes


def test_half_day_expected_count_is_42():
    # 2025-11-28 is a known half-day (210 min / 5 = 42 bars).
    report = scan_intraday_readiness(
        {"SPY": _barset(_session_5min("2025-11-28", n_bars=42))},
        timeframe_minutes=5,
        requested_start=date(2025, 11, 28), requested_end=date(2025, 11, 28),
    )
    assert report.per_symbol[0].expected_bars == 42
    assert report.status == "pass"


def test_stale_dataset_tail_warns():
    report = scan_intraday_readiness(
        {"SPY": _barset(_session_5min("2025-06-10"))},
        timeframe_minutes=5,
        requested_start=date(2025, 6, 10), requested_end=date(2025, 6, 17),
    )
    assert "intraday_stale_dataset_tail" in report.to_dict()["issue_codes"]


def test_feed_label_recorded():
    report = scan_intraday_readiness(
        {"SPY": _barset(_session_5min("2025-06-17"))},
        timeframe_minutes=5,
        requested_start=date(2025, 6, 17), requested_end=date(2025, 6, 17),
        feed_label="fallback",
    )
    assert report.to_dict()["feed_label"] == "fallback"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/milodex/data/test_intraday_readiness.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the scanner**

`src/milodex/data/intraday_readiness.py`:
```python
"""Intraday-aware data-readiness scanner.

``bar_quality.scan_backtest_bars`` is daily-shaped (it collapses timestamps to
calendar dates). Intraday evidence needs per-session completeness against an
EXPECTED bar grid (390 regular-session minutes full / 210 half-day, divided by
the bar timeframe). This scanner answers "is this intraday data good enough to
support an evidence verdict?" — distinct from the backtest pre-flight integrity
check, which it deliberately does not replace.

Checks (all WARNING severity — readiness informs, it does not block a backtest):
  - intraday_session_coverage_below_threshold  (observed/expected per session)
  - intraday_missing_session_open_bar / _close_bar
  - intraday_zero_volume_bars                   (volume == 0 in a regular bar)
  - intraday_intra_session_gap                  (>1 consecutive missing bar)
  - intraday_stale_dataset_tail                 (data ends before requested_end)
  - iex_inward_price_bias                        (added in PR4)

Per symbol it also records a deterministic content hash (canonicalized OHLCV —
NOT the DataFrame repr) and the feed-quality label.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from milodex.data.bar_quality import DataQualityIssue, DataQualitySeverity
from milodex.data.models import BarSet
from milodex.strategies._session_intraday import (
    regular_session_bars,
    session_close_offset_minutes,
    session_date_et,
    _et_time_offset_minutes,  # noqa: PLC2701  (pure time helper; see import-direction note)
)

#: A dataset whose latest session is more than this far before requested_end is
#: flagged stale. One week mirrors bar_quality.REQUESTED_WINDOW_EDGE_TOLERANCE.
STALE_TAIL_TOLERANCE = timedelta(days=7)

#: Per-session observed/expected coverage below this is a warning.
SESSION_COVERAGE_FLOOR = 0.90


@dataclass(frozen=True)
class SymbolReadiness:
    symbol: str
    content_hash: str
    sessions_observed: int
    expected_bars: int
    observed_bars: int

    @property
    def coverage_pct(self) -> float:
        return (self.observed_bars / self.expected_bars * 100.0) if self.expected_bars else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "content_hash": self.content_hash,
            "sessions_observed": self.sessions_observed,
            "expected_bars": self.expected_bars,
            "observed_bars": self.observed_bars,
            "coverage_pct": round(self.coverage_pct, 1),
        }


@dataclass(frozen=True)
class ReadinessReport:
    requested_start: date
    requested_end: date
    timeframe_minutes: int
    feed_label: str
    scanned_symbols: tuple[str, ...]
    per_symbol: tuple[SymbolReadiness, ...]
    issues: tuple[DataQualityIssue, ...] = field(default_factory=tuple)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity is DataQualitySeverity.WARNING)

    @property
    def blocker_count(self) -> int:
        return sum(1 for i in self.issues if i.severity is DataQualitySeverity.BLOCKER)

    @property
    def status(self) -> str:
        if self.blocker_count:
            return "fail"
        if self.warning_count:
            return "pass_with_warnings"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "timeframe_minutes": self.timeframe_minutes,
            "feed_label": self.feed_label,
            "scanned_symbols": list(self.scanned_symbols),
            "warning_count": self.warning_count,
            "blocker_count": self.blocker_count,
            "issue_codes": [i.code for i in self.issues],
            "issues": [i.to_dict() for i in self.issues],
            "per_symbol": [s.to_dict() for s in self.per_symbol],
        }


def _warn(symbol: str, code: str, message: str, context: dict[str, Any]) -> DataQualityIssue:
    return DataQualityIssue(
        code=code, severity=DataQualitySeverity.WARNING, symbol=symbol,
        message=message, context=context,
    )


def _content_hash(df: pd.DataFrame) -> str:
    """Deterministic sha256 of canonicalized OHLCV — order- and dtype-invariant."""
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    sub = pd.DataFrame({c: df[c] for c in cols})
    sub["timestamp"] = pd.to_datetime(sub["timestamp"], utc=True).astype("int64")
    for c in ("open", "high", "low", "close", "volume"):
        sub[c] = pd.to_numeric(sub[c], errors="coerce").round(6)
    sub = sub.sort_values("timestamp").reset_index(drop=True)
    payload = sub.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def scan_intraday_readiness(
    bars_by_symbol: dict[str, BarSet],
    *,
    timeframe_minutes: int,
    requested_start: date,
    requested_end: date,
    feed_label: str = "fallback",
) -> ReadinessReport:
    if timeframe_minutes <= 0:
        raise ValueError("timeframe_minutes must be positive")

    issues: list[DataQualityIssue] = []
    per_symbol: list[SymbolReadiness] = []

    for symbol in sorted(bars_by_symbol):
        df = bars_by_symbol[symbol].to_dataframe()
        if df.empty:
            issues.append(_warn(symbol, "intraday_no_bars", f"{symbol} has no bars.", {}))
            per_symbol.append(SymbolReadiness(symbol, "", 0, 0, 0))
            continue

        ts = pd.to_datetime(df["timestamp"], utc=True)
        session_days = sorted({session_date_et(t) for t in ts})
        total_expected = 0
        total_observed = 0
        for day in session_days:
            close_off = session_close_offset_minutes(day)
            expected = close_off // timeframe_minutes
            session = regular_session_bars(df, day)
            observed = len(session)
            total_expected += expected
            total_observed += observed

            offsets = {
                _et_time_offset_minutes(pd.Timestamp(t).tz_convert("America/New_York"))
                for t in pd.to_datetime(session["timestamp"], utc=True)
            } if observed else set()

            if 0 not in offsets:
                issues.append(_warn(
                    symbol, "intraday_missing_session_open_bar",
                    f"{symbol} {day}: missing the 9:30 ET session-open bar.",
                    {"session": day.isoformat()},
                ))
            if (close_off - timeframe_minutes) not in offsets:
                issues.append(_warn(
                    symbol, "intraday_missing_session_close_bar",
                    f"{symbol} {day}: missing the final regular-session bar.",
                    {"session": day.isoformat()},
                ))
            if expected and observed / expected < SESSION_COVERAGE_FLOOR:
                issues.append(_warn(
                    symbol, "intraday_session_coverage_below_threshold",
                    f"{symbol} {day}: {observed}/{expected} bars "
                    f"({observed / expected:.0%}).",
                    {"session": day.isoformat(), "observed": observed, "expected": expected},
                ))
            max_gap = _max_intra_session_gap(offsets, close_off, timeframe_minutes)
            if max_gap > 1:
                issues.append(_warn(
                    symbol, "intraday_intra_session_gap",
                    f"{symbol} {day}: {max_gap} consecutive missing bars.",
                    {"session": day.isoformat(), "max_gap_bars": max_gap},
                ))
            zero_vol = int((pd.to_numeric(session["volume"], errors="coerce") == 0).sum()) if observed else 0
            if zero_vol:
                issues.append(_warn(
                    symbol, "intraday_zero_volume_bars",
                    f"{symbol} {day}: {zero_vol} zero-volume regular-session bar(s).",
                    {"session": day.isoformat(), "zero_volume_bars": zero_vol},
                ))

        last_session = max(session_days)
        if last_session < requested_end - STALE_TAIL_TOLERANCE:
            issues.append(_warn(
                symbol, "intraday_stale_dataset_tail",
                f"{symbol}: latest session {last_session} is materially before "
                f"requested_end {requested_end}.",
                {"last_session": last_session.isoformat(),
                 "requested_end": requested_end.isoformat()},
            ))

        per_symbol.append(SymbolReadiness(
            symbol=symbol, content_hash=_content_hash(df),
            sessions_observed=len(session_days),
            expected_bars=total_expected, observed_bars=total_observed,
        ))

    return ReadinessReport(
        requested_start=requested_start, requested_end=requested_end,
        timeframe_minutes=timeframe_minutes, feed_label=feed_label,
        scanned_symbols=tuple(sorted(bars_by_symbol)),
        per_symbol=tuple(per_symbol), issues=tuple(issues),
    )


def _max_intra_session_gap(offsets: set[int], close_off: int, step: int) -> int:
    """Max run of consecutive missing expected bars in [0, close_off)."""
    if not offsets:
        return 0
    max_gap = current = 0
    grid_start = min(offsets)
    grid_end = max(offsets)
    for off in range(grid_start, grid_end + step, step):
        if off in offsets:
            current = 0
        else:
            current += 1
            max_gap = max(max_gap, current)
    return max_gap
```
> Note: `_max_intra_session_gap` measures *interior* gaps (between the first and last observed bar) so a session that simply ends early is caught by the close-bar / coverage checks, not double-counted as a giant interior gap.

- [ ] **Step 4: Verify import direction, then run the test**

Read `src/milodex/strategies/__init__.py`; if heavy/cyclic, move the three `_session_intraday` imports inside `scan_intraday_readiness`. Then:
Run: `python -m pytest tests/milodex/data/test_intraday_readiness.py -q`
Expected: PASS (all 7 tests).

- [ ] **Step 5: Lint + commit**

```
python -m ruff check src/milodex/data/intraday_readiness.py tests/milodex/data/test_intraday_readiness.py
python -m ruff format src/milodex/data/intraday_readiness.py tests/milodex/data/test_intraday_readiness.py
```
(If ruff flags the `_et_time_offset_minutes` private import, keep it with the `# noqa` and the import-direction comment — it is the single source of the 9:30 offset math; duplicating it would fork the half-day truth.)
```bash
git add src/milodex/data/intraday_readiness.py tests/milodex/data/test_intraday_readiness.py
git commit -m "feat(data): intraday-aware data-readiness scanner"
```
Then `/ponytail-review`.

---

## Task PR3: `data readiness` CLI

**Files:**
- Modify: `src/milodex/cli/commands/data.py` (`register` ~:25, `run` dispatch ~:94, new `_run_readiness`)
- Test: `tests/milodex/cli/commands/test_data_readiness.py`

- [ ] **Step 1: Confirm timeframe→minutes mapping**

Read `src/milodex/data/models.py` for the `Timeframe` enum and `cli/_shared.py` `TIMEFRAME_CHOICES`. Determine how to get minutes from a `Timeframe` (a `.minutes` property, or build a local `{Timeframe.MIN_5: 5, Timeframe.MIN_15: 15, Timeframe.MIN_30: 30, Timeframe.HOUR_1: 60}` map). Daily (`1d`) is invalid for readiness — reject with a clear error.

- [ ] **Step 2: Write the failing CLI test**

`tests/milodex/cli/commands/test_data_readiness.py`:
```python
"""`milodex data readiness` CLI."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from milodex.cli.commands import data as data_cmd
from milodex.data.models import BarSet, Timeframe


class _FakeProvider:
    def __init__(self, bars):
        self._bars = bars

    def get_bars(self, symbols, timeframe, start, end):
        return {s: self._bars[s] for s in symbols if s in self._bars}


def _session_5min(day: str, n: int = 78) -> BarSet:
    start = pd.Timestamp(f"{day} 09:30", tz="America/New_York")
    rows = [{
        "timestamp": (start + pd.Timedelta(minutes=5 * i)).tz_convert("UTC"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
        "volume": 1000.0, "vwap": 100.2,
    } for i in range(n)]
    return BarSet(pd.DataFrame(rows))


def test_readiness_rejects_daily_timeframe():
    args = _args(timeframe="1d")
    result = data_cmd.run(args, _ctx({"SPY": _session_5min("2025-06-17")}))
    assert result.status == "error"


def test_readiness_reports_status(tmp_path):
    # ...resolve a single-symbol universe, run, assert status == "pass"
    ...
```
> The skeleton above is intentionally partial in the second test; complete it after Step 4 once the arg/ctx surface is known. Add a concrete `_args` / `_ctx` helper matching the existing `tests/milodex/cli/commands/test_data*.py` patterns (read one first — e.g. `test_data.py` — to mirror how `CommandContext` and `argparse.Namespace` are built there).

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/milodex/cli/commands/test_data_readiness.py -q` → FAIL (no `readiness` command).

- [ ] **Step 4: Register the subparser + dispatch + handler**

In `register` (after the `fetch-universe` parser block, ~:79), add:
```python
    rd_parser = data_subparsers.add_parser(
        "readiness",
        help="Intraday data-readiness report for a universe (per-session completeness).",
    )
    add_global_flags(rd_parser)
    rd_parser.add_argument("--universe-ref", required=True, help="Universe id string.")
    rd_parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD.")
    rd_parser.add_argument("--end", required=True, help="End date YYYY-MM-DD.")
    rd_parser.add_argument(
        "--timeframe", choices=tuple(TIMEFRAME_CHOICES), default="5m",
        help="Intraday timeframe (default: 5m). Daily is not valid for readiness.",
    )
    rd_parser.add_argument("--config-dir", default="configs")
    rd_parser.add_argument(
        "--feed-label", default="fallback",
        choices=("research_grade", "execution_adjacent", "fallback"),
        help="Feed-quality label for the verdict (IEX free tier => fallback).",
    )
```
In `run`, add dispatch (near `:95`):
```python
    if args.data_command == "readiness":
        return _run_readiness(args, ctx)
```
Add `_run_readiness` (mirror `_run_fetch_universe`):
```python
def _run_readiness(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    from milodex.data.intraday_readiness import scan_intraday_readiness

    config_path = Path(args.config_dir) / "_dummy.yaml"
    try:
        symbols = resolve_universe_ref(args.universe_ref, config_path)
    except ValueError as exc:
        return CommandResult(
            command="data.readiness", status="error",
            human_lines=[f"Error: {exc}"],
            errors=[{"code": "universe_ref_not_found", "message": str(exc)}],
        )

    timeframe = TIMEFRAME_CHOICES[args.timeframe]
    minutes = _timeframe_minutes(timeframe)
    if minutes is None:
        return CommandResult(
            command="data.readiness", status="error",
            human_lines=[f"Error: {args.timeframe} is not an intraday timeframe."],
            errors=[{"code": "invalid_timeframe",
                     "message": "readiness requires an intraday timeframe (5m/15m/30m/1h)"}],
        )

    start = parse_iso_date(args.start)
    end = parse_iso_date(args.end)
    if end < start:
        raise ValueError("--end must be on or after --start.")

    provider = ctx.data_provider_factory()
    bars_by_symbol = provider.get_bars(list(symbols), timeframe, start, end)
    report = scan_intraday_readiness(
        bars_by_symbol, timeframe_minutes=minutes,
        requested_start=start, requested_end=end, feed_label=args.feed_label,
    )
    return _build_readiness_result(args.universe_ref, args.timeframe, report)
```
Plus `_timeframe_minutes(timeframe)` (from Step 1) and `_build_readiness_result(universe_ref, label, report)` that renders `report.to_dict()` into `human_lines` (status, feed_label, per-symbol coverage, top-N issue codes) and passes `report.to_dict()` as `data` — mirror the `_build_fetch_universe_result` cap-at-10 pattern.

- [ ] **Step 5: Complete the test, run, commit**

Finish `test_readiness_reports_status`, run `python -m pytest tests/milodex/cli/commands/test_data_readiness.py -q` (PASS), then full `tests/milodex/cli/` slice, lint, commit:
```bash
git add src/milodex/cli/commands/data.py tests/milodex/cli/commands/test_data_readiness.py
git commit -m "feat(cli): data readiness intraday report command"
```
Then `/ponytail-review`.

---

## Task PR4: IEX price-fidelity gate

**Files:**
- Create: `src/milodex/data/consolidated_reference.py`
- Modify: `src/milodex/data/intraday_readiness.py` (optional `reference_daily_by_symbol` param)
- Modify: `src/milodex/cli/commands/data.py` (`--cross-check-reference` flag)
- Test: `tests/milodex/data/test_consolidated_reference.py`, extend `test_intraday_readiness.py`

**Why a new module:** `yahoo_provider.py` is VIX-only by explicit contract. The fidelity gate fetches *arbitrary ETF* daily bars — a different purpose deserving a different, clearly-named home so the VIX-only guard stays honest.

- [ ] **Step 1: Write the failing cross-check test**

`tests/milodex/data/test_consolidated_reference.py`:
```python
from __future__ import annotations

from datetime import date

import pandas as pd

from milodex.data.consolidated_reference import cross_check_session_extremes
from milodex.data.models import BarSet


def _intraday(day: str, high: float, low: float, open_: float) -> BarSet:
    start = pd.Timestamp(f"{day} 09:30", tz="America/New_York")
    rows = [{
        "timestamp": (start + pd.Timedelta(minutes=5 * i)).tz_convert("UTC"),
        "open": open_ if i == 0 else 100.0,
        "high": high if i == 5 else 100.5,
        "low": low if i == 7 else 99.5,
        "close": 100.0, "volume": 1000.0, "vwap": 100.0,
    } for i in range(78)]
    return BarSet(pd.DataFrame(rows))


def _daily_ref(day: str, high: float, low: float, open_: float) -> pd.DataFrame:
    return pd.DataFrame([{
        "timestamp": pd.Timestamp(f"{day}", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": 100.0,
        "volume": 1_000_000, "vwap": float("nan"),
    }])


def test_inward_bias_flagged():
    # IEX high systematically BELOW and low ABOVE the consolidated extremes.
    intraday = {"SPY": _intraday("2025-06-17", high=100.5, low=99.5, open_=100.0)}
    reference = {"SPY": _daily_ref("2025-06-17", high=102.0, low=98.0, open_=100.0)}
    issues = cross_check_session_extremes(
        intraday, reference, timeframe_minutes=5, tolerance_pct=0.001, min_sessions=1,
    )
    assert any(i.code == "iex_inward_price_bias" for i in issues)


def test_matching_extremes_no_flag():
    intraday = {"SPY": _intraday("2025-06-17", high=102.0, low=98.0, open_=100.0)}
    reference = {"SPY": _daily_ref("2025-06-17", high=102.0, low=98.0, open_=100.0)}
    issues = cross_check_session_extremes(
        intraday, reference, timeframe_minutes=5, tolerance_pct=0.001, min_sessions=1,
    )
    assert not issues
```

- [ ] **Step 2: Run to verify failure** → `ModuleNotFoundError`.

- [ ] **Step 3: Implement `consolidated_reference.py`**

```python
"""Free consolidated daily reference for IEX price-fidelity cross-checks.

DISTINCT from yahoo_provider.py (VIX-only). This module fetches arbitrary-symbol
DAILY OHLC from a free consolidated source (Yahoo) for the SOLE purpose of
cross-validating IEX-derived intraday session extremes. It is read-only,
best-effort (empty frame on any error), and never feeds the trade path — it only
informs a data-readiness verdict.

Why this gate exists: IEX is ~2.5% of consolidated volume and under-samples
session high/low extremes (ADR 0017). A price-action verdict built on IEX bars
*looks* rigorous but rests on biased inputs. Baselines cannot detect this (both
candidate and null see the same biased bars). Cross-checking IEX session
high/low/open against a consolidated daily reference is the only signal that an
IEX price bias is poisoning a verdict.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from milodex.data.bar_quality import DataQualityIssue, DataQualitySeverity
from milodex.data.models import BarSet
from milodex.strategies._session_intraday import regular_session_bars, session_date_et

_logger = logging.getLogger(__name__)


def fetch_daily_ohlc(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Best-effort free daily OHLC for ``symbol`` ([start, end] inclusive).

    Returns the canonical bar schema (timestamp/open/high/low/close/volume/vwap),
    empty on any error. NOT a general provider — daily-only, cross-check-only.
    """
    import yfinance

    try:
        raw = yfinance.Ticker(symbol).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d", auto_adjust=False,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("fetch_daily_ohlc(%s): %s", symbol, exc)
        return _empty()
    if raw is None or raw.empty:
        return _empty()
    try:
        return _reshape(raw)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("fetch_daily_ohlc(%s): reshape failed: %s", symbol, exc)
        return _empty()


def cross_check_session_extremes(
    intraday_by_symbol: dict[str, BarSet],
    reference_daily_by_symbol: dict[str, pd.DataFrame],
    *,
    timeframe_minutes: int,
    tolerance_pct: float = 0.0015,
    min_sessions: int = 5,
) -> list[DataQualityIssue]:
    """Flag symbols whose IEX session extremes show a persistent INWARD bias.

    Inward bias = IEX session high < consolidated high (beyond tolerance) and/or
    IEX session low > consolidated low. A symbol with inward bias on a majority
    of its >= ``min_sessions`` overlapping sessions earns an
    ``iex_inward_price_bias`` warning (demotes the readiness verdict).
    """
    _ = timeframe_minutes  # reserved; extremes use regular-session bars directly
    issues: list[DataQualityIssue] = []
    for symbol in sorted(intraday_by_symbol):
        ref = reference_daily_by_symbol.get(symbol)
        if ref is None or ref.empty:
            continue
        ref_by_day = _daily_by_session(ref)
        idf = intraday_by_symbol[symbol].to_dataframe()
        if idf.empty:
            continue
        ts = pd.to_datetime(idf["timestamp"], utc=True)
        sessions = sorted({session_date_et(t) for t in ts})
        compared = inward = 0
        for day in sessions:
            r = ref_by_day.get(day)
            if r is None:
                continue
            sess = regular_session_bars(idf, day)
            if sess.empty:
                continue
            iex_high = float(pd.to_numeric(sess["high"]).max())
            iex_low = float(pd.to_numeric(sess["low"]).min())
            compared += 1
            high_inward = iex_high < r["high"] * (1.0 - tolerance_pct)
            low_inward = iex_low > r["low"] * (1.0 + tolerance_pct)
            if high_inward or low_inward:
                inward += 1
        if compared >= min_sessions and inward / compared > 0.5:
            issues.append(DataQualityIssue(
                code="iex_inward_price_bias",
                severity=DataQualitySeverity.WARNING,
                symbol=symbol,
                message=(
                    f"{symbol}: IEX session extremes inward-biased vs consolidated "
                    f"reference on {inward}/{compared} sessions — price-action verdicts "
                    f"on this symbol are non-durable."
                ),
                context={"inward_sessions": inward, "compared_sessions": compared},
            ))
    return issues


def _daily_by_session(ref: pd.DataFrame) -> dict[date, dict[str, float]]:
    out: dict[date, dict[str, float]] = {}
    ts = pd.to_datetime(ref["timestamp"], utc=True)
    for i, t in enumerate(ts):
        out[t.date()] = {
            "high": float(ref["high"].iloc[i]),
            "low": float(ref["low"].iloc[i]),
            "open": float(ref["open"].iloc[i]),
        }
    return out


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"]
    )


def _reshape(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.reset_index()
    df.columns = [c.lower() for c in df.columns]
    ts_col = next((c for c in ("date", "datetime") if c in df.columns), None)
    if ts_col is None:
        raise KeyError(f"no timestamp column in {list(df.columns)}")
    return pd.DataFrame({
        "timestamp": pd.to_datetime(df[ts_col], utc=True),
        "open": pd.to_numeric(df["open"], errors="coerce").astype("float64"),
        "high": pd.to_numeric(df["high"], errors="coerce").astype("float64"),
        "low": pd.to_numeric(df["low"], errors="coerce").astype("float64"),
        "close": pd.to_numeric(df["close"], errors="coerce").astype("float64"),
        "volume": pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0).astype("int64"),
        "vwap": float("nan"),
    }).dropna(subset=["close"]).reset_index(drop=True)
```
> `_reshape` is duplicated from `yahoo_provider._reshape` deliberately — lifting it to a shared helper is a tidy follow-up but couples two modules with different contracts. Note it for the operator; do not block PR4 on the refactor. (If `/ponytail-review` insists, lift to `data/_yahoo_reshape.py` and import from both.)

- [ ] **Step 4: Fold the gate into the readiness scanner**

Add an optional param to `scan_intraday_readiness`:
```python
def scan_intraday_readiness(
    bars_by_symbol, *, timeframe_minutes, requested_start, requested_end,
    feed_label="fallback", reference_daily_by_symbol=None,
):
    ...
    if reference_daily_by_symbol:
        from milodex.data.consolidated_reference import cross_check_session_extremes
        bias_issues = cross_check_session_extremes(
            bars_by_symbol, reference_daily_by_symbol,
            timeframe_minutes=timeframe_minutes,
        )
        issues.extend(bias_issues)
        if bias_issues and feed_label != "research_grade":
            feed_label = "fallback"  # demote: an inward bias cannot be research-grade
    ...
```
Add a readiness test: passing a `reference_daily_by_symbol` with inward bias yields `iex_inward_price_bias` in `issue_codes` and `feed_label == "fallback"`.

- [ ] **Step 5: Wire `--cross-check-reference` into the CLI**

In `register` (readiness parser) add a `store_true` flag `--cross-check-reference`. In `_run_readiness`, when set, fetch daily reference per symbol via `fetch_daily_ohlc` and pass as `reference_daily_by_symbol`. Keep it opt-in (it makes live network calls to Yahoo).

- [ ] **Step 6: Run, lint, commit**

```
python -m pytest tests/milodex/data/test_consolidated_reference.py tests/milodex/data/test_intraday_readiness.py tests/milodex/cli/commands/test_data_readiness.py -q
```
PASS, lint, then:
```bash
git add src/milodex/data/consolidated_reference.py src/milodex/data/intraday_readiness.py src/milodex/cli/commands/data.py tests/milodex/data/test_consolidated_reference.py tests/milodex/data/test_intraday_readiness.py
git commit -m "feat(data): IEX price-fidelity gate (consolidated daily cross-check)"
```
Then `/ponytail-review`.

---

## Task PR5: baselines as strategies

**Files:**
- Create: `src/milodex/strategies/bench_no_trade.py` + `configs/bench_no_trade_spy_v1.yaml`
- Create: `src/milodex/strategies/bench_time_of_day_null.py` + `configs/bench_time_of_day_null_spy_v1.yaml`
- Test: `tests/milodex/strategies/test_bench_no_trade.py`, `test_bench_time_of_day_null.py`, and a per-symbol test for the existing unconditional-long

> **Design decision #2 governs:** "across 17 ETFs" = 17 single-symbol backtests via the batch path, **NOT** new multi-symbol strategy logic. The existing `bench_unconditional_intraday_long` already trades `universe[0]`, so it already generalizes to any *single*-symbol universe (e.g. `[XLB]`). PR5 therefore adds only two genuinely-new null strategies and a test proving the existing one runs per-symbol — it does **not** build multi-symbol capability.

- [ ] **Step 1: Failing test for no-trade baseline**

`tests/milodex/strategies/test_bench_no_trade.py`:
```python
from __future__ import annotations

import pandas as pd

from milodex.data.models import BarSet
from milodex.strategies.bench_no_trade import BenchNoTradeStrategy
from milodex.strategies.base import StrategyContext


def _ctx() -> StrategyContext:
    return StrategyContext(
        strategy_id="benchmark.no_trade.spy.v1", family="benchmark",
        template="no_trade", variant="spy", version=1, config_hash="x",
        parameters={}, universe=("SPY",), universe_ref="universe.spy_only.v1",
        disable_conditions=(), config_path="x", manifest={}, positions={},
    )


def test_no_trade_never_signals():
    decision = BenchNoTradeStrategy().evaluate(BarSet(pd.DataFrame()), _ctx())
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
```
> Verify the `StrategyContext` constructor kwargs against `strategies/base.py` before running (some fields may have defaults — drop any not required, e.g. `equity`, `bars_by_symbol` if defaulted). Read `base.py` first.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement `bench_no_trade.py`**

```python
"""No-trade baseline. The absolute floor: never trades, P&L == 0.

Answers "is the candidate strategy better than doing nothing?" — the trivial
null every candidate must clear before any other comparison matters.
"""

from __future__ import annotations

from milodex.data.models import BarSet
from milodex.strategies.base import DecisionReasoning, Strategy, StrategyContext, StrategyDecision


class BenchNoTradeStrategy(Strategy):
    """Never emits an intent. SPY config provided; works for any universe."""

    family = "benchmark"
    template = "no_trade"
    parameter_specs = ()

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = (bars, context)
        return StrategyDecision(
            intents=[],
            reasoning=DecisionReasoning(
                rule="no_signal", narrative="no-trade baseline: never trades"
            ),
        )
```
And `configs/bench_no_trade_spy_v1.yaml` (mirror the bench config; `id: "benchmark.no_trade.spy.v1"`, `family: benchmark`, `template: no_trade`, `variant: spy`, `universe_ref: "universe.spy_only.v1"`, `parameters: {}`, `tempo` 5Min/0/1, `risk` block, `stage: backtest`, `backtest` block, `disable_conditions_additional: []`). Stage `backtest` — a baseline is a research instrument, never promoted.

- [ ] **Step 4: Run no-trade test + a registry test** (assert `build_default_registry().resolve("benchmark", "no_trade")` is `BenchNoTradeStrategy`). PASS.

- [ ] **Step 5: Failing test for time-of-day null** — enters at `entry_offset_minutes` after open, exits at the time-stop bar, every full session; distinct from unconditional-long (which enters specifically at the post-opening-range bar). Test: at a bar `entry_offset_minutes` after 9:30 with no position → BUY intent; mid-session with a position and not the time-stop → hold; at the time-stop bar with a position → SELL.

- [ ] **Step 6: Implement `bench_time_of_day_null.py`** — mirror `bench_unconditional_intraday_long.py` structure, but gate entry on `_et_time_offset_minutes(latest_ts_et) == entry_offset_minutes` (param, default 30) and reuse `is_time_stop_bar` / `is_half_day` / `session_date_et` / `shares_for_notional_pct`. `parameter_specs`: `entry_offset_minutes` (int, 0–390), `exit_minutes_before_close` (int, 0–60), `per_position_notional_pct` (float, (0,1]). Config `configs/bench_time_of_day_null_spy_v1.yaml`, `id: "benchmark.time_of_day_null.spy.v1"`, stage `backtest`.

- [ ] **Step 7: Per-symbol test for the existing unconditional-long** — `test_bench_unconditional_trades_non_spy_symbol`: build a context with `universe=("XLB",)` and an `bars_by_symbol={"XLB": <session>}`, advance to the entry signal bar, assert the BUY intent targets `XLB` (proves no SPY hardcoding — the "across 17 ETFs" mechanism is per-symbol resolution, no code change). Read the existing `tests/milodex/strategies/test_bench_unconditional_intraday_long.py` first and mirror its context-construction helper.

- [ ] **Step 8: Run full strategies slice + commit**

```
python -m pytest tests/milodex/strategies/ -q
python -m milodex.cli.main config validate   # ensure all configs (incl. new ones) load
```
> Use the worktree python with the PYTHONPATH shadow for the CLI too. Confirm `config validate` passes for the two new baseline configs.
```bash
git add src/milodex/strategies/bench_no_trade.py src/milodex/strategies/bench_time_of_day_null.py configs/bench_no_trade_spy_v1.yaml configs/bench_time_of_day_null_spy_v1.yaml tests/milodex/strategies/test_bench_no_trade.py tests/milodex/strategies/test_bench_time_of_day_null.py tests/milodex/strategies/test_bench_unconditional_intraday_long.py
git commit -m "feat(strategies): no-trade + time-of-day-null baselines"
```
Then `/ponytail-review`.

---

## Cache warmup (operational — run after PR1 lands the manifest)

Warm 5Min for the 17 ETFs so PR3/PR4 (and the canaries beyond SPY) have data:
```powershell
& 'C:\Users\zdm80\Milodex\.venv\Scripts\python.exe' -m milodex.cli.main data fetch-universe `
  --universe-ref universe.liquid_etf_core.v1 --timeframe 5m --start 2024-01-01 --end 2026-06-19 --force
```
> Order dependency: the manifest (PR1) must exist before `fetch-universe` resolves it. Bounded by IEX free-tier depth (~5.75y; SPY 5Min starts 2020-07-27) — expect the readiness report to flag thinner sector ETFs. **This warmup hits the live Alpaca API and writes the real `market_cache/` — it is an operational step, not part of the worktree diff. Confirm with the operator before running it against their cache, OR run it read-only / skip it (the unit tests use synthetic fixtures and do not need warm cache).**

---

## Self-Review (run against the spec)

**1. Spec coverage**
- C-light universe manifest → PR1 Step 1. ✔
- C-light denylist + wire into `resolve_universe_ref` → PR1 Steps 4–5. ✔ (+ inline path, flagged)
- ADR 0016 note → PR1 Step 7. ✔
- Verify 7 manifests pass → PR1 Step 2 parametrized guard test. ✔
- D intraday-aware coverage/gap (expected per-session grid) → PR2 scanner. ✔
- D new checks: zero-volume / stale-final / session open-close / content-hash / feed-label → PR2 (+ stale tail). ✔
- D `ReadinessReport` dataclass → PR2. ✔
- D CLI `data readiness` → PR3. ✔
- D tests grow sub-day fixtures → PR2/PR3 fixtures. ✔
- IEX fidelity gate (Yahoo daily H/L/O cross-check) → PR4. ✔
- E-PR1 no-trade + time-of-day null + generalized-long (per-symbol, no new multi-symbol logic per decision #2) → PR5. ✔
- Reuse `research screen` for comparison (no new comparison surface) → noted; no code (PR5 uses the existing batch path). ✔

**2. Placeholder scan:** PR3 Step 2's second test is intentionally a skeleton (depends on arg/ctx surface discovered in Step 4) — flagged inline with completion instructions, not a silent TODO. All code steps carry runnable code.

**3. Type consistency:** `reject_ineligible_instruments(symbols, *, source)`, `scan_intraday_readiness(..., feed_label, reference_daily_by_symbol=None)`, `cross_check_session_extremes(..., timeframe_minutes, tolerance_pct, min_sessions)`, `fetch_daily_ohlc(symbol, start, end)` — names consistent across PR2↔PR4↔PR3. `DataQualityIssue`/`DataQualitySeverity` reused (not re-defined). ✔

**Open decisions surfaced for the adversarial review (do not pre-resolve):**
1. Inline-universe enforcement (PR1 Step 5) — include or scope to `resolve_universe_ref` only?
2. `data/` → `strategies/_session_intraday` import direction (PR2) — import vs lift helpers to shared module?
3. `_reshape` duplication (PR4) — duplicate vs lift to shared `data/_yahoo_reshape.py`?
4. Is the IEX fidelity gate worth its weight in the lean slice, or defer with the rest of provider-dependent work?

---

## Review fold (2026-06-19) — corrections applied before implementation

A 4-lens adversarial review (ponytail-scope, data-correctness, risk-hot-path, iex-methodology) ran against this plan. Grounded findings, and the resolution carried into implementation:

**PR1**
- **[major] Wrong manifest id.** The parametrize list pins `universe.phase1.v1`; the real id (in `configs/universe_phase1_v1.yaml`) is `universe.phase1.curated.v1`. → Use the real id. Verify each id at the file before running.
- **[major] Third hot-path caller.** `resolve_universe_ref` is also called at `operations/paper_runner_control.py:49` (runner-launch / eval-symbol path), not just `loader.load` + `data fetch-universe`. → Blast radius includes paper-runner launch. The `risk-invariant-reviewer` MUST be told the guard fires there (no-op today: no config has a denied ticker, verified).
- **[major] Silent-skip trap.** `InstrumentEligibilityError(ValueError)` is caught-and-skipped by glob loaders (`resolve_config_path` `loader.py:335`; `gui/query_helpers.py:52`) → a forbidden inline config becomes "not found", not a loud error. → (a) the loud CI tripwire is the no-try/except `load_strategy_config` call in `tests/milodex/strategies/test_loader.py` (`test_every_real_config_has_registered_class`) — verify it exists and call it out as the intentional fail-loud surface; (b) CLI handlers must catch `InstrumentEligibilityError` BEFORE generic `ValueError` and emit a distinct code (not `universe_ref_not_found`). Execution submit path is exempt (`load_strategy_execution_config` bypasses `_load_universe`) — confirmed, no live-trade risk.

**PR2**
- **[major] Content hash is NOT dtype-invariant** (float32 100.27 ≠ float64 after `round(6)`; int64 `1000` vs float `1000.0` render differently). → DROP the dtype-invariance claim. The hash relies on the BarSet float64/int64 column contract (`models.py:56-65`), so it is deterministic in-system. Keep + test ONLY **order-invariance**; rename the test; cast volume to float64 before serialize for internal consistency.
- **[major] Two fixtures assert codes that won't fire.** (1) missing-open: 77/78 = 98.7% > 0.90 floor → `intraday_session_coverage_below_threshold` does NOT fire; assert only `intraday_missing_session_open_bar` and assert the coverage code ABSENT. (2) stale-tail: `2025-06-10 < 2025-06-17 − 7d = 2025-06-10` is False (strict `<`); widen the gap (`requested_end=2025-06-18`).
- **[nit→do] Lazy import unconditional.** Import the three `_session_intraday` helpers lazily INSIDE `scan_intraday_readiness` (not gated on a "cyclic?" check) — avoids dragging the whole strategy/execution graph into the data layer on import.

**PR3**
- **[minor] Enum names.** Use `Timeframe.MINUTE_5/MINUTE_15/HOUR_1` (NOT `MIN_5`...). Map `{MINUTE_1:1, MINUTE_5:5, MINUTE_15:15, HOUR_1:60}`. `1m` IS a valid intraday choice — include it. `30m` is NOT in `TIMEFRAME_CHOICES` — drop it from the map + error text. Error text: `1m/5m/15m/1h`.
- **[nit] Dispatch ordering.** The `readiness` branch goes BEFORE the `if args.data_command != "bars": raise` guard (alongside fetch-universe/warmup-tape).
- **[from PR1] Distinct eligibility error code** in `_run_readiness` (and noted for fetch-universe).

**PR4 — rebuilt (review found a blocker; decision #4 = "build it", so corrected rather than deferred)**
- **[BLOCKER] Adjustment-basis mismatch.** Alpaca bars are split+dividend ADJUSTED (`alpaca_provider.py:199,303`); `yfinance auto_adjust=False` is RAW → ~7% offset on historical SPY → inward test fires on ~100% of dividend ETF sessions. → **Compare session-range SHAPE, not absolute levels:** inward bias = `iex_range/iex_close < ref_range/ref_close * (1 − tol)` where `range = high − low`. Adjustment scales H, L, and close by the same factor, so `range/close` is adjustment-INVARIANT. Also fetch Yahoo with `auto_adjust=True` (belt-and-suspenders). This is the only change that makes the gate detect IEX extreme-under-sampling rather than dividend drift.
- **[major] feed_label demotion is dead/inverted** (no-op on `fallback`; explicitly skips `research_grade`). → DROP the demotion entirely. v1 gate output = one advisory `iex_inward_price_bias` warning that flips status `pass`→`pass_with_warnings`. No fake enforcement.
- **[major/minor] Trigger calibration + ET date key.** Key the daily reference by ET session date (`session_date_et`), not UTC `.date()`, so the join can't silently zero out. Keep `>50% of ≥min_sessions` but make the range threshold conservative (IEX range < ~80% of consolidated). **Document honestly:** the threshold is a heuristic pending real-data calibration (cannot be validated autonomously); short windows (< min_sessions overlapping) yield no signal by design.
- **[nit] Drop the unused `timeframe_minutes` param** from `cross_check_session_extremes`.
- **Operator note:** the iex-methodology lens recommended DEFERRING PR4 (lowest-ROI, weakest). It is built here (corrected, advisory-only, self-contained, opt-in `--cross-check-reference`) to honor accepted decision #4 — but it is the clean first cut if the operator wants to trim. Drop = delete one module + one CLI flag.

**PR5**
- **[major] no-trade test `BarSet(pd.DataFrame())` raises** at construction (missing columns, `models.py:67-70`). → Build the empty BarSet WITH the 6 required columns (mirror `data.py:_empty_barset` `:113-116`).
- **[major→rescope] "17 single-symbol backtests" is not actually wired.** A benchmark pointed at the 17-ETF manifest trades `sorted(universe)[0]` = DIA only. The unit test proves per-symbol generalization; the operational 17-run fan-out needs 17 single-symbol refs or a batch recipe. → **Rescope honestly:** PR5 delivers the two new baseline strategies + a unit test proving the existing unconditional-long generalizes to any single symbol (no SPY hardcoding). The operational per-symbol-across-17 fan-out is a documented follow-up (needs warm cache + per-symbol configs, decision #2's mechanism). Self-Review checkbox corrected — do not claim the fan-out is delivered.
- **[minor] `config validate` needs a path** (validates ONE file, `config.py:19,30`). → `config validate configs/bench_no_trade_spy_v1.yaml` and `... bench_time_of_day_null_spy_v1.yaml` separately.
- **[nit] Generalize the test helper** to take a `symbol` arg (the existing `_context`/`_intraday_bars` hardcode SPY).

**Verdict (synthesized):** PR1/PR2/PR3/PR5 sound, implement with the above literal fixes. PR4 rebuilt shape-normalized to clear the adjustment blocker; advisory-only; flagged as the operator's first cut. No finding requires scope creep into risk/execution/promotion — the lean-slice boundary holds.

---

## No-merge / handoff

This branch (`intraday-etf-evidence-lean-slice`) is for the operator's fresh-session review. Each PR is its own commit with tests passing and a `/ponytail-review`. PR1 additionally gets `risk-invariant-reviewer`. **Do not merge to master.** Leave the branch, this plan, and a build summary.
