# Full-Scale Documentation Audit — 2026-06-13

> **Status: Frozen audit record (captured 2026-06-13).** Point-in-time. The
> "Remediation" tracker below records what was fixed in the same pass; anything
> marked PENDING is a live to-do at capture time, not a permanent claim.

**Scope:** Every project Markdown doc (~150 files, excluding vendored `.venv`/`dist`):
`docs/` (canon, ADRs, reviews, architecture, superpowers, overnight, incidents,
mockups, prototypes, bench), root `README.md`/`CLAUDE.md`/`AGENTS.md`, and
`scripts/README.md`. Auditied against the repo's own documentation contract:
the lifecycle taxonomy in [`docs/README.md`](../README.md) and the Document
Authority Order in [`docs/adr/README.md`](../adr/README.md).

**Method:** 14-cluster parallel fan-out (one auditor per directory/lifecycle
class), each code-grounding accuracy claims against `src/`, `configs/`, and the
ADRs; followed by an adversarial verify pass on every high-severity
accuracy / index-integrity / cross-ref finding (default posture: refute by
re-grounding in code). 165 docs examined, 79 raw findings. Of the high-severity
findings routed to verification, **all came back `confirmed` — 0 refuted,
0 adjusted** — indicating the auditors were not over-reporting.

**Dimensions:** stage-marking · accuracy · index-integrity · cross-ref ·
redundancy · tracking · organization.

---

## Executive summary

| Severity | Count |
|---|---|
| Critical | 1 |
| High | 12 |
| Medium | 36 |
| Low | 30 |
| **Total** | **79** |

**The dominant pattern is append-only drift:** the repository grew past its own
indices and counts. 55 ADRs vs an index that stopped at 0052; 9 `docs/`
subdirectories vs a map listing 6; 13 `src/` subpackages vs prose claiming 10
(root README) / 12 (CLAUDE.md). None of these were *structurally* wrong — every
gap was a stale-by-omission count or a missing index row that nobody updated when
new material landed.

**The second pattern is historical-doc legibility:** several point-in-time
artifacts (reviews, frozen snapshots, a completed roadmap) read as *current*
because they lack a captured/resolved banner — most dangerously two 2026-06-10
audits and a runner-process audit that present **already-fixed P0 capital-safety
findings in the present tense**, mistakable for open hazards.

**What is healthy:** the canon is in strong shape. FOUNDER_INTENT, VISION,
PRODUCT, the phase-close ADR chain (0023→0025→0027→0031→0038), the closed-history
phase-planning banners, the Bench Phase-6 ADR family, and the
operator-preference-vs-system-enforcement framing of the risk layer are all
internally consistent and correctly marked. The living governance specs
(RISK_POLICY, PROMOTION_GOVERNANCE) correctly **defer** threshold numerics to
`promotion/policy.py` rather than restating them. No safety inversion anywhere.

---

## Remediation tracker

### Fixed in this pass (2026-06-13)

