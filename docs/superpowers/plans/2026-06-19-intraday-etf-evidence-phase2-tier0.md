# Intraday ETF Evidence — Phase 2 Tier 0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: each PR is implemented by a fresh subagent under
> `superpowers:test-driven-development` + ponytail ("simplest thing that satisfies spec + tests; mark
> deliberate simplifications with `ponytail:` comments; surgical changes only — no adjacent refactors").
> Steps use checkbox (`- [ ]`) syntax. Orchestrator dispatches per-PR and reviews between PRs.

**Goal:** Make the merged intraday-ETF-evidence lane *able to do its job* — evaluate one base intraday
strategy per-symbol across the frozen 17-ETF universe with a warmed cache — by closing the dominant
"trades `sorted(universe)[0]` and discards 16" gap and the evidence-corrupting readiness defects.

**Architecture:** Three PRs.
(0A) **Fan-out** — a single-symbol cardinality guard (`single_symbol`) so a single-symbol strategy can
never silently trade `[0]` again, plus a config generator that materializes one inline-`universe:[SYM]`
config per symbol from a base config; these committed per-symbol configs feed the **existing**
`research screen --configs` → `run_batch` path unchanged.
(0B) **Readiness-fix bundle** — count distinct on-grid offsets (not `len(session)`) so coverage can't
exceed 100% or be inflated by dup/off-grid bars, surface dup/off-grid as a warning, extend the half-day
calendar through 2026, one ADR-wording sentence.
(0C) **Cache warmup** — operational: `data fetch-universe` for the 16 non-SPY ETFs at 5Min + a
verification readiness scan across all 17. Gate before any candidate run.

**Tech Stack:** Python 3.11, pytest (xdist; `-n0` for tight loops), ruff. Run tests in the worktree with
`PYTHONPATH=<worktree>/src` (editable-install-shadow gotcha). Green baseline = `2796 passed, 1 skipped,
4 xfailed` (verified at branch start). A clean run reads `… 1 skipped …`, **never** `1 failed`.

**Locked decisions (operator, this session):** fan-out = per-symbol; E-PR2 seed = config-parameter (Tier 1);
build full phase; IEX gate decided at Tier 3. **Never merge to master** — operator reviews and merges.

---

## PR-0A: Cross-ETF fan-out (decent)

**Why:** Every single-symbol intraday strategy does `sorted({s.upper() for s in context.universe})[0]`
(`bench_time_of_day_null.py:60`, `bench_unconditional_intraday_long.py:72`, `meanrev_rsi2_intraday.py:98`,
`meanrev_vwap_reversion_intraday.py:97`) and silently discards the rest. Pointed at the 17-ETF universe it
trades DIA only. We (1) make that failure **loud** and (2) provide a generator so a base strategy fans out
to 17 committed single-symbol configs that the existing batch path runs.

**Files:**
- Modify: `src/milodex/strategies/base.py` (add `single_symbol` free function)
- Modify: `src/milodex/strategies/bench_time_of_day_null.py:57-60`
- Modify: `src/milodex/strategies/bench_unconditional_intraday_long.py:69-72`
- Modify: `src/milodex/strategies/meanrev_rsi2_intraday.py:95-98`
- Modify: `src/milodex/strategies/meanrev_vwap_reversion_intraday.py:94-97`
- Create: `src/milodex/research/__init__.py` + `src/milodex/research/fanout.py` (NEW package + generator —
  `src/milodex/research/` does not exist yet)
- Modify: `src/milodex/cli/commands/research.py` (EXISTING 408-line module) — add a `fan-out` subcommand to
  the `research` group, following the existing `screen` registration pattern at `research.py:63-115`
- Test: `tests/milodex/strategies/test_single_symbol_guard.py`
- Test: `tests/milodex/research/test_fanout.py` (NEW `tests/milodex/research/` dir)
- Generated/committed: `configs/<base>_<sym>_v1.yaml` for the Tier-1 strategies (Step group A4)

### Task A1: single-symbol cardinality guard

- [ ] **Step 1 — Write the failing test** (`tests/milodex/strategies/test_single_symbol_guard.py`):

```python
import pytest

from milodex.strategies.base import single_symbol


def test_single_symbol_returns_sole_symbol():
    assert single_symbol(("spy",)) == "SPY"
    assert single_symbol(["XLF"]) == "XLF"


def test_single_symbol_none_on_empty():
    assert single_symbol(()) is None
    assert single_symbol([]) is None


def test_single_symbol_raises_on_multi():
    with pytest.raises(ValueError, match="single-symbol strategy received"):
        single_symbol(("SPY", "QQQ"))


def test_single_symbol_dedups_then_counts_distinct():
    # case-insensitive dedup of one logical symbol is size 1, not multi
    assert single_symbol(("SPY", "spy")) == "SPY"
```

- [ ] **Step 2 — Run, expect ImportError/FAIL:**
  `python -m pytest tests/milodex/strategies/test_single_symbol_guard.py -v -n0`
  Expected: FAIL (`cannot import name 'single_symbol'`).

- [ ] **Step 3 — Implement in `src/milodex/strategies/base.py`** (top-level function, near `StrategyContext`;
  do **not** modify the frozen `StrategyContext` dataclass):

```python
def single_symbol(universe: Iterable[str]) -> str | None:
    """The sole symbol of a single-symbol strategy's universe.

    Returns ``None`` for an empty universe (caller emits a graceful no-signal).
    Raises ``ValueError`` when the universe has more than one distinct symbol: a
    single-symbol strategy must never silently trade ``sorted(universe)[0]`` and
    discard the rest. The cross-ETF evidence lane fans out one symbol per config
    (PR-0A), so a >1 universe here means a mis-wired fan-out, not a trade signal.

    ponytail: cardinality guard, not session logic — lives in base.py, not
    _session_intraday.py, so a 24/7 strategy could reuse it without importing
    US-equity session code.
    """
    symbols = sorted({s.upper() for s in universe})
    if not symbols:
        return None
    if len(symbols) > 1:
        msg = (
            f"single-symbol strategy received a {len(symbols)}-symbol universe "
            f"{symbols}; the evidence lane fans out one symbol per config "
            f"(universe must resolve to exactly one symbol)"
        )
        raise ValueError(msg)
    return symbols[0]
```

  `base.py:6` currently imports `from collections.abc import Callable, Mapping, Sequence` — it does NOT
  import `Iterable`. Add `Iterable` to that existing line (surgical), or type the param `Sequence[str]`.

- [ ] **Step 4 — Run, expect PASS:**
  `python -m pytest tests/milodex/strategies/test_single_symbol_guard.py -v -n0` → PASS (4 tests).

- [ ] **Step 5 — Commit:** `feat(strategies): add single_symbol cardinality guard (cross-ETF fan-out)`

### Task A2: wire the guard into the 4 single-symbol strategies

For **each** of the four files, replace the existing three-line select-`[0]` block with the guard. The
existing block is (variable name is `symbol` or `primary_symbol`):

```python
universe_symbols = sorted({symbol.upper() for symbol in context.universe})
if not universe_symbols:
    return _no_signal("empty universe")
primary_symbol = universe_symbols[0]
```

becomes:

```python
primary_symbol = single_symbol(context.universe)
if primary_symbol is None:
    return _no_signal("empty universe")
```

(keep the downstream variable name each file already uses — `symbol` vs `primary_symbol` — surgical).
Add `from milodex.strategies.base import single_symbol` to each module's imports. **Do not touch
`regime_spy_shy_200dma.py`** — it is genuinely multi-symbol and must keep its universe loop.

- [ ] **Step 1 — Write a regression test** (`tests/milodex/strategies/test_single_symbol_guard.py`, append).
  The four strategies' existing test builders **hardcode `universe=("SPY",)` with no `universe=` param**
  (`test_meanrev_rsi2_intraday.py:276`, `test_meanrev_vwap_reversion_intraday.py:233`,
  `test_bench_time_of_day_null.py:47`, `test_bench_unconditional_intraday_long.py:222`) — so you CANNOT call
  them with a 2-symbol universe. Instead **inline a 2-symbol `StrategyContext`** per strategy, copying the
  ready template at `test_bench_unconditional_intraday_long.py:164-185` (a full inlined `StrategyContext(...)`)
  and setting `universe=("SPY", "QQQ")` with that strategy's valid default parameters. The test MUST drive
  real `evaluate()` (not call `single_symbol` directly) so it proves the guard is on the trade path. All four
  `evaluate()` methods call `_validated_parameters(context)` before the guard, so valid params are required
  for the guard to be reached.

```python
import pytest

# One test per strategy: build a 2-symbol-universe StrategyContext inline (copy the
# template at test_bench_unconditional_intraday_long.py:164-185), drive evaluate(), assert raise.
def test_<strategy>_multi_symbol_universe_raises():
    ctx = StrategyContext(..., universe=("SPY", "QQQ"), parameters=<valid defaults>, ...)
    with pytest.raises(ValueError, match="single-symbol strategy received"):
        <Strategy>().evaluate(ctx)
```

- [ ] **Step 2 — Run, expect FAIL** (strategies still pick `[0]`): `... -v -n0` → FAIL.

- [ ] **Step 3 — Apply the 4-file edit above.**

- [ ] **Step 4 — Run targeted + the four strategies' existing test modules, expect PASS:**
  `python -m pytest tests/milodex/strategies/test_single_symbol_guard.py tests/milodex/strategies/test_meanrev_rsi2_intraday.py tests/milodex/strategies/test_meanrev_vwap_reversion_intraday.py tests/milodex/strategies/test_bench_time_of_day_null.py tests/milodex/strategies/test_bench_unconditional_intraday_long.py -v -n0`
  Expected: PASS (regression green; existing single-symbol behavior unchanged because their configs
  resolve to one symbol).

- [ ] **Step 5 — Commit:** `fix(strategies): guard single-symbol strategies against multi-symbol universe`

### Task A3: per-symbol config generator

A function + thin CLI that, given a **base** strategy config path and a `universe_ref`, writes one
single-symbol config for each resolved symbol **EXCEPT the base's own variant symbol** (which the base
config already represents). Output is committed YAML (flat in `configs/`). For the 17-ETF universe with a
`spy` base, this writes **16** configs; the base SPY config is the 17th member, untouched.

