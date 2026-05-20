# ADR 0054 — Risk profiles are bounded operator preferences

**Status:** Accepted (2026-05-20)

## Context

The "Risk Office" badge (Issue 10, UI Readiness Batch 2026-05-19) is the first
interactive risk-policy surface in Milodex. Before implementing it, the doctrine
governing how the risk layer and the operator interact with profiles must be written
down so that every subsequent implementation decision has an authoritative reference.

The core tension: the operator should have meaningful agency over their own risk
posture, but Milodex's safety architecture depends on the risk layer being the
independent boundary between automation and capital — a boundary that cannot be
weakened by the things it evaluates. Both requirements must be honoured simultaneously.

The resolution is the concept of **bounded operator preferences**: the operator chooses
a posture (Conservative, Standard, Aggressive) from an explicit, audited menu, but
every item on that menu is already bounded below non-negotiable account-level ceilings.
The operator moves within the fence; no one moves the fence.

References:
- `docs/FOUNDER_INTENT.md:131` — safe-by-default rule; higher-risk postures are an
  explicit opt-in, not a quiet drift
- `docs/FOUNDER_INTENT.md:137` — "The operator cannot disable the floor; they can
  only choose where they sit above it."
- CLAUDE.md — "Risk layer is sacred. Every trade passes through risk/ before
  execution. Strategy proposes, risk disposes. Never bypass or weaken for convenience."
- CLAUDE.md — "Operator owns risk preferences; risk layer owns enforcement."
- ADR 0011 — event store as the durable audit trail; risk-profile changes write here

## Decision

**§1. Three named profiles (Conservative, Standard, Aggressive) bound by code-level
absolute ceilings.**

The operator selects a risk posture from exactly three named profiles. Each profile
is an overlay on `configs/risk_defaults.yaml`. The overlays live in
`configs/risk_profiles/{conservative,standard,aggressive}.yaml`. The permitted set
is an explicit allowlist in code — not a file-system scan — so no undocumented posture
can be activated by dropping a YAML into the directory.

**§2. Conservative is the safe default.**

A fresh installation, a missing `data/risk_profile.txt`, or an unreadable file all
resolve to Conservative silently. The operator never unknowingly runs at a posture
they did not choose — and "didn't configure yet" is resolved conservatively, never
permissively. This is the direct implementation of `docs/FOUNDER_INTENT.md:131`.

**§3. Backtest engine intentionally stays on base risk_defaults.**

The backtest engine reads `configs/risk_defaults.yaml` directly (via the legacy
`load_risk_defaults()` / `load_backtesting_defaults()` callers) and is not routed
through `load_active_risk_profile()`. This is intentional and permanent. Backtests
evaluate strategy potential under a stable reference set of constraints, not current
operator posture. A backtest run while the operator holds Aggressive should produce
the same edge statistics as one run under Conservative — the posture question is about
real-capital enforcement, not exploratory measurement. The backtest exemption is the
only intentional exception to the "runtime consumer routes through active profile" rule.

**§4. `_ABSOLUTE_CEILINGS` are Python code constants, never YAML.**

The account-level floors are defined in `src/milodex/risk/config.py` as
`_ABSOLUTE_CEILINGS: dict[str, float]`. They are Python literals. They cannot be
patched by editing any config file, environment variable, or profile overlay, because
they are not read from any external source. The only path to changing an absolute
ceiling is a pull request that modifies this module and triggers the full review
process — specifically including this ADR's amendment procedure. Current values:

```
kill_switch.max_drawdown_pct        = 0.20   (Aggressive's 0.15 + safety margin)
portfolio.max_total_exposure_pct    = 0.85   (keeps minimum 15% cash regardless)
daily_limits.max_daily_loss_pct     = 0.08   (single-session loss never exceeds 8%)
```

**§5. Profile activation is refused mid-flight (active runners present).**

The bridge layer (implemented in PR-7b, `risk/profile_bridge.py`) enforces a
"no running runners" precondition before writing a new profile to
`data/risk_profile.txt`. If runners are active at switch time, the bridge returns
a `SwitchRefused` result with reason `RUNNERS_ACTIVE`. The operator must stop all
runners, then switch. This prevents the incoherent state where a runner started
under one set of limits continues executing while the system has nominally switched
to a different set.

**§6. Profile activation is refused during a triggered kill switch.**