| # | Finding | Sev | File(s) |
|---|---|---|---|
| 1 | ADR index missing 0053/0054/0055 — appended with titles verified against each ADR's H1 | **Critical** | `docs/adr/README.md` |
| 2 | Doc map omitted `architecture/`, `incidents/`, `overnight/` subdirs — rows added | High | `docs/README.md` |
| 3 | SRS internal contradiction: R-ANA-001 / R-STR-010 named legacy `state/` vs R-XC-006 `data/` (ADR 0018) — corrected to `data/` | High | `docs/SRS.md` |
| 4 | OPERATIONS claimed `milodex promote` "still works" — code **refuses** it (ADR 0015) — corrected | High | `docs/OPERATIONS.md` |
| 5 | ADR 0051 Phase D2 cited the **capital** gate (0.5/15%/30) for a **paper** promotion — verified against `policy.py` (`STAGE_PAPER` → `paper_gate` 0.0/25%); rewritten to reference the paper-readiness tier | High | `docs/adr/0051-…md` |
| 6 | Hardcoded ADR count "54" (3 sites) vs 55 on disk — corrected / de-hardcoded | Med | `README.md`, `docs/README.md` |
| 7 | Module count: README "Ten" / CLAUDE.md "Twelve" vs 13 subpackages — corrected; added missing `operations/` | Med | `README.md`, `CLAUDE.md` |
| 8 | OPERATIONS cache section + verify snippet pinned `v2`; current is `v3` (`CACHE_VERSION`, confirmed on disk: 123 parquet in `market_cache/v3/1Day`) — updated | Med | `docs/OPERATIONS.md` |
| 9 | ADR 0015 & 0019 Status value "Implemented" not in the vocabulary (Accepted/Superseded/Deprecated) and disagreed with the index — set to `Accepted` (narrative preserved) | Med | `docs/adr/0015-…md`, `0019-…md` |
| 10 | ADR 0038 cited "ADRs 0039-0046" for Q-A…Q-H answers; answers span 0039-0048 (0047/0048 postdated 0038) — widened (3 sites) | Med | `docs/adr/0038-…md` |
| 11 | Orphan top-level docs TROUBLESHOOTING.md / KNOWN_FLAKY_TESTS.md not in the map — classified as operational living references | Med | `docs/README.md` |
| 12 | LAUNCH_READINESS undated in the map's Captured column — dated 2026-05-14 (`fee27fe`) | Med | `docs/README.md` |
| 13 | Stray 0-byte `=` file in repo root — deleted | Low | (repo root) |

### Pending (recommended, not yet applied)

Grouped by the decision they need.

**A. Safe / mechanical — ready to apply, no judgment:**
- **DESIGN_SYSTEM.md token re-sync (High×3 + 5 Med/Low).** §3.1 (Editorial Dark),
  §3.3 (Bronze), §6.1 (status colors, Dark+Bronze cols) carry **pre-brightness-pass**
  hex values; the live theme `.qml` files (which name DESIGN_SYSTEM as their source
  of truth) and DESIGN.md's narrative already use the lifted values. Also: §4.1 token
  names (`bench.row`/`rowMinHeight`/`metric` → live `kanbanCard`/`kanbanCardMinHeight`/
  `kanbanMetric` + `bench*` family), §2 type-scale understates roles, header still
  says "Phase 5 (open)", §6.4/§7.5/§6.5 reference deleted `AnchorSurface.qml` /
  `StrategyBankSurface.qml`. Fix as one coherent doc→code re-sync pass.
- **DESIGN.md cross-refs (Med/Low).** `Main.qml:166-169` nav citation → `:287-290`;
  "No iconography" attribution to DeskSurface header (string absent in source).
- **Historical-doc banners (1 High + several Med/Low).** Add captured/resolved
  banners so class+currency is readable from each doc's own header:
  LAUNCH_READINESS (captured 2026-05-14; reads as a still-open CONDITIONAL-GO
  checklist); TEST_EFFICACY_AUDIT (captured 2026-05-06/Phase 4); the
  2026-06-10 `runner-process-audit` & `gui-wiring-audit` and
  `backtest-rejection-analysis` (resolution banners — their P0/P1 findings are
  fixed: ledger contamination → HR-1 #221, kill-switch path → #220 AnchorSurface
  deleted, engine adjustment → `adjustment=ALL`, gate → two-tier ADR 0052);
  `architecture/roadmaps/2026-06-10-hardening-roadmap` (all 12 PRs merged — add
  completion banner at top); `strategy-bank-final-comparison` / `screen_2026-05-07`
  (frozen + pointer to STRATEGY_BANK.md); PROJECT_STATE_ASSESSMENT / PHASE_1.2_EVIDENCE
  (closed-history pointers); living-spec banners on RISK_POLICY / PROMOTION_GOVERNANCE.
- **Redundancy de-restatements (Med/Low).** Replace inlined gate numerics with a
  link to `policy.py`/ADR 0052: SRS R-PRM-004, ADR 0009 implementation note,
  DISTRIBUTION risk percentages. SRS R-EXE-004 says `_CHECKS` has "14" — actual 16
  (drop the count). ADR 0020's "Update to CLAUDE.md" section makes a now-false claim
  that CLAUDE.md lists thresholds inline (it forbids it) — add a corrective note.