**Design (carries the contract — keep it exactly):**
- Read the base config with `load_strategy_config` → gives `family`, `template`, `version`, `variant`
  (the base variant, e.g. `"spy"`) as **separate verbatim fields** (template is dotted, e.g.
  `"rsi2.intraday"` — do NOT string-split the id). Resolve the universe with
  `resolve_universe_ref(base.universe_ref, base_config_path)` (NOT bare `load_strategy_config` — it returns
  `((), ref)` unresolved; see `loader.py:519`). This also runs the ADR-0016 eligibility guard.
- **BLOCKER FIX (reviewer):** **skip the symbol whose `.lower() == base.variant`** — generating it would
  write to the base config's own path and clobber the hand-annotated, `config_hash`-bearing base file
  (`compute_config_hash`, `loader.py:406`), and a separate filename for it would double-count in the
  no-dedup `_resolve_strategy_ids` (`research.py:168-179`) when the screen glob matches both. Assert the
  base variant IS in the resolved universe (else the base is mis-paired with this universe → raise).
- For each remaining symbol: deep-copy the base YAML mapping, set `strategy.variant = sym.lower()`,
  set `strategy.id = f"{family}.{template}.{variant}.v{version}"` (CORRECT recipe — uses the loaded
  `family`/`template`/`version` verbatim, which already contain the dots; the loader asserts this equality
  at `loader.py:522-540`), **remove** `strategy.universe_ref`, **set** `strategy.universe: [SYM]`. Write to
  `configs/<stem>_<sym-lower>_v<version>.yaml` where `<stem>` is the base file stem with its trailing
  `_<basevariant>_v<version>` segment removed (e.g. `meanrev_rsi2_intraday`).
