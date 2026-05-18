# The Trading Desk — Design-Fidelity Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the verified functional 7-section Trading Desk in place so it matches the rest of the app's editorial visual language (the master `DeskSurface.qml` reference), with zero change to the functional layer.

**Architecture:** Pure QML restyle. Four shared components are rebodied (`SectionHeader`, `SegmentedToggle`, `FunnelRow`, `RunnerSelect`); three get micro-token tweaks (`RollupCell`, `ActivityTable`, `Sparkline`); `DeskSurface.qml` gains seven italic standfirst lines and a stage-gloss map. The Python read-model layer, the slice/gating logic, and every signal contract are frozen. The render-level regression harness (`test_desk_layout_regression.py`) is extended with structural presence/absence guards (gate G3).

**Tech Stack:** PySide6 / Qt Quick (QML), Python 3.11, `uv`, pytest. Editorial design system via the `Theme` QML singleton (Newsreader / Public Sans / JetBrains Mono; `Theme.color.*`, `Theme.typography.*`, `Theme.space[]`, `Theme.radius.*`, `Theme.column.*`).

**Spec:** `docs/superpowers/specs/2026-05-17-trading-desk-fidelity-design.md` (branch `feat/trading-desk`, this worktree). Read it before starting — every primitive's token contract and the §5 preserve list are normative.

**Fidelity reference (read-only, do not edit):** the OLD surface — obtain with
`git show 757afe7:src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml`. Cited line numbers below are in that file. The `SectionLabel` component is master:208–262; the section standfirst idiom is master:653–659.

**Commands (Windows, this worktree):**
- Full test suite: `uv run --extra dev pytest -q`
- Single test: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py -q`
- Lint: `uv run --extra dev ruff check src tests`
- Operator visual (G4, manual): `uv run milodex gui` from this worktree, DESK tab.

**Hard constraints (spec §5 — a diff violating any is rejected in review):**
- No `.py` read-model / query / threading change. The only `.py` edits permitted in this entire plan are to `tests/milodex/gui/test_desk_layout_regression.py` (G3).
- `SegmentedToggle` public API frozen: `property var options`, `property string current`, `signal activated(string value)`.
- `RunnerSelect` signal frozen: `signal selected(string runnerId)` plus `runners` / `current`.
- The three-state empty/stale/fresh gating (DeskSurface §II, lines ~355–498) and the Section VI path-to-log behaviour: typography may change, the selecting conditions may NOT.
- `Sparkline` change must be additive/opt-in (it is shared with the FRONT digest, which is out of scope and must not regress).
- Zero hardcoded hex / size literals — `Theme.*` tokens only.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/milodex/gui/qml/Milodex/components/SectionHeader.qml` | Section header band | **Rebody** to master `SectionLabel` (serif letter + baseline uppercase title + right meta slot + `border.subtle` hairline). API (`numeral`,`title`, default `rightSlot`) preserved. |
| `src/milodex/gui/qml/Milodex/components/SegmentedToggle.qml` | Period / filter control | **Rebody** to type-only word row. Public API frozen. All sizing scaffold removed. |
| `src/milodex/gui/qml/Milodex/components/FunnelRow.qml` | Risk-throughput stage row | **Rebody** to ladder idiom: `label — gloss` + right mono-tnum value + `border.subtle` hairline. Bar removed. Adds `gloss` property. |
| `src/milodex/gui/qml/Milodex/components/RunnerSelect.qml` | Runner selector (III) | **De-chrome**: strip `surface.*` fills, `radius.md`, borders, ▲▼ glyphs; re-render type-only. `selected`/`runners`/`current` frozen. |
| `src/milodex/gui/qml/Milodex/components/RollupCell.qml` | Hero / rollup metric | **Additive**: add `"brand"` tone → `Theme.color.brand.primary` (spec §3 V). |
| `src/milodex/gui/qml/Milodex/components/ActivityTable.qml` | VII event table | **Micro**: empty-state text → `font.italic: true`. |
| `src/milodex/gui/qml/Milodex/components/Sparkline.qml` | P/L sparkline (shared) | **Additive opt-in**: `property bool hairline:false`; when true → no area, no end-dot, stroke binds `Theme.color.border.regular`. Default path unchanged (FRONT safe). |
| `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` | 7-section composition | Insert 7 P2 standfirsts; add `_stageGloss` map (IV); set §II Sparkline `hairline:true; showGrid:false`; pass `tone:"brand"` to §V rollups. No logic/gating change. |
| `tests/milodex/gui/test_desk_layout_regression.py` | Render-level guard (G3) | Extend `_HARNESS_B` with presence + absence assertions. |

P3 (page rhythm) requires **no change** — already conformant (`DeskSurface.qml:168-235`). It is verified in Task 8.

**Build order rationale:** SectionHeader first (largest visual lever, lowest risk, establishes the G3 presence-assertion infra). Then SegmentedToggle (frozen API, removes the polish-loop class). Then the remaining component rebodies. Standfirsts (broad DeskSurface edit) after components are stable. Integration gate last. Every task leaves the full suite green.

---

