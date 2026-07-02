# PR H visual verification notes

Screenshot capture via offscreen Qt (`QT_QPA_PLATFORM=offscreen`) is possible for
smoke/load tests but cannot reliably render interactive drag states (mid-drag with
neighbor-shift animation) or hover states without a display server. State 1
was captured as a PNG (`bench-pr-h-1-rest.png`) via the headless render path.
States 2, 3, and 4 require interactive observation (no headless capture exists).

## State 1 — bench at rest (see bench-pr-h-1-rest.png)

Handle glyph (`::` rotated 90°) rendered at 0.30 opacity in each row's left gutter.
No row is lifted or shifted. Row separator hairlines visible. Action buttons rendered
in `secondary` (outlined) or `ghost` variant per section.

## State 2 — hover state (manual observation)

On mouse-enter to any BenchRow: handle glyph opacity ramps to 0.65 via
`Behavior on opacity { NumberAnimation { duration: 120 } }`. Row background shifts
from transparent to `surface.raised` at 0.42 alpha. Cursor does not change until
the mouse is over the handle specifically, where it becomes `SizeVerCursor`.

## State 3 — mid-drag (manual observation)

On mouse-press on the handle (`dragHandle` MouseArea):
- `root.dragging = true` fires.
- `z` jumps from 0 to 20 (immediate, no animation — intentional so the dragged row
  visually floats above the stack).
- Row background darkens to `surface.raised` at 0.72 alpha (existing wiring).
- Handle glyph opacity rises to 0.80 (`dragHandle.pressed` branch).
- Cursor changes to `ClosedHandCursor`.
- As the mouse moves vertically, `root.y` tracks the cursor (clamped to
  `[dragMinY, dragMaxY]`). `moveRequested(root.y)` fires on each position change.
- `sectionRoot.targetIndex` updates via `Math.round(localY / rowHeight)`.
- Non-dragged rows that fall between `draggingIndex` and `targetIndex` shift by
  ±78px (one `rowHeight`) via `Behavior on y { NumberAnimation { duration: 120 } }`.
  This opens a visible slot at the landing position.
- The dragged row has its `Behavior on y` disabled (`enabled: draggingIndex !== index`
  is `false` for the dragged row) so it tracks the cursor without 120ms lag.

## State 4 — post-drop (manual observation)

On mouse-release:
- `root.dragging = false`.
- `dragEnded()` fires. BenchSurface splices `rowOrder`: removes `draggingIndex`,
  inserts at `targetIndex`.
- `draggingIndex` and `targetIndex` reset to -1; `dragYOffset` resets to 0.
- The `Behavior on y` re-enables for the formerly-dragged row (it was the spliced
  row and now has a new `index` binding from the Repeater). All rows animate to their
  new slot positions at 120ms — the landing is congruent with the neighbor shifts.
- If release is in the original slot (targetIndex === draggingIndex), no array
  mutation occurs.
- `z` returns to 0; cursor returns to default.

## Constraints verified

- Cross-stage drag is structurally impossible: `drag.minimumY = 0`,
  `drag.maximumY = (rowOrder.length - 1) * rowHeight`, and `clip: true` on
  `rowsContainer` prevent any row from visually escaping its section.
- No `DropArea`, no `Drag.XAxis`, no `Drag.XAndYAxis` (static guard test confirms).
- Action button and action menu are unaffected: the drag `MouseArea` covers only
  `handleSlot` (left gutter); the Action button is anchored to the right edge with
  no overlap. The whole-row `mouseArea` is not a `drag.target`.
- No new motion tokens, no new theme tokens, no new variant strings.