- ponytail: inline `universe:[SYM]` over minting 16 single-symbol manifests — the eligibility guard still
  fires on inline universes (`loader.py:512`). Mark with a `ponytail:` comment in `fanout.py`.
- **Slippage immunity (reviewer-confirmed):** all four base configs carry `backtest.slippage_pct: 0.0005`
  at the **config level** (engine resolver tier 2, `engine.py:695-729`), which wins over the manifest's
  universe-level `slippage_pct` (tier 3, returns None for inline universes anyway). The deep-copy carries
  it verbatim → every per-symbol backtest runs at the same 5 bps. This immunity holds ONLY because the base
  has a config-level `backtest.slippage_pct`; the smoke test pins it (below) so a future manifest-reliant
  base is caught.

- [ ] **Step 1 — Write the failing test** (`tests/milodex/research/test_fanout.py`). This is the §5(b)
  correctness-exception smoke test — every generated config must `load_strategy_config` and resolve to
  exactly one eligible symbol:

```python
from pathlib import Path

from milodex.research.fanout import generate_per_symbol_configs
from milodex.strategies.loader import load_strategy_config, resolve_universe_ref


def test_fanout_generates_one_config_per_non_base_symbol(tmp_path: Path):
    # Arrange: copy the spy base config + the liquid_etf_core manifest into tmp_path.
    # (implementer: copy configs/meanrev_rsi2_intraday_spy_v1.yaml +
    #  configs/universe_liquid_etf_core_v1.yaml into tmp_path)
    base = tmp_path / "meanrev_rsi2_intraday_spy_v1.yaml"
    written = generate_per_symbol_configs(
        base_config_path=base,
        universe_ref="universe.liquid_etf_core.v1",
        out_dir=tmp_path,
    )
    # 17-ETF universe minus the base 'spy' variant == 16 generated
    assert len(written) == 16
    # the base config is never overwritten / collided with
    assert base not in written
    assert all(p.name != base.name for p in written)

    ids = set()
    for path in written:
        cfg = load_strategy_config(path)
        # resolves to exactly one eligible symbol (inline universe → guard already ran)
        assert len(cfg.universe) == 1
        sym = cfg.universe[0]
        assert sym.lower() != "spy"  # base variant skipped
        # id equals {family}.{template}.{variant}.v{version} with variant == symbol
        assert cfg.strategy_id.endswith(f".{sym.lower()}.v{cfg.version}")
        assert cfg.universe_ref is None
        ids.add(cfg.strategy_id)
    # 16 generated + base = 17 unique ids, no double-count under the screen glob
    assert len(ids) == 16
    assert load_strategy_config(base).strategy_id not in ids


def test_fanout_preserves_config_level_slippage(tmp_path: Path):
    # slippage immunity: each generated config keeps backtest.slippage_pct = 0.0005
    # (a future manifest-reliant base with no config-level slippage would fail here)
    base = tmp_path / "meanrev_rsi2_intraday_spy_v1.yaml"
    written = generate_per_symbol_configs(
        base_config_path=base, universe_ref="universe.liquid_etf_core.v1", out_dir=tmp_path,
    )
    for path in written:
        cfg = load_strategy_config(path)
        assert cfg.backtest.slippage_pct == 0.0005  # bind to the real field accessor


def test_fanout_rejects_ineligible_symbol(tmp_path: Path):
    # a universe_ref pointing at a manifest with a forbidden ETP must raise
    # InstrumentEligibilityError (proves the generator does not bypass ADR 0016).
    # (implementer: write a tiny manifest containing e.g. "TQQQ" and point the base at it)
    ...
```