## Task 1: SectionHeader → master `SectionLabel` band (P1) + G3 presence guard

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/SectionHeader.qml` (full rebody, currently 79 lines)
- Modify: `tests/milodex/gui/test_desk_layout_regression.py` (extend `_HARNESS_B`)
- Verify against: `git show 757afe7:src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` lines 208–262

The current `SectionHeader` renders the numeral in `data.sm` mono `text.muted` and the title in `label.xs` `text.primary` on an anchored row with a bottom hairline. The master `SectionLabel` renders the letter in serif `display.sm` `brand.primary`, the name in `label.xs` `text.secondary` baseline-aligned to the letter, an optional right meta in `data.sm` `text.muted`, and a `border.subtle` hairline below with `space[2]` gap. We adopt the master structure while keeping the existing public API (`numeral`, `title`, default `rightSlot`) so the seven call sites in `DeskSurface.qml` are untouched.

- [ ] **Step 1: Extend `_HARNESS_B` with the SectionHeader presence assertion (failing test first)**

In `tests/milodex/gui/test_desk_layout_regression.py`, add a new test function above the `_HARNESS_*` string blocks:

```python
@_skip_no_qt
def test_section_headers_have_editorial_primitives() -> None:
    """Every DeskSurface SectionHeader must expose the editorial primitives:
    a serif-letter Text (objectName 'sectionHeaderLetter') and a
    border.subtle hairline Rectangle (objectName 'sectionHeaderRule').
    Catches a silently dropped lettermark (gate G3, presence half).
    """
    script = _HARNESS_C.format(
        import_root=repr(str(_QML_IMPORT_ROOT)),
        desk=repr(str(_DESK_SURFACE)),
    )
    _run(script, "SectionHeader editorial-primitive presence")
```

Add `_HARNESS_C` as a new raw string at end of file. It is a copy of `_HARNESS_B`'s preamble (lines 192–293: identical imports, read-model construction, `register_qml_types`, view setup, `view.show()`, pump) with the assertion body replaced by:

```python
def _walk(item):
    yield item
    for c in item.childItems():
        yield from _walk(c)

headers = [it for it in _walk(root) if it.property("title") not in (None, "")
           and it.metaObject().className().startswith("SectionHeader")]
if len(headers) < 7:
    print("expected >=7 SectionHeaders, found " + str(len(headers)), file=sys.stderr)
    sys.exit(4)

bad = []
for h in headers:
    kids = list(_walk(h))
    has_letter = any(k.property("objectName") == "sectionHeaderLetter" for k in kids)
    has_rule   = any(k.property("objectName") == "sectionHeaderRule"   for k in kids)
    if not (has_letter and has_rule):
        bad.append((h.property("title"), has_letter, has_rule))

if bad:
    for t, l, r in bad:
        print("MISSING PRIMITIVE title=" + str(t)
              + " letter=" + str(l) + " rule=" + str(r), file=sys.stderr)
    sys.exit(5)

print("SECTION_HEADER_PRIMITIVES_OK (" + str(len(headers)) + " headers)")
sys.exit(0)
```

(`metaObject().className()` for a QML-defined component is the component name with a numeric suffix, e.g. `SectionHeader_QMLTYPE_42`; `startswith("SectionHeader")` is the stable match.)

- [ ] **Step 2: Run the new test, verify it FAILS**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py::test_section_headers_have_editorial_primitives -q`
Expected: FAIL, exit 5 — current `SectionHeader` has no `objectName`-tagged letter or rule.

- [ ] **Step 3: Rebody `SectionHeader.qml`**

Replace the entire file with:

```qml
// SectionHeader.qml — Editorial section-label band (master SectionLabel,
// reference DeskSurface.qml@757afe7:208-262).
//
// Tokens consumed:
//   color.brand.primary    — serif letter (numeral)
//   color.text.secondary   — uppercase section name (title)
//   color.text.muted       — right meta slot
//   color.border.subtle    — hairline rule
//   typography.display.sm  — letter (serif)
//   typography.label.xs    — name (uppercase, letter-spaced)
//   typography.data.sm     — meta slot (mono tnum)
//   space[2], space[3]     — rule gap / letter→name gap
//
// Public API (unchanged — 7 DeskSurface call sites depend on it):
//   numeral  : string  — the serif letter shown left of the name
//   title    : string  — section name (rendered uppercase)
//   rightSlot: Item    — default property alias; meta Text child(ren)

import QtQuick
import Milodex 1.0

Column {
    id: root

    property string numeral: ""
    property string title:   ""
    default property alias rightSlot: rightSlotArea.children

    width:   parent ? parent.width : implicitWidth
    spacing: Theme.space[2]

    Item {
        width:          parent.width
        implicitHeight: letterText.implicitHeight

        Text {
            id: letterText
            objectName:    "sectionHeaderLetter"
            anchors.left:  parent.left
            anchors.top:   parent.top
            visible:       root.numeral !== ""
            text:          root.numeral
            color:         Theme.color.brand.primary
            font.family:    Theme.typography.display.sm.family
            font.pixelSize: Theme.typography.display.sm.size
            font.weight:    Theme.typography.display.sm.weight
        }

        Text {
            id: nameText
            anchors.left:       root.numeral !== "" ? letterText.right : parent.left
            anchors.leftMargin: root.numeral !== "" ? Theme.space[3] : 0
            anchors.baseline:   letterText.baseline
            text:               root.title
            color:              Theme.color.text.secondary
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }

        Item {
            id: rightSlotArea
            anchors.right:    parent.right
            anchors.baseline: letterText.baseline
            implicitWidth:    childrenRect.width
            implicitHeight:   childrenRect.height
        }
    }

    Rectangle {
        objectName: "sectionHeaderRule"
        width:      parent.width
        height:     1
        color:      Theme.color.border.subtle
    }
}
```