The bridge also refuses a profile switch when the kill switch is in a triggered
(non-reset) state. Switching to Aggressive during a kill-switch event — potentially
to allow more drawdown and reopen trading — is exactly the failure mode this rule
prevents. The kill switch is resolved first; the profile switch proceeds only after
a manual reset confirms the operator has reviewed the event and is starting fresh.

**§7. Every profile switch is written to the `risk_profile_changes` audit table.**

Each call to the bridge — whether the switch succeeds, is refused, or falls back to
Conservative on startup — writes a row to `risk_profile_changes` (schema defined in
migration 011, PR-7b). Every row captures: `from_profile`, `to_profile`, `actor`
(gui | cli | startup), `confirmation_method`, `context_mode`, `runners_active_count`,
`success`, and `failure_reason`. This satisfies the `docs/FOUNDER_INTENT.md:131`
"logged and reviewable" property. Per ADR 0011, this table is the durable trail.

**§8. Elevated postures are visibly active (persistent banner).**

Whenever the active profile is Aggressive, the GUI renders a persistent banner
(implemented in PR-7c, `RiskOfficeDrawer`) that is visible from any screen, not
only the Risk Office drawer. The operator cannot be in Aggressive posture without
the UI making it continuously apparent. Standard is "visible on inquiry" (shown in
the drawer). Conservative is the baseline; no special indicator is required. This
implements the `docs/FOUNDER_INTENT.md:135` "visibly active" property.

**§9. The `load_active_risk_profile()` function returns a `RiskDefaults` instance.**

For clean integration with `execution/service.py`, `load_active_risk_profile()`
returns a `RiskDefaults` dataclass — the same type as the legacy `load_risk_defaults()`
function. This is Approach (b) from the design spec: the function constructs a
merged dict (base + overlay), validates it against ceilings, then instantiates
`RiskDefaults` from the validated dict. Downstream consumers (the risk evaluator,
the evaluation context) require no shape changes. The legacy `load_risk_defaults()`
remains callable for backtest-only sites (§3).

**§10. No strategy, ML model, frontier agent, or feature may switch profiles.**

The active profile is a property of the operator's session, written to
`data/risk_profile.txt` only by the bridge layer in response to an authenticated
operator action. There is no API surface in the strategies module, the data module,
the analytics module, or the backtest engine that accepts a profile name or writes to
`data/risk_profile.txt`. This is the direct implementation of
`docs/FOUNDER_INTENT.md:142-144`: the thing being evaluated may not propose a looser
risk policy from inside the harness.

## Consequences

- `src/milodex/risk/config.py` gains `_ABSOLUTE_CEILINGS`, `CeilingViolationError`,
  `load_active_risk_profile()`, `get_active_profile_name()`, and helpers
  `_load_overlay()`, `_merge()`, `_get_by_path()`, `_validate_against_ceilings()`.

- `src/milodex/execution/service.py:376` is migrated from
  `load_risk_defaults(self._risk_defaults_path)` to `load_active_risk_profile()`.
  This is the critical runtime integration; without it the profile system does not
  actually affect trade evaluation.

- `src/milodex/backtesting/engine.py` intentionally retains `load_risk_defaults()`
  and `load_backtesting_defaults()` (§3).

- `configs/risk_profiles/` is a new directory holding three overlay YAMLs.

- `data/risk_profile.txt` is the operator-writable selector; absence = Conservative.

- PR-7b adds the audit table and bridge enforcement (§5, §6, §7).

- PR-7c adds the elevated-posture banner (§8) and the Risk Office GUI.

- Violating any of the ten decisions above requires an amendment to this ADR. The
  amendment must be reviewed and committed before the violating code lands.

## Citations

- `docs/FOUNDER_INTENT.md:131` — safe-by-default and explicit opt-in properties
- `docs/FOUNDER_INTENT.md:135` — "visibly active" property
- `docs/FOUNDER_INTENT.md:137` — "the operator cannot disable the floor"
- `docs/FOUNDER_INTENT.md:142–154` — denial of strategy/agent profile control
- CLAUDE.md — "Risk layer is sacred" / "Operator owns preferences, risk layer
  owns enforcement" / risk-policy mutation surface definition
- ADR 0008 — risk layer veto architecture (structural foundation)
- ADR 0011 — event store as durable audit trail (§7 relies on this)
- ADR 0052 — promotion policy is a typed governance source of truth (parallel
  pattern: code constants own policy, YAML owns tuning)