- [ ] **Step 2 — Run, expect FAIL** (`generate_per_symbol_configs` missing).

- [ ] **Step 3 — Implement `src/milodex/research/fanout.py`** per the Design above. Keep it a single
  function returning the list of written `Path`s. Use `ruamel`/`yaml` consistent with the rest of the repo
  (grep how `loader.py` reads YAML — match it).

- [ ] **Step 4 — Run, expect PASS.** Then run `python -m ruff check` + `format` on the new files.

- [ ] **Step 5 — Add the thin CLI subcommand** in `research.py` (`fan-out`: args `--strategy-id <base id>`
  or `--config <path>`, `--universe-ref`, `--out` default `configs`). Wire into the existing `research`
  subparser group exactly like `screen`. Add a CLI smoke test (`tests/milodex/cli/...`) asserting the
  command writes N files and exits 0. Keep the command thin — all logic in `fanout.py`.

- [ ] **Step 6 — Commit:** `feat(research): per-symbol config generator + research fan-out CLI`

### Task A4: generate the Tier-1 per-symbol configs (data)

Run the generator for the **three** strategies the Tier-1 milestone needs: the candidate
(`meanrev.rsi2.intraday.spy.v1`) and the two baselines (`bench.time_of_day_null...`,
`bench.unconditional_intraday_long...`). Each produces 16 configs (base spy reused) → 3×16 = **48**
committed configs; with the 3 existing bases the screen glob resolves 3×17 = 51 unique strategy_ids.