Note: root changes `Item` → `Column` (matches master). Call sites set `width: parent.width` and pass a child `Text` for meta — both still work (`default property alias rightSlot` unchanged; meta `Text` was already styled `data.sm`/`text.muted` at the call sites, so it lands correctly in the baseline-aligned slot).

- [ ] **Step 4: Run the new G3 presence test, verify it PASSES**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py::test_section_headers_have_editorial_primitives -q`
Expected: PASS — `SECTION_HEADER_PRIMITIVES_OK (7 headers)`.

- [ ] **Step 5: Run the full layout-regression file + full suite + lint**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py -q` → all PASS (the non-zero-height guard still green: `Column` root still sizes).
Run: `uv run --extra dev pytest -q` → no regressions.
Run: `uv run --extra dev ruff check src tests` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/milodex/gui/qml/Milodex/components/SectionHeader.qml tests/milodex/gui/test_desk_layout_regression.py
git commit -m "feat(desk): SectionHeader adopts master SectionLabel band (P1) + G3 presence guard"
```

---

## Task 2: SegmentedToggle → type-only (P4) + G3 absence guard

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/SegmentedToggle.qml` (full rebody, currently 166 lines)
- Modify: `tests/milodex/gui/test_desk_layout_regression.py` (extend `_HARNESS_C` / add absence assertion)

The current toggle is a boxy segmented pill with a one-way-sizing scaffold (`_labelMetrics`, `_widthProbe`, `_segmentsWidth`, positioned `trackRow`). Spec P4: render as a plain `Row` of `Text` words, selected `brand.primary` / unselected `text.muted`, **no `Rectangle` at all**. The frozen public API is `options`, `current`, `activated(string value)`. The existing `test_segmented_toggle_no_polish_loop` must stay green (it will — there is no track/anchors-fill geometry left; Defect-A is structurally moot here per spec §2 P4).

- [ ] **Step 1: Add the G3 absence assertion (failing test first)**

In `test_desk_layout_regression.py`, add:

```python
@_skip_no_qt
def test_no_foreign_chrome_idioms() -> None:
    """No SegmentedToggle may contain ANY Rectangle descendant, and no
    FunnelRow may contain a magnitude-fill Rectangle (only its tagged
    hairline). Catches a silently RETAINED chrome idiom — the exact
    mechanism that produced the original regression (gate G3, absence half).
    """
    script = _HARNESS_D.format(
        import_root=repr(str(_QML_IMPORT_ROOT)),
        desk=repr(str(_DESK_SURFACE)),
    )
    _run(script, "no foreign chrome idioms (toggle/funnel)")
```

Add `_HARNESS_D` (same preamble as `_HARNESS_B`, assertion body):

```python
def _walk(item):
    yield item
    for c in item.childItems():
        yield from _walk(c)

def _cls(it): return it.metaObject().className()

toggles = [it for it in _walk(root) if _cls(it).startswith("SegmentedToggle")]
funnels = [it for it in _walk(root) if _cls(it).startswith("FunnelRow")]

if not toggles:
    print("expected >=1 SegmentedToggle, found 0", file=sys.stderr); sys.exit(4)

violations = []
for tg in toggles:
    rects = [k for k in _walk(tg) if k is not tg
             and _cls(k).startswith("QQuickRectangle")]
    if rects:
        violations.append("SegmentedToggle has " + str(len(rects))
                          + " Rectangle(s) — must be type-only")

for fn in funnels:
    rects = [k for k in _walk(fn) if k is not fn
             and _cls(k).startswith("QQuickRectangle")]
    bad = [r for r in rects if r.property("objectName") != "funnelRule"]
    if bad:
        violations.append("FunnelRow has " + str(len(bad))
                          + " non-hairline Rectangle(s) — bar not removed")

if violations:
    for v in violations: print("CHROME RETAINED: " + v, file=sys.stderr)
    sys.exit(5)

print("NO_FOREIGN_CHROME (" + str(len(toggles)) + " toggles, "
      + str(len(funnels)) + " funnels)")
sys.exit(0)
```

- [ ] **Step 2: Run it, verify FAIL**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py::test_no_foreign_chrome_idioms -q`
Expected: FAIL exit 5 — current SegmentedToggle has track/fill/separator Rectangles.

- [ ] **Step 3: Rebody `SegmentedToggle.qml`**

Replace the entire file with:

```qml
// SegmentedToggle.qml — Type-only period/filter control (no chrome).
//
// Spec: 2026-05-17-trading-desk-fidelity-design.md §2 P4. The app idiom is
// "type and colour only, no iconography" — this control renders as a row of
// uppercase words, not a pill. No Rectangle anywhere (gate G3 absence half).
//
// Tokens consumed:
//   color.brand.primary  — selected word
//   color.text.muted     — unselected word
//   typography.label.xs  — uppercase letter-spaced words
//   space[4]             — inter-word gap
//
// MOTION DISCIPLINE: state changes are instant — no Behavior on color.
//
// Public API (FROZEN — spec §5):
//   options  : var    — array of { label: string, value: string }
//   current  : string — currently selected value
//   signal activated(string value)

