# ADR 0008 — Risk Layer Has Veto Over Execution

**Status:** Accepted
**Date:** 2026-04-16

## Context

The system has strategies that propose trades and a broker that executes them. The question is where risk enforcement lives: inside each strategy, inside the broker adapter, as a decorator, as a post-hoc monitor — or as a mandatory gate between the two.

The VISION document calls risk management "sacred" and says strategy proposes, risk disposes. This ADR formalizes what that architecture actually looks like.

## Decision

Risk enforcement lives in a dedicated `RiskEvaluator` that sits inside `ExecutionService`. The enforcement contract:

1. All order submissions flow through `ExecutionService.submit()` (R-EXE-001). Strategy and CLI code do not call `BrokerClient.submit_order()` directly.
2. `ExecutionService.submit()` calls the full `RiskEvaluator` check set *before* any broker call.
3. If any check fails, no order is submitted and the caller receives a structured result naming the failed check(s).
4. There is no "skip risk" flag. Risk parameters are tunable via config; enforcement is not.

The eleven enforced checks are the ones already listed in R-EXE-004. Additional checks are added by extending the evaluator, not by bypassing it.

## Rationale

- **Single point of enforcement.** One code path for every order means one place to audit, one place to test, one place to fix a bug. Distributing risk logic into each strategy would create N chances to forget a check.
- **Strategies can be wrong without being dangerous.** A strategy with a bug that proposes an oversized order or a trade during a data outage is stopped at the gate. This is the invariant the whole research-first philosophy relies on: the operator can experiment aggressively with strategies because the risk layer is the backstop.
- **Preview is free.** Because enforcement is a pure function of `(intent, config, account state, market state)`, the same evaluator powers `milodex trade preview` (R-EXE-003). The operator can rehearse any submission and see every check's verdict before anything leaves the machine.
- **Stage enforcement lives here, not in strategy code.** A `stage: paper` strategy cannot accidentally submit live orders because the stage check is in the evaluator, not the strategy (see ADR 0004, ADR 0009).
- **Aligns with the highest-stakes assumption.** VISION.md names the risk layer's correctness as the system's single most load-bearing assumption. Concentrating enforcement in one module is the only way to test it honestly and hold that assumption to account.