- [ ] **Step 1** — for each base strategy id:
  `python -m milodex.cli.main research fan-out --strategy-id <base-id> --universe-ref universe.liquid_etf_core.v1 --out configs`
- [ ] **Step 2** — `python -m pytest tests/milodex/ -k "config" -n0` to confirm all configs still load
  (the suite has config-validation tripwires). Then verify the screen glob resolves **exactly 17 unique
  strategy_ids per strategy with no duplicates** (the no-dedup double-count guard, reviewer finding #3):
  `python -m milodex.cli.main research screen --configs 'meanrev_rsi2_intraday_*.yaml' ...` and confirm the
  matched-config count == 17 and ids are unique. It will 0-trade until cache warmup — expected, that is what
  PR-0C fixes; just confirm resolution + uniqueness + no crash.
- [ ] **Step 3 — Commit:** `chore(configs): per-symbol fan-out configs for Tier-1 candidate + baselines`
  (NOTE: 48 generated files — not hand-edited; the reviewed surface is `fanout.py` + 2 spot-checks).

**PR-0A review:** `/ponytail-review` + **Opus final review** (dominant gap + §5(b) smoke-test correctness
exception). No `risk-invariant-reviewer` (strategy layer; does not touch risk/execution/promotion/broker
or the frozen `StrategyContext`). Full suite green.

---

## PR-0B: Readiness-fix bundle (small — but Opus-reviewed, §5(a))

**Why:** `intraday_readiness.py` computes `coverage_pct = observed_bars / expected_bars * 100` (`:58-59`)
where `observed_bars = len(session)` (`:183-184`) with **no dedup and no off-grid filter**. A duplicate or
off-grid bar pushes coverage >100%, which is `> SESSION_COVERAGE_FLOOR` (`:211`), so the report returns
`'pass'` with **zero warnings** (`:87-90`) — silently masking a real coverage hole. Empirically a dup →
101.28% pass. Plus `US_MARKET_HALF_DAYS` (`_session_intraday.py:43-55`) stops at 2025-12-24, so 2026
half-days score as full 390-min sessions → spurious coverage failures.

**The fix is NOT "cap at 100"** (that silently re-opens the blind spot per §5(a)). It is: **count distinct
on-grid offsets.** Off-grid and duplicate bars do not increment `observed`, so a genuine missing on-grid
bar still drops coverage below the floor and warns; and `observed ≤ expected` by construction.

**Files:**
- Modify: `src/milodex/data/intraday_readiness.py` (`:160-225` region — read it first)
- Modify: `src/milodex/strategies/_session_intraday.py:43-55` (calendar) + header comment `:39-42`
- Modify: `docs/adr/0016-phase1-instrument-whitelist.md` (one sentence)
- Test: `tests/milodex/data/test_intraday_readiness.py` (append)

### Task B1: distinct on-grid coverage + dup/off-grid warning (TESTS FIRST — §5(a))

- [ ] **Step 1 — Write three failing tests** (`tests/milodex/data/test_intraday_readiness.py`, append; mirror
  the existing fixtures in that file — e.g. the builder used by `test_clean_full_session_passes:63`):

```python
def test_duplicate_bar_does_not_inflate_coverage_above_100():
    # full clean session + one duplicate of an existing on-grid bar
    report = scan(... full_session_bars + [duplicate_of_one_bar] ...)
    sess = report_for_one_session(report)
    assert sess.coverage_pct <= 100.0
    # the duplicate is surfaced, not silently swallowed
    assert any(w.code == "intraday_offgrid_or_duplicate_bars" for w in report.warnings)


def test_offgrid_bar_excluded_from_coverage_and_warned():
    # Drop >=8 on-grid bars (78 -> <=70) so coverage falls below the 0.90 floor,
    # THEN add one OFF-grid bar. One dropped bar = 98.7% > 90% would NOT trip the
    # floor (reviewer finding) — the off-grid bar must not paper over the real hole.
    report = scan(... grid_missing_8 + [offgrid_bar] ...)   # observed == 70 -> 89.7%
    sess = report_for_one_session(report)
    assert sess.coverage_pct < 100.0
    assert any(w.code == "intraday_session_coverage_below_threshold" for w in report.warnings)
    assert any(w.code == "intraday_offgrid_or_duplicate_bars" for w in report.warnings)


def test_clean_full_session_still_exactly_100():
    # regression: the existing clean case is unchanged
    report = scan(... clean_full_session ...)
    assert report_for_one_session(report).coverage_pct == 100.0
```

  (Implementer: bind `scan(...)`/`report_for_one_session(...)` to the real API in the file. Use the
  existing 5Min/78-bar session helper.)

- [ ] **Step 2 — Run, expect FAIL** (dup currently inflates >100, off-grid currently counts).

- [ ] **Step 3 — Implement the fix** in `intraday_readiness.py`. Read `:160-225` first. Replace the
  `observed = len(session)` count with a distinct-on-grid-offset count, reusing the already-built `offsets`
  set and `last_grid_offset`/`timeframe_minutes` in scope (`:181-189`):

```python
# Compute the per-bar offsets list ONCE from the session frame (reviewer: `session_timestamps`
# is not a real name — derive it like line :189 does). `offsets` (the set at :188) stays for the
# gap scan; `raw_offsets` (list, keeps dups/off-grid) drives the new counts.
raw_offsets = [_offset_min(t) for t in pd.to_datetime(session["timestamp"], utc=True)]
# distinct on-grid offsets within [0, last_grid_offset]; dups collapse, off-grid excluded
on_grid = {off for off in raw_offsets if off % timeframe_minutes == 0 and 0 <= off <= last_grid_offset}
observed = len(on_grid)
# surface dup/off-grid bars rather than silently dropping them
n_offgrid = sum(1 for off in raw_offsets if off % timeframe_minutes != 0)
n_duplicate = len(raw_offsets) - len(set(raw_offsets))
if n_offgrid or n_duplicate:
    issues.append(_warn(symbol, "intraday_offgrid_or_duplicate_bars",
                        f"{symbol} {day}: {n_offgrid} off-grid, {n_duplicate} duplicate bar(s).",
                        {"session": day.isoformat(), "off_grid": n_offgrid, "duplicate": n_duplicate}))
```

  Keep `observed` as the value fed to `coverage_pct` and the floor check (`:211`). Bind variable names to
  the actual code. ponytail: distinct-on-grid-offset count, not a `min(100, …)` cap.
  - The `<= last_grid_offset` upper bound is safe: `regular_session_bars` already filters `< close_offset`
    upstream (`_session_intraday.py:232`), so no 16:00 close bar ever reaches this code — the fix does not
    change close-bar handling (reviewer-confirmed: the suspected close-bar regression cannot occur).
  - `n_duplicate` over the combined domain can overlap with `n_offgrid` in the message tally (a duplicated
    off-grid bar counts in both). This is a cosmetic label on an advisory warning, not a coverage bug —
    acceptable; do not over-engineer it. Leave `_max_intra_session_gap` consuming the existing `offsets`
    set unchanged (pre-existing behavior, out of scope for this PR).

- [ ] **Step 4 — Run the three new tests + the full readiness module, expect PASS:**
  `python -m pytest tests/milodex/data/test_intraday_readiness.py -v -n0`

- [ ] **Step 5 — Commit:** `fix(data): count distinct on-grid offsets in readiness coverage (dup/off-grid)`

### Task B2: extend half-day calendar through 2026

- [ ] **Step 1 — Write failing test** (append to readiness or a `_session_intraday` test): a 2026 half-day
  (`date(2026,11,27)` and `date(2026,12,24)`) yields `session_close_offset_minutes(...) == 210` and a clean
  half-day 5Min session (42 bars) scores `coverage_pct == 100.0` / status `pass`.
- [ ] **Step 2 — Run, expect FAIL** (2026 half-day currently scored as 390 min).
- [ ] **Step 3 — Add to `US_MARKET_HALF_DAYS` (`_session_intraday.py:43-55`):**
  `date(2026, 11, 27),  # Day after Thanksgiving` and `date(2026, 12, 24),  # Christmas Eve (Thu)`; update
  the header comment `:39` range `2022-2025` → `2022-2026`.
- [ ] **Step 4 — Run, expect PASS.**
- [ ] **Step 5 — Commit:** `fix(strategies): extend US_MARKET_HALF_DAYS through 2026`

### Task B3: ADR 0016 wording (doc, no test)

- [ ] Add one sentence to the enforcement section of `docs/adr/0016-phase1-instrument-whitelist.md` noting:
  *"A bare `load_strategy_config` validates an inline `universe` but does not resolve a `universe_ref`
  (it returns the ref unresolved per `loader.py:519`); the eligibility guard for a `universe_ref` fires
  only when the ref is resolved via `resolve_universe_ref` (e.g. `StrategyLoader.load`, `data
  fetch-universe`)."* Do not chase a "fires on each load" string — it isn't there.
- [ ] **Commit:** `docs(adr): note bare load_strategy_config does not resolve universe_ref (ADR 0016)`

**PR-0B review:** tests-first (done above) + `/ponytail-review` + **Opus final review** (§5(a): a wrong
coverage fix silently re-opens the evidence blind spot). Full suite green.

---

## PR-0C: Cache warmup (operational — orchestrator + operator, not an implementer)

**Why:** 5Min cache is SPY-only (`market_cache/v3/5Min/SPY.parquet`). The other 16 ETFs are cold → any
candidate run is 0-trade → indistinguishable from a real negative verdict, silently poisoning the baseline
comparison. This gates every Tier-1 candidate run.

- [ ] **Step 1 — Warm the 16 non-SPY ETFs at 5Min** (operator confirms `--start` at the warmup gate — bounded
  by IEX 5Min history depth; default proposal `--start 2024-01-01`):
  `python -m milodex.cli.main data fetch-universe --universe-ref universe.liquid_etf_core.v1 --timeframe 5m --start 2024-01-01 --end 2026-06-19 --force`
  (`--force` does a full-range refetch+merge, healing interior gaps — `data.py` `backfill_range`. SPY is
  re-merged idempotently; harmless.)
- [ ] **Step 2 — Verify readiness across all 17** using the lean-slice readiness CLI (with PR-0B's coverage
  fix merged in). Confirm each of the 17 has acceptable coverage and no `intraday_offgrid_or_duplicate_bars`
  surprises. Capture the report as the Tier-0 gate artifact. NOTE: IEX free-tier 5Min history depth is an
  external limit — if `--start 2024-01-01` silently truncates, the readiness scan's stale-tail/coverage
  warnings will surface the short window; do not assume the requested range is the realized range.
- [ ] **Step 3 — GATE:** do not run any Tier-1 candidate until all 17 pass the readiness scan. Sync with the
  operator (operator checkpoint, §4 "end of Tier 0").

---

## Cross-PR notes

- **Order:** 0A and 0B are independent (disjoint files) → can be implemented in parallel by two subagents.
  0C (warmup) runs after 0B is merged (it uses the fixed coverage scan) and after 0A (so configs exist to
  verify resolution). Tier-0 gate = operator sync after all three.
- **No risk-seam touches in Tier 0:** nothing here edits `risk/`, `execution/`, `promotion/`, `broker/`,
  the frozen `StrategyContext`, or any migration. `risk-invariant-reviewer` is reserved for E-PR2 (Tier 1,
  if it touches `StrategyContext`) and F-PR1 (Tier 2 migration). Confirm this still holds at review time —
  if an implementer drifts into a risk seam, escalate.
- **Thermonuclear** is NOT run at end of Tier 0 — it runs at the end of Tier 0+1 (the working-lane
  milestone) per §5. Tier 0 ends with per-PR reviews + green + operator sync only.

## Self-review (orchestrator, pre-dispatch)

- Spec coverage: §2 must-fix-first fan-out → PR-0A; §2 grid-validate coverage + 2026 calendar + ADR
  wording → PR-0B; §2 cache warmup → PR-0C. ✅ All Tier-0 foundation items covered.
- The `load_strategy_config`-doesn't-resolve-`universe_ref` foundation item (§2 fix-alongside) is consumed
  by the generator (A3 uses `resolve_universe_ref`, not bare load) and documented (B3). ✅
- Placeholder scan: test bodies that say "bind to existing fixtures" are intentional — the real fixtures
  live in the target test modules; the implementer reads them. The *behavioral contracts* (asserts) are
  concrete. Acceptable for TDD.
- Type consistency: `single_symbol(universe) -> str | None` used identically in A1/A2;
  `generate_per_symbol_configs(base_config_path, universe_ref, out_dir) -> list[Path]` used identically in
  A3/A4.