import QtQuick
import Milodex 1.0

Row {
    id: root

    property var    options: []
    property string current: ""

    signal activated(string value)

    spacing: Theme.space[4]

    Repeater {
        model: root.options

        Text {
            text:                modelData.label
            color:               modelData.value === root.current
                                 ? Theme.color.brand.primary
                                 : Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase

            MouseArea {
                anchors.fill: parent
                onClicked: {
                    if (modelData.value !== root.current)
                        root.activated(modelData.value)
                }
            }
        }
    }
}
```

- [ ] **Step 4: Run G3 absence + the existing polish-loop test + functional tests**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py -q`
Expected: ALL PASS — `test_no_foreign_chrome_idioms` now green; `test_segmented_toggle_no_polish_loop` still green (no Rectangle/anchors-fill geometry remains); `test_desk_surface_rows_have_nonzero_height` still green; `test_section_headers_have_editorial_primitives` still green.

- [ ] **Step 5: Run full suite + lint — confirm frozen API intact**

Run: `uv run --extra dev pytest -q`
Expected: no regressions. Any pre-existing test that drives `options`/`current`/`activated` (period & filter switching in II/IV/VII) must pass unchanged — if one fails, the API was not preserved; STOP and fix the rebody, do not edit the test.
Run: `uv run --extra dev ruff check src tests` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/milodex/gui/qml/Milodex/components/SegmentedToggle.qml tests/milodex/gui/test_desk_layout_regression.py
git commit -m "feat(desk): SegmentedToggle becomes type-only (P4) + G3 absence guard"
```

---

## Task 3: FunnelRow → ladder idiom (§3 IV) + DeskSurface gloss wiring

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/FunnelRow.qml` (full rebody, currently 81 lines)
- Modify: `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` (Section IV: add `_stageGloss` map, pass `gloss`)
- Read first: `src/milodex/gui/risk_throughput_state.py` — to learn the exact stage `label` strings emitted in `bySlice[...]` (needed to key the gloss map).

Spec §3 IV: replace the proportion bar with the master `A. STRATEGY LADDER` row idiom — `label — gloss` on the left, right-aligned mono-tnum value, one `border.subtle` hairline per row. The `gloss` is a static presentational map (no model/query change — spec §5 compliant).

- [ ] **Step 1: Discover the canonical stage labels**

Read `src/milodex/gui/risk_throughput_state.py`. Record the exact ordered set of stage `label` values (e.g. `Evaluations`, `Signals`, `Orders Proposed`, `Risk-Approved`, `Rejected`, `Submitted`, `Filled` — confirm against the source, do not assume). These keys drive the gloss map in Step 4.

- [ ] **Step 2: Tighten the G3 absence test for FunnelRow (already added in Task 2)**

No new test. `test_no_foreign_chrome_idioms` already asserts FunnelRow has no Rectangle except an `objectName: "funnelRule"` hairline. Run it now to confirm it currently FAILS for FunnelRow (the bar track + fill are still present):

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py::test_no_foreign_chrome_idioms -q`
Expected: FAIL exit 5 — `FunnelRow has 2 non-hairline Rectangle(s) — bar not removed`.

- [ ] **Step 3: Rebody `FunnelRow.qml`**

Replace the entire file with:

```qml
// FunnelRow.qml — Risk-gate stage row (master STRATEGY LADDER idiom).
//
// Spec: 2026-05-17-trading-desk-fidelity-design.md §3 IV. Magnitude is the
// right-aligned mono-tnum value on a hairline — never a filled bar.
//
// Tokens consumed:
//   color.text.primary    — value (mono tnum)
//   color.text.secondary  — stage label
//   color.text.muted      — em-dash gloss
//   color.border.subtle   — per-row hairline
//   typography.data.sm    — value
//   typography.body.sm    — label + gloss
//   space[1], space[2]    — vertical padding / label↔gloss gap
//
// Public API:
//   label : string — stage name (e.g. "Evaluations")
//   gloss : string — short em-dash micro-gloss (e.g. "gate inputs")
//   value : string — formatted count (e.g. "142")

import QtQuick
import Milodex 1.0

