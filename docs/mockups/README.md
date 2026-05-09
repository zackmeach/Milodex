# docs/mockups — Design Reference

These files are **design-only reference artifacts**. They are not shipped code, not imported by the application, and not executed at runtime.

## Contents

| File | Purpose |
|---|---|
| `bench-brief.md` | Implementation brief for the Bench surface (operator Kanban). Describes layout, row anatomy, drag-and-drop mechanics, modal patterns, and visual escalation by stage. If the brief and the HTML mockup disagree, the mockup wins. |
| `bench-surface.html` | Static HTML mockup of the Bench surface. Uses actual Editorial Dark token values (post-2026-05-08 brightness pass) and live Google Fonts. Open in any browser. Shows the complete vertical-stage layout, all five action button variants, the LIVE lead-story treatment, and the blocked-promotion modal in its open state. |

## Usage

Open `bench-surface.html` in a browser during implementation. It is the visual source of truth for spacing, typography weight, and color decisions on the Bench surface.

These files are intentionally kept in `docs/mockups/` rather than deleted after implementation — they document the design intent at the time of build and serve as a reference for future surface work.
