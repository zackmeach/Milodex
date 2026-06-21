# Intraday ETF Evidence — Phase 2 Complete (Tiers 0–4)

**Branch:** `intraday-etf-evidence-phase2` — **NOT merged. Operator reviews and merges.**
Off master `b5962e0`. Master untouched. The historical suite result was **3190 passed, 1 skipped, 4 xfailed**; it predates the second-pass remediation below and is not a current verification claim.

---

## Second-pass remediation (2026-06-20) — current status

A second adversarial pass found the first correction incomplete: after-hours bars could still fill
a 15:55 ET exit at 16:05, every non-daily strategy was force-flattened regardless of lifecycle,
screen JSON provenance remained forgeable, baseline run IDs were omitted from registry evidence,
ORB completeness still used a count rather than the exact timestamp grid, and explanation readers
still received ID order.

The branch now uses an explicit `tempo.position_lifecycle` policy. US-equity intraday configs are
`same_session` and use RTH-only timelines/close prices; 24/7 crypto configs are `multi_session` and
may carry positions across outer days. Screen evidence validates the durable run, completed status,
strategy/window/config-hash consistency, persisted metrics, exact roster, and duplicates while
accepting the exact legitimate error-row shape. Registry evidence keeps candidate and baseline run
IDs, ORB requires the exact `{0,5,10,15,20,25}` opening offsets, and explanation reads order by
`(recorded_at, id)`.

**Evidence status:** experiment-registry row 2 and its 2.47 margin are superseded. No numerical
verdict or margin is current until the 68-cell screen is rerun with the new config hashes and the
result is reassembled into a new append-only registry row.

---

## Historical first-pass corrections (2026-06-20) — superseded

An external adversarial review (Codex) found **1 BLOCKER + 4 MAJOR + 2 MINOR** that the thermonuclear
gate's original merge verdict missed. All seven were independently
re-derived against the code and **confirmed real**, then fixed on this branch.

| Finding | Sev | Fix |
|---|---|---|
| Nulls held **overnight** — a final-bar exit filled at next session's open, not at the close → "held-to-close intraday null" was false; asymmetric overnight-drift confound vs the intraday-exiting candidate | **BLOCKER** | `acd96a4` — engine realizes final-bar intraday exits at the session close; **risk-invariant SAFE** |
| 3 H strategies used `sorted(universe)[0]` on a 17-ETF base config → silently evaluated **DIA** while labeled SPY (fan-out doubled DIA, omitted SPY) | MAJOR | `05a5e80` — `single_symbol` guard + `spy_only` base configs + 17-symbol bijection test |
| Decisive-loss margin measured against the **strongest** null, not the nearest | MAJOR | `b53c6e2` — `min(nulls) − candidate` + boundary tests |
| ORB-retest accepted a **partial** opening range → manufactured breakouts on a missing bar | MAJOR | `05a5e80` — requires all on-grid opening-range bars |
| `research evidence` trusted screen-JSON metrics without provenance validation | MAJOR (MINOR blast radius — non-durable registry) | `b53c6e2` — run_id + date-consistency validation |
| Buffered no-action explanations mis-order in id-ordered reads | MINOR | `b53c6e2` — documented; no live reader affected |
| 2 unsorted import blocks (falsified the "clean lint" claim) | MINOR | `05a5e80` — ruff clean |

**Why the gate missed the BLOCKER:** the N2 docstring asserted the overnight hold was "by design,"
which disarmed the thermonuclear finders — they accepted a confident comment instead of challenging
the methodology. The gate was strong on *risk-layer safety* (Codex independently re-confirmed that
held — risk/promotion/broker/kill-switch untouched) and weak on *statistical/methodology correctness*.

**Corrected evidence (held-to-close engine + fixed predicate).** The 68-cell screen was re-run on the
corrected engine and re-assembled (experiment registry **row 2**, append-only — row 1 preserved).
That historical verdict remained **`rejected`** (rsi2 below all three nulls on **17/17** symbols),
but the then-reported margin was materially reduced once the nulls' overnight-drift return was
removed (e.g. random-matched TLT −3.37 → −5.16). This result is now itself superseded by the
second-pass remediation above and is not current evidence. The prior report remains at
`docs/reviews/working_lane_evidence_phase2.md` for audit history.

**Historical verification after those fixes:** suite **3190 passed, 1 skipped, 4 xfailed**; ruff clean; engine fix
risk-invariant SAFE (attack log). The "merge-ready" verdict below is **superseded** by this section —
merge-readiness now rests on these fixes + the corrected rerun, not the original thermonuclear pass.