Item {
    id: root

    property string label: ""
    property string gloss: ""
    property string value: ""

    implicitWidth:  200
    implicitHeight: labelText.implicitHeight + Theme.space[2] * 2

    Text {
        id: labelText
        anchors.left:           parent.left
        anchors.verticalCenter: parent.verticalCenter
        text:                   root.gloss !== ""
                                ? root.label + " — " + root.gloss
                                : root.label
        color:                  Theme.color.text.secondary
        font.family:    Theme.typography.body.sm.family
        font.pixelSize: Theme.typography.body.sm.size
        font.weight:    Theme.typography.body.sm.weight
        elide:          Text.ElideRight
    }

    Text {
        id: valueText
        anchors.right:          parent.right
        anchors.verticalCenter: parent.verticalCenter
        text:                   root.value
        color:                  Theme.color.text.primary
        font.family:    Theme.typography.data.sm.family
        font.pixelSize: Theme.typography.data.sm.size
        font.weight:    Theme.typography.data.sm.weight
        font.features:  Theme.typography.data.sm.features
        horizontalAlignment: Text.AlignRight
    }

    Rectangle {
        objectName:     "funnelRule"
        anchors.bottom: parent.bottom
        anchors.left:   parent.left
        anchors.right:  parent.right
        height:         1
        color:          Theme.color.border.subtle
    }
}
```

- [ ] **Step 4: Wire the gloss map in DeskSurface Section IV**

In `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml`, inside `throughputCol` (the `Column` at ~line 637), add a readonly gloss map alongside `stages`/`maxValue` (replace the bracketed glosses with ones keyed to the real labels found in Step 1; keep them terse, ≤3 words):

```qml
readonly property var _stageGloss: ({
    "Evaluations":     "gate inputs",
    "Signals":         "raised",
    "Orders Proposed": "pre-risk",
    "Risk-Approved":   "passed gate",
    "Rejected":        "blocked",
    "Submitted":       "sent to broker",
    "Filled":          "executed"
})
```

Then update the `FunnelRow` delegate (currently lines ~677-684) — remove the `proportion` binding, add `gloss`:

```qml
delegate: FunnelRow {
    width: parent.width
    label: modelData.label
    gloss: throughputCol._stageGloss[modelData.label] || ""
    value: String(modelData.value)
}
```

Also delete the now-unused `maxValue` readonly property (lines ~646-653) since nothing reads it after the bar is gone (YAGNI; verify no other reference with a grep before deleting).

- [ ] **Step 5: Run G3 + non-zero-height + full suite + lint**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py -q` → all PASS (`test_no_foreign_chrome_idioms` now green for funnels too).
Run: `uv run --extra dev pytest -q` → no regressions.
Run: `uv run --extra dev ruff check src tests` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/milodex/gui/qml/Milodex/components/FunnelRow.qml src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml
git commit -m "feat(desk): FunnelRow becomes ladder idiom, bar removed (§3 IV)"
```

---

## Task 4: Sparkline hairline opt-in (§3 II soft spot) — FRONT-safe

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/Sparkline.qml` (additive)
- Modify: `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` (Section II Sparkline call site, ~lines 456-463)

Spec §3 II: the Desk sparkline becomes a single `border.regular` 1px stroke — no area, no grid, no axis, no end-dot. This is an *invented-to-idiom* primitive (spec §3 II, flagged soft spot). The change MUST be additive: `Sparkline` is shared with the FRONT digest (file header lines 4-5), and FRONT is out of scope and must not regress (§5). So add an opt-in `hairline` flag defaulting to `false`; the existing render path is byte-for-byte unchanged when `hairline` is false.

- [ ] **Step 1: Add a FRONT non-regression characterization assertion (failing-safe first)**

There is no existing structural test for Sparkline. Add a lightweight guard to `test_desk_layout_regression.py` that simply asserts the DeskSurface still loads clean with the Section II sparkline present (this protects against a Canvas-paint exception from the new branch). Add:

```python
@_skip_no_qt
def test_desk_loads_clean_after_sparkline_change() -> None:
    """DeskSurface must still load with zero QML errors after the Sparkline
    hairline opt-in (guards against a Canvas onPaint exception)."""
    script = _HARNESS_E.format(
        import_root=repr(str(_QML_IMPORT_ROOT)),
        desk=repr(str(_DESK_SURFACE)),
    )
    _run(script, "DeskSurface clean-load post-sparkline")
```

`_HARNESS_E` = `_HARNESS_B` preamble through `view.show()` + pump, then assertion body:

```python
errs = view.errors() if hasattr(view, "errors") else []
if view.status() == QQuickView.Error or errs:
    for e in errs: print(str(e.toString()), file=sys.stderr)
    sys.exit(5)
print("DESK_CLEAN_LOAD")
sys.exit(0)
```

