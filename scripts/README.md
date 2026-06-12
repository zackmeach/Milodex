# scripts/

One-off analysis and maintenance scripts. Not part of the installed package; tests that cover
them live in `tests/milodex/scripts/` and import via a `sys.path` insert.

## Governance-state backfills

Any script that mutates event-store governance state (promotions, kill switches, manifests)
must satisfy ALL of the following — see ADR 0032 for the policy rationale:

- [ ] **Dry-run/verify mode** (`--verify-only` or equivalent) plus an explicit apply mode.
- [ ] **Idempotent** — a second run is a no-op, verified by test.
- [ ] **Hardcoded scope** — strategy id, timestamps, and field values are constants in the
      script. No generic arguments that turn it into a reusable bypass tool.
- [ ] **Notes/audit field** — the inserted row documents what gap it repairs and when it was
      discovered.
- [ ] **Archived after execution** — once run against production state, `git mv` the script to
      `scripts/archive/` with a header noting the execution/archive date. It is forensic
      evidence from then on, not a tool.

Anything recurring does not belong here — promote it to a named `milodex` maintenance command
so it runs inside governed command surfaces.

## Archive

`scripts/archive/` holds executed one-shot governance backfills, retained for audit forensics.
Do not re-run them against production state.
