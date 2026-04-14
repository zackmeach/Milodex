# Milodex UI State Review

Date: 2026-04-14
Reviewer: Codex

## Summary

Milodex is still a backend-first project. The current operator-facing UI is conceptual rather than implemented: `src/milodex/cli/` exists as the intended primary interface, but it currently contains only package documentation and no runnable commands, console entrypoints, or user-facing workflows. There is no desktop GUI scaffold in the repository today.

The project has meaningful platform work in place for broker access, market data access, local caching, and shared configuration. Those layers are promising for a future UI because they already expose typed interfaces and keep Alpaca-specific details behind package boundaries. However, most of the workflows a real operator would expect to use from either a CLI or GUI are still missing because the strategy, risk, analytics, and backtesting modules are package stubs rather than implemented services.

The shortest path to a usable operator experience is to make the CLI real first. A desktop GUI should wait until the first end-to-end operator flows are proven through a stable command surface and the risk layer exists as an enforceable execution boundary.

## Review Scope

This review covered:

- The current operator-facing surface under `src/milodex/cli/`
- The future GUI intent documented in `docs/VISION.md`
- The backing readiness of `broker/`, `data/`, `config.py`, `analytics/`, `backtesting/`, `risk/`, and `strategies/`
- Repo-level delivery signals such as tests, packaging metadata, and entrypoints

## Current State

### What exists today

- A documented project vision that says the product is CLI-first and may later gain a polished desktop GUI.
- A concrete data layer:
  - `DataProvider` ABC in `src/milodex/data/provider.py`
  - Typed market data models in `src/milodex/data/models.py`
  - Alpaca implementation in `src/milodex/data/alpaca_provider.py`
  - Local Parquet cache in `src/milodex/data/cache.py`
- A concrete broker layer:
  - `BrokerClient` ABC in `src/milodex/broker/client.py`
  - Typed order/account/position models in `src/milodex/broker/models.py`
  - Alpaca implementation in `src/milodex/broker/alpaca_client.py`
- A shared configuration module in `src/milodex/config.py`
- Tests for config, broker, and data modules

### What does not exist yet

- No runnable CLI commands
- No `main.py`, Typer/Click/argparse command tree, or console script in `pyproject.toml`
- No GUI app directory, PySide6 app, Tauri app, frontend package, or design assets for a desktop shell
- No implemented strategy engine
- No implemented risk engine
- No implemented backtest engine
- No implemented analytics/reporting service
- No operator workflows for status, portfolio inspection, logs, reports, or promotions

### What an operator can actually do today

From the repo itself, an operator cannot yet interact with Milodex through a supported UI surface. The codebase can be used as a Python package by a developer, but there is not yet a shipped interaction model for:

- viewing account status
- listing positions
- fetching reports
- running a backtest
- promoting a strategy
- toggling trading modes
- reviewing risk decisions

In practice, the only meaningful interaction path today is direct Python/module usage by a developer or test harness.

## UI Inventory Matrix

| Surface | Status | Implemented entrypoint | Backing module readiness | Missing pieces | Priority |
|---|---|---|---|---|---|
| CLI shell | Stubbed | `src/milodex/cli/__init__.py` docstring only | Low | Real command tree, console script, error handling, output formatting, workflow orchestration | P0 |
| Desktop GUI shell | Vision-only | None | Low | Tech choice, app scaffold, design system, domain adapter layer, packaging | P3 |
| Account and market status view | Backend-ready, UI-missing | None | Medium | CLI command or GUI screen using `BrokerClient.get_account()` and `is_market_open()` | P1 |
| Positions view | Backend-ready, UI-missing | None | Medium | CLI command or GUI screen using `BrokerClient.get_positions()` | P1 |
| Orders/history view | Backend-ready, UI-missing | None | Medium | UI surface for `get_orders()`, filtering, formatting, and error states | P1 |
| Market data inspection | Backend-ready, UI-missing | None | Medium | Symbol/timeframe input, tabular output, cache visibility, validation | P2 |
| Backtest run flow | Vision-only | None | Low | Actual backtest engine, result models, CLI orchestration, reporting | P0 |
| Strategy management | Vision-only | None | Low | Strategy implementations, config loader/editor flow, validation, listing | P0 |
| Risk review and veto visibility | Vision-only | None | Low | Risk engine, decision objects, kill-switch state, operator audit trail | P0 |
| Analytics and reports | Vision-only | None | Low | Metric computation, benchmark comparisons, export/report generation, UI views | P1 |
| Promotion pipeline controls | Vision-only | None | Low | Stage model, guardrails, human-review checkpoints, approval UX | P1 |
| Config editing | File-only | YAML files under `configs/` | Low | Schema validation, safe editing workflow, diff/preview, command integration | P2 |

## Repo Truth That Informs UI Planning

### CLI baseline

`src/milodex/cli/__init__.py` contains only descriptive text about the intended CLI. There are no commands, no parser, and no entrypoint wiring. `pyproject.toml` does not define any `[project.scripts]`, so there is no installable command such as `milodex`.

### Desktop GUI baseline

The only GUI mention appears in `docs/VISION.md`, which says the project is CLI-first and that a future desktop GUI may use PySide6 or Tauri. There is no code or package metadata indicating that GUI work has started.

### Backend readiness for UI work

The strongest UI-ready seams today are:

- `milodex.config`
- `milodex.data.DataProvider`, `Bar`, `BarSet`, `Timeframe`
- `milodex.broker.BrokerClient`, `Order`, `Position`, `AccountInfo`

These are good foundations for both a CLI and future GUI because they are typed, abstracted, and avoid leaking Alpaca types outside implementation files.

The weakest seams are the ones that matter most for a real operator experience:

- `analytics`
- `backtesting`
- `risk`
- `strategies`
- `cli`

Each of those areas is represented only by package-level documentation today.

## Architecture Note

### Interfaces that should stay stable across CLI and GUI

These interfaces are already good candidates for shared UI consumption and should remain thin, typed, and independent of any presentation layer:

- `DataProvider`
- `BrokerClient`
- `Bar`, `BarSet`, `Timeframe`
- `Order`, `Position`, `AccountInfo`
- Shared config accessors in `milodex.config`

### Interfaces that still need to exist before serious UI work

Before either the CLI or GUI can become a real operator product, Milodex needs stable service-level interfaces for:

- strategy discovery and execution
- risk evaluation and veto decisions
- backtest orchestration and result retrieval
- analytics/report generation
- promotion pipeline state and approvals

Those interfaces should live in the domain layer, not inside any UI package.

### Risk layer constraint

The risk layer must remain a mandatory boundary. Any future CLI command or GUI action that can lead to order submission should route through a dedicated application/service layer that invokes risk checks before broker execution. The UI must never call `BrokerClient.submit_order()` directly for live flows.

That same rule applies to:

- kill switch resets
- live deployment promotion
- position size increases

Those actions need explicit human-review flows and should not be hidden behind convenience commands.

## Environment and Delivery Blockers

### Test baseline

Running `pytest -q` in the current environment fails during test collection:

```text
ImportError while loading conftest 'C:\Users\zdm80\Milodex\tests\conftest.py'.
tests\conftest.py:4: in <module>
    from datetime import UTC, datetime
ImportError: cannot import name 'UTC' from 'datetime' (C:\Users\zdm80\AppData\Local\Programs\Python\Python310\lib\datetime.py)
```

This is an environment mismatch rather than a UI bug. The project requires Python 3.11+, but the active interpreter used for the test run is Python 3.10. That matters for UI review because it lowers confidence in any CLI or demo validation until the local runtime matches project requirements.

### Packaging baseline

`pyproject.toml` contains dependencies and pytest configuration, but no installable CLI entrypoint. That means even a minimal operator-facing CLI still needs a packaging step before it becomes a normal user experience.

## Gaps Between Docs and Code

### README and vision promises that are partially supported

The docs describe Milodex as a system that will:

- run backtests
- start paper/live trading
- view analytics
- manage strategy configurations
- deliver full transparency and reports

The current codebase only partially supports that story. Broker/data/config foundations exist, but the workflows above are not exposed through a CLI and most are not implemented in the domain layer either.

### No contradiction, but a maturity gap

The repo is not inconsistent so much as early. The documentation describes the intended system shape accurately, but the UI and several operator-facing subsystems have not been built yet.

## Recommendation

### Primary recommendation: make the CLI real first

Milodex should not start desktop GUI implementation yet. The next UI milestone should be a minimum viable CLI that proves the operator experience against real domain seams.

Recommended first CLI scope:

1. `status`
   - show trading mode, market open/closed, and account summary
2. `positions`
   - list open positions
3. `orders`
   - list recent orders
4. `data bars`
   - inspect cached or fetched bars for a symbol and timeframe
5. `config validate`
   - validate YAML structure before a strategy engine exists

This would create a real operator interface without violating the risk boundary or pretending that trading orchestration is complete.

### What should happen before desktop GUI work

Before building a GUI shell, Milodex should add:

- a real CLI entrypoint and command structure
- application/service layer wrappers around broker/data access
- first-class risk service interfaces
- backtest and analytics result objects
- stable workflow orchestration for promotion and approvals

At that point, a desktop GUI can become a thin shell over proven flows rather than a second place where domain decisions get invented.

## Near-Term Roadmap

### 1. Minimum viable CLI

- Add an installable `milodex` console entrypoint in `pyproject.toml`
- Create a command module under `src/milodex/cli/`
- Implement read-only commands first: status, positions, orders, market data, config validation
- Standardize terminal output and error handling

### 2. Operator reporting and analytics views

- Implement analytics service contracts and result models
- Expose daily portfolio snapshot, trade log summaries, and benchmark comparison through the CLI
- Keep reporting read-only until risk/backtest infrastructure is real

### 3. Desktop GUI readiness checkpoint

- Confirm the risk layer exists and is mandatory
- Confirm backtesting and analytics are implemented as reusable services
- Confirm the CLI can complete the core read-only workflows end to end
- Choose PySide6 or Tauri only after these seams are stable

### 4. Desktop GUI implementation

- Build the GUI as a thin shell over domain services and CLI-proven workflows
- Start with dashboard, positions, orders, and report views
- Defer execution-heavy controls until manual-review flows are explicit

## Bottom Line

Milodex has enough backend foundation to justify building a real CLI now, but not enough operator workflow implementation to justify a desktop GUI yet. The UI story today is "promising architecture, almost no user surface." The right next move is to turn the CLI from a documented intention into a small, trustworthy operator tool while the missing domain layers are built behind it.