Run it now: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py::test_desk_loads_clean_after_sparkline_change -q` → PASS (baseline; it must stay green through this task).

- [ ] **Step 2: Add the `hairline` opt-in to `Sparkline.qml`**

In `Sparkline.qml`: add `property bool hairline: false` in the Public API block (after `areaAlpha`), add `onHairlineChanged: canvas.requestPaint()` with the other repaint hooks, and in `onPaint` make three guarded changes (do NOT alter the default path):

- After `var lineColor = ...` (line ~86): `if (root.hairline) lineColor = Theme.color.border.regular`
- Wrap the **area fill** block (lines ~116-125) in `if (!root.hairline) { ... }`.
- Wrap the **end-point dot** block (lines ~138-144) in `if (!root.hairline) { ... }`.
- Leave grid/axis as-is (callers pass `showGrid:false; showAxis:false`); the baseline zero-line stays (a calm hairline still benefits from the zero reference at `text.disabled`).

- [ ] **Step 3: Flip the Section II call site**

In `DeskSurface.qml` Section II `Sparkline` (~lines 456-463), change `showGrid: true` → `showGrid: false`, `areaAlpha: 0.12` → `areaAlpha: 0`, add `hairline: true`. Leave `series` / `width` / `height` bindings unchanged.

- [ ] **Step 4: Verify Desk + FRONT both clean**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py -q` → all PASS.
Run: `uv run --extra dev pytest -q` → no regressions (any FRONT/digest Sparkline test must stay green — it uses the default `hairline:false` path which is unchanged).
Run: `uv run --extra dev ruff check src tests` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/milodex/gui/qml/Milodex/components/Sparkline.qml src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml tests/milodex/gui/test_desk_layout_regression.py
git commit -m "feat(desk): Sparkline hairline opt-in for §II; FRONT path unchanged (§3 II)"
```

---

## Task 5: RollupCell `"brand"` tone + ActivityTable italic empty-state

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/RollupCell.qml` (additive tone)
- Modify: `src/milodex/gui/qml/Milodex/components/ActivityTable.qml` (one property)
- Modify: `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` (Section V rollups → `tone: "brand"`)

Spec §3 V wants the five attention numerals in `brand.primary`. `RollupCell`'s `_valueColor` currently has no `brand` case (`data`→`text.primary`). Add it additively (the `ActivityTable` tone map is intentionally kept in sync but does not need `brand`; this is acceptable — `brand` is display-only and never a row tone). Spec §3 VII wants the VII empty-state italic.

- [ ] **Step 1: Add `"brand"` to `RollupCell._valueColor`**

In `RollupCell.qml`, in the `_valueColor` block (lines ~34-41), add as the first branch:

```qml
if (root.tone === "brand")    return Theme.color.brand.primary
```

Update the doc-comment token list to include `color.brand.primary — tone "brand"`.

- [ ] **Step 2: Point Section V rollups at the new tone**

In `DeskSurface.qml` Section V `GridLayout` (~lines 714-751), change the `tone:` on the five `RollupCell`s: `Running Now` / `Paper Testing` → `tone: "brand"` (was `"data"`); `Backtest Only` stays `"muted"`; `Needs Review` keeps `Number(...) > 0 ? "warning" : "muted"`; `Underperforming` keeps `Number(...) > 0 ? "negative" : "muted"`. (Only the always-on counts go brand; threshold-driven ones keep their semantic tone.)

- [ ] **Step 3: ActivityTable empty-state italic**

In `ActivityTable.qml`, the empty-state `Text` (lines ~149-156) — add `font.italic: true`.

- [ ] **Step 4: Tests + lint**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py -q` → all PASS.
Run: `uv run --extra dev pytest -q` → no regressions.
Run: `uv run --extra dev ruff check src tests` → clean.

- [ ] **Step 5: Commit**

```bash
git add src/milodex/gui/qml/Milodex/components/RollupCell.qml src/milodex/gui/qml/Milodex/components/ActivityTable.qml src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml
git commit -m "feat(desk): brand-tone attention numerals (§3 V); VII empty-state italic (§3 VII)"
```

---

## Task 6: RunnerSelect de-chrome (§3 III) — signal frozen

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/RunnerSelect.qml` (rebody, currently 174 lines)

`RunnerSelect` is a rounded-box dropdown with `surface.base`/`surface.raised` fills, `radius.md`, borders, and ▲▼ glyphs — all foreign to the app idiom ("no chrome, no iconography", spec §1.3 / file ethos). It is genuinely interactive (select → III detail grid), so the behaviour and the `selected(string runnerId)` signal + `runners` / `current` are frozen (spec §5 — III breaks without them). The fidelity task: strip the box chrome and the glyph; render type-only. Keep the open/close interaction (a borderless expanding list), keep `_open`/`_currentLabel` internals.

- [ ] **Step 1: Characterization — confirm III still works after, via the existing harness**

No new dedicated test (the interaction is exercised by existing functional GUI tests and the non-zero-height harness which walks Section III). Before editing, run the full suite to capture the green baseline:
Run: `uv run --extra dev pytest -q` → record PASS baseline.

- [ ] **Step 2: Rebody `RunnerSelect.qml`**

