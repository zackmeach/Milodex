# M3 current-window preflight — HOLD

*2026-09-28; source code `9b6df70` on `master`. This is a failed input gate,
not an M3 verdict or closure.*

The official window requested was **2022-01-01 through 2026-09-25**, the latest
completed Alpaca exchange session at the run time and three calendar days old.
The isolated 17-ETF, five-minute IEX cache was force-refetched for that full
window. The capture read that scratch cache, not the main market cache. The
frozen exchange calendar lists 1,188 sessions, including the in-progress
2026-09-28 session. Its 1,187 completed sessions through 2026-09-25 have
92,262 expected five-minute RTH grid slots, including half-days.

The immutable capture is local, under
`data/research_snapshots/m3-iex-20260928-hold-35642e27/` (ignored by Git).
`manifest.json` SHA-256:
`35642e276eb49f109210e9d8a0f6e21a2af680bb9e3978b7d1b91c5b4f2f75a1`.
It contains 17 bar files plus the frozen calendar/config inputs (170 hashed
files and the manifest). `verify_snapshot` passed after copying it to that
location; its code hash matches the Python sources at this commit, and all 171
files are read-only.

**Official preflight refusal:** `DIA has no on-grid bars on 2025-03-10`.
An independent scan of the frozen calendar and bars found the same missing
session for **all 17 symbols**. A fresh, read-only Alpaca IEX request for the
full roster returned zero bars on 2025-03-10; the adjacent sessions returned
bars for all 17 (1,313 on 2025-03-07 and 1,310 on 2025-03-11). Thus this is
an upstream IEX gap in the requested historical window, not a calendar-only
or local cache omission.

| Symbol below 90% | Calendar-grid coverage |
|---|---:|
| DIA | 78.94% |
| GLD | 88.68% |
| XLRE | 89.07% |

The other 14 symbols meet the aggregate threshold, but all 17 fail the
nonzero-bars-on-every-session rule. No candidate/null official cell set ran,
and this attempt appended no experiment-registry row; the main registry count
was verified read-only as **0**. No weak symbol was excluded, and the window
and 90% threshold were not changed. There are no three current verdicts.

M3 remains **HOLD**. Resuming requires a complete, freshly frozen input set
that passes the adopted contract, or a separately reviewed founder decision
changing that contract. IEX results, if later produced, remain exploratory,
non-durable, and outside promotion.