- **ADR line-number drift (Low).** 0008 ("14 checks" → 16/omit), 0011 (`state/`
  backup example → `data/`), 0019 (drifted `:N` refs). Prefer file+symbol over `:N`.
- **VISION Massive caveat (Low).** Note MassiveDataProvider is deferred (ADR 0017)
  and canonical reads currently fall back to Alpaca/Yahoo.
- **`configs/risk_defaults.yaml` comment (High cross-ref).** The
  `max_concurrent_positions` comment still says strategy `risk.max_positions` is
  "informational only" — the pre-ADR-0029 interpretation; ADR 0029 made it BINDING
  and the code enforces it. *Touches risk config (comment only); the doc is correct,
  the config comment is the suspect.*

**B. Tracking — needs a keep/delete call:**
- **Commit the 5 substantive untracked reviews + the hardening-roadmap** (the
  2026-05-29 truth audit, both 2026-06-10 audits, both 2026-06-12 reviews,
  `architecture/roadmaps/2026-06-10-hardening-roadmap`). All dated, cross-referenced,
  high-value; losing them orphans cross-refs and a 15-PR audit trail.
- **2 handoff prompts** under `superpowers/specs/` (2026-06-01, 2026-06-10): untracked,
  mis-classified (they're dispatch prompts, not `*-design.md` specs). Keep+relocate
  (`superpowers/prompts/`?) or delete as ephemeral.
- **`logs/fleet-monitor-2026-06-10.md`**: doc-shaped narrative in gitignored `logs/`
  — will never be tracked. Move to `reviews/`/`incidents/` or accept as ephemeral.

**C. Judgment — product intent:**
- **strategy-families.md** asserts "exactly one family per instance" + defines 5
  (meanrev, regime, momentum, breakout, seasonality), but `benchmark`/`scored`/`tree`
  ship in code (one at paper). Either document them or add a scoping note that they're
  decision-layer-seam / canary families specified in code only. (Not a safety defect —
  the risk layer defaults undocumented families to the universal disable-condition set.)
- **reviews/ consolidation (org, optional).** 8 undated-filename 2026-05 strategy-bank
  screens could fold into a dated subfolder.

---

## Full findings by cluster

Clean clusters (no defects) are noted; see the JSON workflow output for verbatim
evidence (file:line) on every finding.

### meta-index (`docs/README.md`, `docs/adr/README.md`, root README/CLAUDE/AGENTS, scripts/README)
Clean: AGENTS.md, scripts/README.md. Defects: ADR index stopped at 0052 (**critical**, fixed);
map omitted 3 subdirs (high, fixed); ADR-count 54→55 (fixed); README "Ten"/CLAUDE "Twelve"
modules (fixed); orphan TROUBLESHOOTING/KNOWN_FLAKY (fixed).

### read-first (FOUNDER_INTENT, VISION, PRODUCT, SRS)
Clean: FOUNDER_INTENT, PRODUCT. SRS: `state/`→`data/` contradiction (high, fixed);
`_CHECKS` "14"→16 (pending); R-PRM-004 restated gate numerics (pending). VISION: Massive
forward-looking caveat (low, pending).

### gov-specs (RISK_POLICY, PROMOTION_GOVERNANCE, strategy-families, STRATEGY_BANK, REQUIREMENTS_COVERAGE)
Clean content; all numerics correctly deferred. `risk_defaults.yaml` stale comment (high
cross-ref, pending — config); living-spec banners missing on RISK_POLICY/PROMOTION_GOVERNANCE
(low, pending); strategy-families missing 3 shipped families (low, pending-judgment);
STRATEGY_BANK as-of 2 weeks old (med, re-run refresh SQL); REQUIREMENTS_COVERAGE SHA stamp one
commit behind HEAD (low — content reproduces identically; re-run generator).

### ops-specs (ENGINEERING_STANDARDS, OPERATIONS, CLI_UX, REPORTING, DISTRIBUTION, PAPER_WORKFLOW, BENCH_BOUNDARY, INSTALL)
Clean: ENGINEERING_STANDARDS, CLI_UX, REPORTING, PAPER_WORKFLOW, BENCH_BOUNDARY. OPERATIONS:
promote-refused (high, fixed) + cache v2→v3 (med, fixed). INSTALL: stale "Phase 5" tags (low,
pending). DISTRIBUTION: restated risk percentages (low, pending).

### design (DESIGN, DESIGN_SYSTEM)
DESIGN_SYSTEM token re-sync — the cluster's dominant issue (high×3 + med/low, **pending**).
DESIGN narrative is sound; two stale cross-refs (pending).

### lifecycle-markers (TEST_EFFICACY_AUDIT, LAUNCH_READINESS, ROADMAP_PHASE1, PHASE2-5_PLANNING, PHASE6_BENCH_PREP)
Clean: all 5 closed-history phase docs (correct closed-banners → right ADRs) + PHASE6_BENCH_PREP
(active-planning banner). The 2 frozen snapshots lack captured banners — LAUNCH_READINESS (high)
reads as still-open; TEST_EFFICACY_AUDIT (med). LAUNCH map-date fixed; banners pending.

### adr-early (0001-0027) / adr-late (0028-0055)
44 of 55 ADRs clean. Phase-close chain consistent; supersession links (0044↔0047, 0045↔0048,
0049↔0051) bidirectional and verified. Fixed: index (0053-55), 0015/0019 status, 0038 range,
0051 paper-gate conflation. Pending: 0009 inline gate numerics, 0020 false CLAUDE.md claim,
0008/0011/0019 line-ref drift, 0053/0055 optional Related/impl-status notes.

### reviews-chrono (18) / reviews-bank (12)
Strong lifecycle health overall. Two cross-cutting issues: 5 untracked substantive audits
(commit, pending-B); 3 docs state now-closed P0/P1 findings as current (resolution banners,
pending-A). `backtest-rejection-analysis` undated filename. `strategy-bank-final-comparison`
filename implies "current bank" without a STRATEGY_BANK.md pointer (med). 2026-04-14 ui-state
and 2026-06-12 thermo-nuclear are the gold standards for marking.

### architecture (5)
Whole subdir was orphaned from the map (fixed via subdir row). hardening-roadmap untracked +
no completion banner despite all 12 PRs merged (pending-A/B). deepening-audit "RM-009 ready"
stale (low). Accuracy strong; no restated thresholds.

### sp-plans (18) / sp-specs (11)
Clean historical scratch; correctly classed by the map (now date-bounded "NOT current canon").
The only real defects: the 2 untracked, mis-classified handoff prompts (pending-B).

### aar-incidents-ui (overnight, incidents, mockups, prototypes, bench, .claude, stray)
Lifecycle marking strong (every AAR/incident/mockup carries a correct in-doc banner). Defects:
map omitted overnight//incidents/ (fixed); stray `=` (fixed); gitignored fleet-monitor log
(pending-B). `.claude/` defs are gitignored by design (not a defect); spot-verified accurate.

---

## Recurring root causes (worth a structural fix, not just per-doc patches)

1. **Hardcoded counts drift.** ADR count and module count are each stated in
   multiple places and none is derived. Prefer "see the index" / "derived from
   `src/`" over a literal number.
2. **Specs inline values their owner controls.** The SRS and several ADRs restate
   `policy.py` thresholds, the `state/`→`data/` path, and `_CHECKS` counts instead
   of deferring. The fix that already works (PROMOTION_GOVERNANCE, RISK_POLICY) is
   to link the owner and state only the qualitative structure.
3. **Point-in-time docs need a captured/resolved banner, not just a dated filename.**
   The reviews that age worst are the ones whose findings read as current. A one-line
   banner (the 2026-06-12 thermo-nuclear Resolution Log is the model) fixes it without
   rewriting history.