---

## Intended lane outcome (fresh evidence rerun pending)

Freeze the 17-ETF universe (`universe.liquid_etf_core.v1`) → warm its 5Min cache → produce a readiness report across all 17 → run a price-action candidate + the required baselines **per-symbol across all 17** (real fan-out, not `sorted(universe)[0]`) → compose an evidence report + an experiment-registry entry, every verdict explicitly labeled **IEX-exploratory / non-durable** (ADR 0017).

The historical rsi2 run proved the orchestration path, but its verdict is superseded. A fresh run is
required to prove the current RTH/lifecycle semantics end-to-end.

## Phase map

| Tier | Delivered | Key commits | Review |
|---|---|---|---|
| **0** | cross-ETF fan-out + readiness fixes + 5Min cache warm (17 ETFs) | (pre-finalization) | ponytail |
| **1** | E-PR2 random-matched null + E-PR3 `baseline_ref`; **finalization** N1 clamp / N2 engine force-flatten / N3 / N4 / seed-warmup fix | `ccd6810` `75fe447` `120754c` `09e92c2` | N2 **risk-invariant SAFE** |
| **1 (run)** | 68-cell working-lane evidence run (candidate + 3 baselines × 17), 0 errors | `9d3084c` | — |
| **2 (F)** | experiment registry — migration 015 + store + CLI (append-only, no-delete) | `3819ab5` `a5fda91` | F-PR1 **risk-invariant SAFE** |
| **3 (G)** | evidence assembler + CLI — candidate-vs-baseline deltas + IEX-non-durable verdict policy | `4342c5c` `ffd83c9` | validated end-to-end |
| **4 (H)** | 3 new candidate strategies: gap-continuation, late-session momentum, opening-range-retest | `78d5be3` `f547215` `efd322a` | ponytail (dropped a redundant 4th) |
| **gate** | thermonuclear follow-ups: writer-invariant marker coherence, drop `--feed-label`, predicate boundary tests, gitignore | `0e0e623` | thermonuclear |

**Perf:** Fix A (explanation batching) + Fix B (date idiom) landed. **Fix C was confirmed a no-op** (measured: snapshots are once-per-sweep; cProfile shows zero per-bar fsync — the remainder is CPU-bound pandas tz-conversion, not batchable).

## Historical evidence (superseded governance proof)

The original 68-cell screen and its later 2.47 correction are retained only as append-only historical
audit evidence. Both were computed before the current RTH/session-lifecycle semantics and must not
be cited as the current candidate verdict. See “Second-pass remediation” above.

## Verdict policy (operator-decided, second-opinion-adjudicated)

`terminal_status` ∈ {`failed`, `rejected`, `inconclusive`}. **`rejected`** only when the candidate's Sharpe is below all three nulls on **≥14/17** symbols with the tightest qualifying margin **≥2.0**; a decisive *win* → `inconclusive` (IEX can overstate an edge). ADR-0017's non-durability is honored *structurally*: every row carries `feed="iex"` / `durable=false` / `revisitable=true` + the predicate evaluation, and the writer **refuses** to emit `rejected` unless the full IEX marker set is coherent on the report.

## Thermonuclear final gate

**Historical verdict, superseded.** The thermonuclear pass reported “merge-ready, zero
BLOCKER/MAJOR,” but two later adversarial passes disproved that assessment. Merge readiness now
requires a current clean verification run and a fresh 68-cell evidence rerun.

### Deferred follow-ups (safe, post-merge — not blocking)

- **#3** Promote `durable`/`feed` to a queryable column (`migration 016`). *Mitigated:* `revisitable` IS a column (forced `True`), and `json_extract(evidence_json, '$.durable')` is queryable today.
- **#5** `research/fanout.py:123` `baseline_ref` rewrite uses substring `replace()` — collision-proof for all 17 real tickers; optional dotted-component hardening.
- **#7** `tests/.../test_research_evidence_command.py` single-write test name is misleading (assembler mocked); the real single-write guarantee is pinned by `test_single_registry_append` (un-mocked).

## Standing caveat

Every verdict on this surface is **IEX-exploratory / non-durable** until a SIP/consolidated provider exists (ADR 0017, deferred). The current implementation still requires clean-suite verification and a fresh evidence run. The 3 new H strategies remain research candidates at `stage: backtest`, not promoted.

## How to review

Tests run against the worktree source via `$env:PYTHONPATH = "<worktree>\src"` (the `.venv` editable install points at master's `src`). `git diff b5962e0` is the full phase.