Replace the file with a type-only treatment. Trigger row: `RUNNER` eyebrow (`label.xs` uppercase `text.muted`) + current label (`body.sm` `text.primary`) + a non-icon affordance — the word `CHANGE` / `CLOSE` in `label.xs` `text.muted` (no glyph). No `surface.*` fill, no `radius`, no `border`. Dropdown: a borderless `Column` of word rows, selected row `brand.primary`, others `text.muted`, `border.subtle` 1px hairline between rows (the app's list idiom), `z: 10` retained so it floats. Keep `implicitHeight`, `_open`, `_currentLabel`, the `MouseArea` toggle, and `selected()` emission logic exactly as-is.

```qml
// RunnerSelect.qml — Type-only runner selector (no chrome, no glyph).
//
// Spec: 2026-05-17-trading-desk-fidelity-design.md §3 III. App idiom is
// type + colour + hairline only. Behaviour & signal FROZEN (spec §5).
//
// Tokens consumed:
//   color.text.muted      — eyebrow / unselected / affordance
//   color.text.primary    — current label
//   color.brand.primary   — selected row
//   color.border.subtle   — row hairline
//   typography.label.xs   — eyebrow / affordance (uppercase)
//   typography.body.sm    — labels
//   space[1..3]           — padding
//
// Public API (FROZEN):
//   runners : var    — array of { id: string, label: string }
//   current : string — selected runner id
//   signal selected(string runnerId)

import QtQuick
import Milodex 1.0

Item {
    id: root

    property var    runners: []
    property string current: ""
    signal selected(string runnerId)

    property bool _open: false

    readonly property string _currentLabel: {
        for (var i = 0; i < root.runners.length; i++) {
            if (root.runners[i].id === root.current) return root.runners[i].label
        }
        return root.current
    }

    implicitWidth:  200
    implicitHeight: triggerRow.height

    Row {
        id: triggerRow
        anchors.top:  parent.top
        anchors.left: parent.left
        width:        parent.width
        spacing:      Theme.space[2]
        height:       triggerLabel.implicitHeight + Theme.space[2] * 2

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text:  "RUNNER"
            color: Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }
        Text {
            id: triggerLabel
            anchors.verticalCenter: parent.verticalCenter
            text:  root._currentLabel
            color: Theme.color.text.primary
            font.family:    Theme.typography.body.sm.family
            font.pixelSize: Theme.typography.body.sm.size
            font.weight:    Theme.typography.body.sm.weight
            elide:          Text.ElideRight
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text:  root._open ? "CLOSE" : "CHANGE"
            color: Theme.color.text.muted
            font.family:         Theme.typography.label.xs.family
            font.pixelSize:      Theme.typography.label.xs.size
            font.weight:         Theme.typography.label.xs.weight
            font.letterSpacing:  Theme.typography.label.xs.letterSpacing
            font.capitalization: Font.AllUppercase
        }
        MouseArea {
            anchors.fill: parent
            onClicked: root._open = !root._open
        }
    }

    Column {
        id: dropColumn
        visible:           root._open
        anchors.top:       triggerRow.bottom
        anchors.topMargin: Theme.space[1]
        anchors.left:      parent.left
        width:             parent.width
        z:                 10

        Repeater {
            model: root.runners

            Item {
                id: dropItem
                width:  dropColumn.width
                height: dropLabel.implicitHeight + Theme.space[2] * 2

                readonly property bool _isCurrent: modelData.id === root.current

                Text {
                    id: dropLabel
                    anchors.left:           parent.left
                    anchors.right:          parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    text:  modelData.label
                    color: dropItem._isCurrent ? Theme.color.brand.primary
                                               : Theme.color.text.muted
                    font.family:    Theme.typography.body.sm.family
                    font.pixelSize: Theme.typography.body.sm.size
                    font.weight:    Theme.typography.body.sm.weight
                    elide:          Text.ElideRight
                }

                Rectangle {
                    anchors.bottom: parent.bottom
                    anchors.left:   parent.left
                    anchors.right:  parent.right
                    height: 1
                    color:  Theme.color.border.subtle
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        root._open = false
                        if (modelData.id !== root.current)
                            root.selected(modelData.id)
                    }
                }
            }
        }
    }
}
```

- [ ] **Step 3: Tests + lint**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py -q` → all PASS.
Run: `uv run --extra dev pytest -q` → matches the Step 1 baseline (any test driving `RunnerSelect.selected` / III selection must stay green; if one fails the signal/behaviour was not preserved — fix the rebody, never the test).
Run: `uv run --extra dev ruff check src tests` → clean.

- [ ] **Step 4: Commit**

```bash
git add src/milodex/gui/qml/Milodex/components/RunnerSelect.qml
git commit -m "feat(desk): RunnerSelect de-chromed to type-only; signal frozen (§3 III)"
```

---

## Task 7: P2 — seven italic section standfirsts

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` (insert one standfirst `Text` per section, 7 total)
- Verify treatment against: `git show 757afe7:...DeskSurface.qml` lines 653–659

Spec §2 P2: one italic line directly under every `SectionHeader`. Treatment transcribes the master **section** standfirst (master:653–659): `color: Theme.color.text.secondary`, `font.family: Theme.typography.body.md.family`, `font.pixelSize: Theme.typography.body.sm.size`, `font.italic: true`, `wrapMode: Text.WordWrap`. Copy per §2 P2 table. Section II's standfirst is the *header descriptor*; its existing three-state freshness text (empty/stale/fresh) is separate and its selecting logic is **untouched** (§5) — the standfirst is purely additive there.

- [ ] **Step 1: Insert the standfirst component**

Add a reusable inline component near `KeyStat` (after line ~163, inside `Item { id: root }`):

```qml
// Editorial section standfirst (master section idiom, ref :653-659).
component Standfirst: Text {
    width:          parent ? parent.width : implicitWidth
    color:          Theme.color.text.secondary
    font.family:    Theme.typography.body.md.family
    font.pixelSize: Theme.typography.body.sm.size
    font.italic:    true
    wrapMode:       Text.WordWrap
}
```

- [ ] **Step 2: Place one `Standfirst` per section, immediately after each `SectionHeader`**

Insert (exact copy from spec §2 P2):

| Section | After line (approx) | `text:` |
|---|---|---|
| I Risk & Mode | after SectionHeader ~252 | `"risk posture and operating mode, on live broker state"` |
| II Performance & Trust | after SectionHeader block ~379 | `"realised P/L for the selected window, with snapshot freshness stated plainly"` |
| III Active Operations | after SectionHeader block ~548 | `"what is running right now in this session"` |
| IV Risk Layer Throughput | after SectionHeader ~655 | `"how work moved through the risk gate, stage by stage"` |
| V Strategy Attention | after SectionHeader ~706 | `"strategies that need an operator's eye, by reason"` |
| VI Market Tape | after SectionHeader block ~822 | `"instrument status for the market-data feed"` |
| VII Order / Signal Tape | after SectionHeader block ~903 | `"the chronological record of orders, signals, and fills"` |

Each as: `Standfirst { text: "<copy>" }`. Place it as the first child after the `SectionHeader` and before any `SegmentedToggle` / `SectionStatus`. Line numbers are approximate (earlier tasks shifted them) — anchor on the `SectionHeader { numeral: "<N>" }` for each section, not the absolute line.

- [ ] **Step 3: Tests + lint**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py -q` → all PASS (non-zero-height + presence + absence + clean-load all green).
Run: `uv run --extra dev pytest -q` → no regressions (II freshness gating untouched — its tests must stay green).
Run: `uv run --extra dev ruff check src tests` → clean.

- [ ] **Step 4: Commit**

```bash
git add src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml
git commit -m "feat(desk): seven italic section standfirsts (P2)"
```

---

## Task 8: Integration gate — full G2/G3 verification + G4 operator sign-off

**Files:** none modified (verification only) — except a possible doc note.

This task is the gate, not new code. It enforces spec §4 G2–G4.

- [ ] **Step 1: G3 — full structural guard suite green**

Run: `uv run --extra dev pytest tests/milodex/gui/test_desk_layout_regression.py -q`
Expected ALL PASS: `test_segmented_toggle_no_polish_loop`, `test_desk_surface_rows_have_nonzero_height`, `test_section_headers_have_editorial_primitives`, `test_no_foreign_chrome_idioms`, `test_desk_loads_clean_after_sparkline_change`.

- [ ] **Step 2: Full suite + lint**

Run: `uv run --extra dev pytest -q` → green, no quarantine count change vs branch baseline (`docs/KNOWN_FLAKY_TESTS.md` unchanged).
Run: `uv run --extra dev ruff check src tests` → clean.

- [ ] **Step 3: G2 — token-literal + fidelity diff (review checklist)**

Confirm, for every file touched in Tasks 1–7: zero hardcoded hex / px / size literals (all visual props bind `Theme.*`); and the rebodied components structurally match the cited master lines (serif letter present, hairline present, standfirst present, no box/bar/glyph). `grep -nE '#[0-9a-fA-F]{3,6}|pixelSize:\s*[0-9]' src/milodex/gui/qml/Milodex/components src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` should surface only token-bound usages and the intentional `height: 1` hairlines / Canvas-internal numerics in `Sparkline.qml`.

- [ ] **Step 4: G3 — verify P3 page rhythm unchanged**

Confirm `DeskSurface.qml` still has the conformant container: `Flickable` → `pageColumn { x: Theme.space[7]; width: scroller.width - Theme.space[7]*2; topPadding: Theme.space[7]; spacing: Theme.space[6] }`, header band intact, `border.regular` top + inter-row hairlines intact. No task should have altered these; this is the regression check.

- [ ] **Step 5: G4 — operator visual sign-off (BLOCKING, manual)**

Run `uv run milodex gui` from this worktree, open the DESK tab. Compare side-by-side against the master reference (run `git stash`-free: open a second checkout / screenshot of `757afe7`). Confirm: section headers read as the serif-letter band; every section has its italic standfirst; the period/filter controls are type-only (no pill); the throughput section is a hairline ladder (no bars); the runner selector has no rounded box or glyph; overall rhythm/air matches. **The branch does not finish until the operator signs off here.** Headless green is necessary, not sufficient (spec §4 G4).

- [ ] **Step 6: Final commit (if any G2/G4 nits fixed)**

```bash
git add -A
git commit -m "chore(desk): design-fidelity pass integration gate (G2-G4) verified"
```

After G4 sign-off, proceed to `superpowers:finishing-a-development-branch`.

---

## Notes

- **Out of scope (do not touch):** any `.py` read-model/query, the FRONT/BENCH/LEDGER surfaces, `Main.qml` chrome, the II freshness-gating conditions, the `docs/KNOWN_FLAKY_TESTS.md` quarantine. The sparkline cannot express shape at one hairline — known soft spot, deferred (spec §3 II), not fixed here.
- **If a frozen-API test fails** after a rebody (Tasks 2/6): the rebody is wrong, not the test. Never edit a functional test to make a restyle pass — that is the §5 contract.
- **Skill reference:** @superpowers:subagent-driven-development for execution; @superpowers:finishing-a-development-branch after G4.
