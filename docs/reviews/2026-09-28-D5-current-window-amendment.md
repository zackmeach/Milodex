# D-5 Amendment — Current M3 Evidence Window

*2026-09-28. Founder decision recorded from the shareable-paper-v1 planning conversation; independently challenged against current code and the original D-5 record.*

## Decision

The official M3 evidence window starts **2022-01-01** and ends at the latest complete exchange session common to all 17 frozen ETFs. That end session must be no more than seven calendar days before the official run. This replaces the fixed **2026-06-13** endpoint approved in the [2026-07-09 D-5 record](2026-07-09-D5-evidence-durability-brief.md) for the next official rows. The founder chose the refreshed window while setting the paper-v1 plan and subsequently adopted its adversarial amendments. The feed stance is unchanged: IEX results are exploratory, non-durable, and cannot serve as promotion evidence.

The exact end date, exchange-session calendar, symbol roster, bar snapshot, strategy/config hashes, code revision, baseline settings, and coverage outcome must be recorded **before** the official comparison. No operator or agent may shorten the start date, move the endpoint, exclude a weak symbol, or relax a failed preflight silently. A failed preflight holds M3 and produces no closure claim.

## Why the old endpoint changes

The fixed June endpoint made the July screen reproducible, but it cannot answer whether the three verdicts are current in September. A common frozen endpoint and immutable data snapshot preserve comparability while allowing the question to be re-asked on current data. Rejected and inconclusive remain legitimate verdicts when the input contract passes; they are not evidence of a profitable edge.

## Independent dissent and resolution

The independent review found that the current Alpaca provider may contact the API and replace cached history even on a warm read; the readiness scanner omits wholly missing sessions from its denominator; batch and walk-forward paths can derive different out-of-sample dates per symbol; and the assembler can append a registry row with incomplete baseline cells. Merely writing the new end date into a report would therefore create false reproducibility. The official run is held until one frozen calendar and read-only bar snapshot feed every candidate and matched null on a common out-of-sample schedule, all required cells are checked, and snapshot hashes match after execution. The existing D-5 promotion firewall and IEX labels remain mandatory.

This amendment changes research methodology and evidence input handling only. It changes no risk limit, promotion criterion, broker permission, or live-capital boundary. Update `docs/CURRENT_ROADMAP.md` at the M3 gate, not as a live task-board entry.
