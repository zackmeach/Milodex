# Milodex Frontend Framework Audit — "If We Started Over Today"

**Date:** 2026-06-20
**Question:** Was QML the right frontend choice for Milodex, and if not, what would be?
**Posture:** Neutral. Not a defense of the existing choice, not a sales pitch for a switch. The goal is the honest tradeoff so the operator can decide with priorities he sets.
**Hard constraint:** Must launch as a standalone desktop application in its own window — not a web app, not a browser tab. (Tauri-v2-style "standalone window OR browser" qualifies.)

> **For the presentation:** the single most important idea in this document is the **Python-Integration Class (A/B/C/D)** in §3. Every framework's "fit" for Milodex collapses to which class it falls in. If the deck teaches one concept, teach that one. The second most important is §5 — the point-by-point adjudication of the operator's four stated doubts, because that is where intuition and evidence diverge.

---

## How this audit was produced

This is not a single model's opinion. It is the synthesis of a **27-agent, web-grounded research workflow** (~2.1M tokens) run on 2026-06-20:

- **21 framework agents** — one per framework or tight cluster — each doing independent live web research (GitHub stars, release cadence, governance, licensing, packaging, AI-tooling, testing story) current to mid-2026.
- **5 cross-cutting "deep-dive" agents** — one per decision axis (AI-assistability, testability, unique-look, migration cost, longevity/licensing) — each tasked to *adjudicate a specific belief the operator holds*, neutrally, with citations.
- **1 synthesis agent** — builds the weighted scored matrix and the finalist shortlist.

Every claim about Milodex's own code was first grounded by direct inspection of the repository (LOC counts, the command-facade seam, the ADRs that recorded the original decision). Where the audit corrects the operator, it does so with evidence from his own repo.

---

## 1. TL;DR — the honest bottom line

**If you started Milodex today, QML would still be a defensible choice, and the cost of switching is high enough that staying is the rational default.** This is a *mild lean-to-stay* — not a toss-up, and not a switch.

The unvarnished tradeoff:

- **QML wins the two axes that matter most for a Python-strong solo dev:** in-process Python integration (no IPC seam, no second backend language) and a top-tier unique-look ceiling (the exact reason it was chosen over Tkinter/Widgets).
- **QML loses one axis you correctly identified — AI-assistability — but you materially overstated it.** The gap is real but narrower in 2026 than you believe, and it is being actively closed by Qt's *own* official MCP docs server + agent-skills (both already loaded in this Claude Code session).
- **Two of your four doubts are largely refuted by your own repo:** you already have ~22.5k LOC of working GUI tests (including live-render harnesses), and your stack is backed by a profitable public company on an irrevocable LGPL license — among the *lowest* governance-risk options on the board, not the riskiest.
- **No alternative dominates QML across your weighted axes.** Everything that beats it on AI/testability costs you either the unique-look goal you switched to win, a non-Python host language you're weak in, a sidecar IPC seam, or a large capability-neutral rewrite of ~20k LOC.
- **The one scenario where switching is genuinely attractive:** if AI-assisted iteration *on the view layer specifically* becomes your dominant, measured bottleneck — then the cleanest move is **PyWebView** (web UI in a native window, Python stays in-process), *not* a sidecar or second-language stack. But even that is a multi-evening rebuild that relocates a working UI rather than adding product.

**The reframe worth internalizing:** your clean `commands/bench.py` facade caps the *blast radius* of any swap — the trading core is never touched — but it does **not** make a swap cheap. And "mainstream = safer" is false here; your incumbent is governance-safer than several frameworks that *feel* more mainstream (Flutter desktop, CustomTkinter, the entire PySimpleGUI lineage).

---

## 2. Milodex's actual frontend situation (grounded facts)

These numbers were measured directly from the repo on 2026-06-20. They define the migration cost and the "fit" of every option.

| Fact | Value | Why it matters |
|---|---|---|
| Backend language | **100% Python** (~46k src LOC) | Will not be rewritten under any realistic scenario. This is the spine of the whole analysis. |
| Current frontend | **PySide6 + Qt Quick (full QML)** (ADR 0033) | The incumbent under review. |
| QML view code | **~11.3k LOC across 44 `.qml` files** | The "must-rebuild" surface if you switch. |
| Python GUI code | **~8.7k LOC across 34 files**, ~20 `QObject` bridge/state classes | Thin Qt adapters — *less* to rebuild than "20 bridges" sounds, because the query logic lives elsewhere (below). |
| GUI tests | **~22.5k LOC across 42 files** | **The single largest and least-portable asset.** Bigger than the view code itself. |
| The seam | **`commands/bench.py` facade (ADR 0051)** imports no PySide6/QML, returns JSON-serializable frozen dataclasses | Business logic is **0% coupled** to QML. Any frontend that can call Python or read JSON drives Milodex unchanged. |
| Qt-free read-model layer | **~1,665 LOC** (`query_helpers`, `snapshot_builders`, `ledger_builders`, `strategy_row`, `bench_actions`, `row_formatters`) — verified zero Qt references | **Reusable by any frontend**, on top of the facade and 100% of CLI/risk/execution/promotion logic. |
| Design tokens | **~1,060 LOC of QML** (`Theme.qml`, 3 theme files, `Formatters.qml`) | The *values* (color/spacing) port as data; the *QML encoding* must be transcribed. |
| Distribution | PyInstaller `--onedir` + Inno Setup, **unsigned, Windows-only, per-user `%LOCALAPPDATA%`** (ADR 0037) | Single friend-installable `.exe`. Any candidate must preserve this. |
| Polish target | **"Tier B designed product"** — genuinely unique, non-generic, peer-impressive (FOUNDER_INTENT #3/#4) | The bar that disqualifies "clean but conventional" options. |
| Data shapes | ~12 strategies, thousands of event-store rows, dashboards/status-panels/animated transitions; **no high-frequency tick rendering** | Modest. No framework is stressed by the workload — performance is never the deciding axis. |

**The original decision (ADR 0033, 2026-05-07) already named your exact doubt as an accepted cost:** *"Smaller community velocity than web frontends... AI-tooling familiarity with QML... is real but smaller."* This audit's job is to re-weigh that accepted cost with 13 months of hindsight and the 2026 tooling landscape.

---

## 3. The conceptual spine: Python-Integration Class (A/B/C/D)

Every desktop UI framework's fit for a **100%-Python backend** collapses to one question: **how does the UI talk to your Python?** This produces four classes, and the class matters more than any individual framework's features.

```
A — IN-PROCESS PYTHON          UI and backend in one process. Direct function calls.
    UI ──calls──► Python        No IPC, no second backend language, no serialization seam.
                                ✅ Lowest friction for a Python codebase.
                                e.g. QML(PySide6), Qt Widgets, Tkinter, Kivy, Toga,
                                     wxPython, Dear PyGui, Slint-python, GTK/PyGObject

B — EMBEDDED WEBVIEW + PYTHON   HTML/CSS/JS UI inside a native OS webview, Python backend
    UI(webview) ◄─bridge─► Py    in the SAME process via a JS↔Python bridge. Standalone
                                window, no Chromium bundle. Web look + Python in-process.
                                e.g. PyWebView, NiceGUI(native mode)

C — PYTHON SIDECAR (IPC)        Non-Python UI talks to Python as a subprocess/sidecar over
    UI(other lang) ◄─IPC─► Py    stdio/HTTP/websocket. Python demoted to a serialized
                                service. Adds a second build stack + process-lifecycle glue.
                                e.g. Electron, Tauri, Wails, Flutter, Avalonia, WPF,
                                     Compose, .NET MAUI, Flet (local socket)

D — REQUIRES BACKEND REWRITE    Framework's ecosystem implies rewriting backend logic in
    (not viable)                its language. Off the table — the Python backend stays.
```

**Implications for Milodex:**

- **Class A is home turf.** It drives `bench.py` and the Qt-free read-model layer directly. Your incumbent (QML) is here. So are the cheapest possible moves.
- **Class B is the compromise that keeps Python in-process while getting the web's AI/community advantages.** This is where **PyWebView** lives — and why it is the strongest *switch* candidate.
- **Class C buys you the strongest AI/testing/community ecosystems (web, Flutter, .NET, Kotlin) but at a structural price:** Python becomes a PyInstaller sidecar behind a localhost/stdio seam, you add a second build pipeline and process-lifecycle glue, and the UI is written in a language you're weaker in. The `bench.py` JSON facade is *exactly* the sidecar seam ADR 0051 anticipated — so it's *possible* — but it's the wrong center of gravity for a solo Python codebase.

> **Presentation note:** this 4-box diagram is the load-bearing visual. Everything downstream is "which box, and what does that box cost you."

---

## 4. The scored matrix — all 21 candidates

Scores are 1–5 (5 = best for *this* operator). **Migration** is sized in the operator's own PR-bucket language (tiny / decent / large / rewrite). **Standalone** = passes the hard "own window" constraint.

Legend — **Py-fit:** Python-integration friction · **AI:** realistic AI-assisted-dev experience · **Test:** UI-layer testability · **Unique:** non-generic visual ceiling · **C/L:** community + 5-year longevity/governance.

### Class A — In-process Python (home turf)

| Framework | Py-fit | AI | Test | Unique | C/L | Migration | One-line take |
|---|:--:|:--:|:--:|:--:|:--:|:--:|---|
| **PySide6 — Qt Quick (QML)** ⭐ *incumbent* | 5 | 3 | 4 | **5** | 4 | **tiny** | Zero-migration; wins on Python-fit + unique-look; one soft spot (AI) actively narrowing. |
| **PySide6 — Qt Widgets** | 5 | **5** | **5** | 3 | **5** | decent | Strictly better than QML on your doubts — but drops the unique-look ceiling 5→3. |
| **Tkinter + CustomTkinter + ttkbootstrap** | 5 | **5** | 2 | 2 | 3 | large | Best AI-assist, but lands at "clean & conventional"; modern layer (CustomTkinter) is abandoned. |
| **Kivy + KivyMD** | 5 | 3 | 3 | **5** | 2 | large | High ceiling (OpenGL/GLSL) but swaps QML's DSL problem for KV's; KivyMD stale. |
| **Toga (BeeWare) + Briefcase** | 5 | 2 | 4 | **1** | 2 | large | Deliberate no-themes / WinForms-on-Windows = unique impossible. Briefcase (packager) is the useful half. |
| **wxPython** | 5 | 3 | 2 | 2 | 2 | large | Generic native chrome, weak UI tests, smaller community than the Qt side you're questioning. |
| **Dear PyGui** | 5 | 3 | 2 | 2 | 3 | large | Game-tools/debug-overlay aesthetic; great for a quant *terminal*, wrong for a "designed product." |
| **Slint (Python bindings)** | 4 | 3 | 3 | **5** | 3 | large | Native-GPU ceiling, in-process — but `.slint` corpus is *thinner* than QML and bindings are single-backer beta. |

### Class B — Embedded webview + Python (web look, Python in-process)

| Framework | Py-fit | AI | Test | Unique | C/L | Migration | One-line take |
|---|:--:|:--:|:--:|:--:|:--:|:--:|---|
| **PyWebView** | 4 | **5** | 4 | **5** | 3 | large | **The strongest switch candidate.** Web AI/ceiling, Python stays in-process, no IPC. Single-maintainer dep. |
| **NiceGUI (native mode)** | 4 | 4 | **5** | 3 | 3 | large | Best-in-class Python testing + official `llms.txt`, but generic-Material by default; native-mode packaging fragile. |

### Class C — Python sidecar / IPC (strongest ecosystems, structural tax)

| Framework | Py-fit | AI | Test | Unique | C/L | Migration | One-line take |
|---|:--:|:--:|:--:|:--:|:--:|:--:|---|
| **Flutter Desktop (Dart)** | 2 | **5** | **5** | **5** | 4 | rewrite | Strongest single answer to all four doubts — but Dart + sidecar; Google handed desktop to Canonical (2026). |
| **Electron** | 2 | **5** | **5** | **5** | **5** | rewrite | Maxes AI/test/community/ceiling; heaviest footprint; JS/TS+Node mandatory, Python sidecar. |
| **Tauri v2** | 2 | 4 | 4 | **5** | **5** | rewrite | Lean web-shell, great governance — but two unfamiliar ecosystems (JS/TS + Rust) where you're weakest. |
| **Avalonia UI (+ Uno)** | 2 | 3 | **5** | **5** | 4 | rewrite | Technically one of the strongest — elite headless tests, Skia ceiling — but full C#/XAML rewrite + sidecar. |
| **Compose Multiplatform (Kotlin)** | 2 | 4 | **5** | **5** | 4 | rewrite | Best-governed challenger (JetBrains/Apache-2.0); heavy Kotlin+Gradle+JVM learning cost + sidecar. |
| **.NET MAUI** | 2 | 4 | 4 | 4 | 4 | rewrite | Mobile-first; desktop is its off-center use case. Wrong center of gravity. |
| **WPF / WinUI 3** | 2 | 4 | 4 | 3 | 4 | rewrite | Good AI/test, clean license — but two-language/two-process, Windows-only, recognizably-generic Fluent. |
| **Flet (Python-native Flutter)** | 4 | 3 | 2 | 4 | 3 | large | Pure-Python UI (your strong language!) but mid-1.0-beta rewrite; weak view tests = lost coverage. |
| **Wails / NW.js / Neutralino** | 1 | 3 | 3 | **5** | 3 | rewrite | Forces a non-Python host language (Go/Node); a Python-first webview gives the same ceiling without it. |
| **Photino / Sciter** | 1 | 3 | 2 | 4 | 2 | rewrite | Photino is .NET-only; Sciter's Python binding abandoned since 2022. Loses on every flagged axis. |
| **Rust cluster (egui / Iced / Dioxus)** | 1 | 2 | 4 | **5** | 3 | rewrite | Highest ceiling, but bets your biggest strength (Python) on your biggest weakness (Rust); pre-1.0 churn. |

**Disqualified by the standalone-window constraint:** *none of the scored options* — every one ships a real native window. The pure-web stacks that fail the constraint (Streamlit, Gradio, Dash, plain FastAPI+browser) were excluded before scoring. Note that the pure-Python "simpler GUI" instinct (Tkinter/wxPython/Toga/Dear PyGui) fails on the **polish target**, not the window.

---

## 5. The heart: adjudicating your four doubts

You named four reasons you sometimes doubt QML. Here is what the evidence says about each — neutrally, including where you're right.

### Doubt #1 — "LLMs have little QML training data, so AI-assisted dev is weaker"
**Verdict: OVERSTATED (true premise, out-of-date conclusion).**

- **TRUE:** QML is a genuine low-resource DSL. Cross-language studies (arXiv 2406.00602, 2404.19368) put low-resource languages at ~21–24% Pass@1 (Racket 20.8%, Erlang 24.3%) vs >60% for mainstream. QML sits structurally in that low band, so raw base-model QML generation *is* weaker than Python/JS/Dart.
- **OVERSTATED because:**
  1. The same "<1% of training data is UI code" limit hits **even React** — LLMs "often fail to generate compilable React applications" (arXiv 2512.24570). "Mainstream = solved" is folklore; the web advantage is one of *degree*, not a categorical split.
  2. The gap is now actively closed by tooling: an **official Qt Documentation MCP server shipped 2026-05-12** (covers Qt 6.11 + 6.8 LTS, verified on Claude Code/Copilot), and **context7 indexes 2,269 QML snippets** at high source reputation. At the retrieval-augmentation layer, QML is roughly at parity with React/Flutter/Tailwind. *Both are live in this very environment.*
  3. Qt's **agent-skills plugin** (the `qt-development-skills` pack, already loaded here) lifts Claude Sonnet 4.6 from **64%→75%** on Qt's own QML100 benchmark and corrects the known pre-training biases.
- **Your *real* AI friction is mislocated.** It is empirically the **PySide `QObject` bridge** (`Signal` vs `pyqtSignal`, the `@Slot` decorator whose omission silently breaks QML-invoked methods), not QML markup scarcity. With ~20 bridge classes, that two-language seam is where AI degrades — and **most challengers don't remove it** (Slint, Kivy, Toga, Rust all have *worse* AI support than QML). Only the pure-Python-widget toolkits and the web stacks genuinely beat QML here.

> **Net:** directionally valid, materially out of date, and mis-located. AI-assistability alone does not justify abandoning QML; it justifies leaning on the Qt MCP and keeping bridge classes thin.

### Doubt #2 — "It doesn't seem very testable"
**Verdict: OVERSTATED, leaning FALSE as a blanket claim — refuted by your own repo.**

- Your repository contains **~22.5k LOC of GUI tests across 42 files**, with **94 live-render-harness sites across 23 files** that instantiate *real* `QQuickView`/`QQuickItem` trees offscreen (`QT_QPA_PLATFORM=offscreen`), pump the event loop, and assert on rendered behavior and recorded bridge calls — not just Python bridges. **You are already doing real rendered-QML testing.**
- The suite splits cleanly into two tiers: (a) ~20 bridge/state/read-model classes tested as **plain pytest with zero Qt**, and (b) live-render behavior tests via your hand-rolled offscreen subprocess harness (e.g. `test_bench_confirmation_modal_behavior.py` exists specifically to catch QML renames that "load clean" but resolve to `undefined`).
- **What's actually true:** rendered-QML testing is more *ergonomically awkward* than the web gold standard. Qt Quick Test (`qmltestrunner` + `SignalSpy`) is a real, long-stable framework — but it lives in a CMake/C++-flavored toolchain that's uncomfortable from pure-Python, which is exactly why you built your own harness instead. And the process-global `qmlRegisterSingleton` / Qt type-cache pollution is a genuine structural tax that forces subprocess isolation and produces your documented 1-skip.
- **The gold standard you're implicitly measuring against** is web (Playwright + Vitest + Testing Library). Flutter (`flutter_test` + golden files), Compose (`runComposeUiTest`), and Avalonia (`Avalonia.Headless`) are in the same top tier. **QML sits one notch below these on ergonomics, roughly level on capability.**
- **Critically:** three of the alternatives a Python dev reaches for first — **Tkinter, Kivy, Dear PyGui — are strictly *worse* on this axis** than where you already stand.

> **Net:** the categorical "not testable" is false; you're already testing the rendered layer. The honest gain from switching is "nicer harness, more LLM-writable tests, less subprocess ceremony" — real but incremental, and Qt shipped agentic test-gen skills (`qt-qml-test`, 2026-05-28) that narrow even that.

### Doubt #3 — "QML lacks the community and support of mainstream frameworks"
**Verdict: OVERSTATED, and on governance partly FALSE.**

- You're conflating **QML's niche with Qt's reach.** PySide6 is officially developed and funded by **The Qt Company** (FY2025 net sales **€216.3M**, ~1.5M developers), on a locked release train (6.9.0 Jun-2025 → 6.10.1 Feb-2026). The LGPLv3 grant on released Qt6 core modules is **legally irrevocable** — even a hostile pivot can't retroactively close PySide6 6.x. Decades of Qt knowledge transfer to QML concepts; active forums; current tutorials.
- **"Mainstream" is not a reliable proxy for governance safety** — it conflates popularity with survival risk. Counter-evidence the audit surfaced:
  - **Flutter** is maximally mainstream, yet Google laid off much of the team (Apr 2024) and **handed desktop maintenance to Canonical** (Google I/O 2026). Mainstream did not protect the desktop target.
  - **CustomTkinter** has 13k+ stars (mainstream-*looking*) yet is **abandoned since Jan 2024** (Snyk: "Inactive").
  - **PySimpleGUI** was popular and friendly, then **paywalled, got removed from PyPI, and the maintainer ended it.** Only the `FreeSimpleGUI` fork survives.
- The correct decision rule is not "mainstream vs niche" but **"permissive-or-irrevocable license + diversified-or-deep-pocketed backer + bus-factor > 1."** By *that* rule, PySide6 sits in the **top tier** alongside Compose-MP and Tauri — while several mainstream-feeling Python options (CustomTkinter, Flet, the PySimpleGUI lineage) sit at the bottom.

> **Net:** your instinct correctly ranks big-org frameworks above single-maintainer ones — but it wrongly implies your current stack is risky-niche when it's in fact among the *safest* options on the board. *(Caveat to avoid: PyQt6 — Riverbank, GPL-or-pay, single maintainer — is the risky Qt binding. You're on PySide6, which is the safe one. Don't switch to PyQt6.)*

### Doubt #4 (implicit) — "A Python-native frontend was the right call, and QML is the uniqueness sweet spot"
**Verdict: SPLIT — clause 1 TRUE, clause 2 OVERSTATED.**

- **"Python-native was the right call": TRUE and well-supported.** The backend is 100% Python and isn't being rewritten; in-process Python (QML's Class-A integration) avoids the sidecar-IPC seam *every* web-shell/Flutter/.NET/Kotlin/Rust challenger imposes; and Python is your strongest skill.
- **"QML is the uniqueness *sweet spot*": OVERSTATED.** On *absolute* ceiling the claim holds — QML genuinely escapes the generic Widgets/Tkinter look via `ShaderEffect` (baked `.qsb` fragment shaders), `QSGMaterialShader` custom scene-graph materials, Qt Quick particles, and the RHI that Qt renders its own scene graph with. That's real per-pixel custom-draw, top tier. **But QML has no uniqueness *advantage* — it ties web-tech, Flutter/Flet, Kivy, Slint, Compose, Avalonia, and the Rust cluster at the top.**
- **The deciding metric is uniqueness-*per-effort*, and QML is mid-pack there** because its thin-corpus DSL means AI gets you to the same look more slowly than HTML/CSS/JS (maximal corpus) or Flutter/Dart (elite). The web stack wins uniqueness-per-effort because the *same* top ceiling is reachable with the densest AI support. For Milodex's modest data, the web ceiling (now WebGPU-class, shader-grade) is far above anything the app will ever exercise.

> **Net:** the look fear that drove your original decision was *valid* and QML *resolved* it. The belief that no alternative matches QML's uniqueness is *false* (parity, not lead). QML is the uniqueness sweet spot **only within the in-process-Python subset** — where its sole ceiling-peers (Kivy, Slint) have worse AI corpora and decaying/beta polish layers.

---

## 6. The finalist shortlist

Four realistic options survive the constraint + the operator's profile. Presented neutrally, with the tradeoff each represents.

### Option 1 — **Stay on QML, invest the migration budget in polish + Qt's agentic tooling**
*For whom:* you, as actually profiled — Python-strong, JS/Rust-weak, solo, indefinite maintenance tail, wants Tier-B polish without a capability-neutral rewrite.

| Strengths | Weaknesses |
|---|---|
| **Zero migration cost** — the only option that ships no rewrite | AI-assistability is genuinely mid-pack; the `QObject` bridge is the real AI-confusion locus |
| In-process Python; drives `bench.py` + the ~1.6k-LOC Qt-free read-model layer directly | Smaller QML-on-Python community than web/Flutter/.NET |
| Top-tier unique-look ceiling — the exact axis that motivated the original choice | Rendered-QML tests carry process-global-singleton pollution (the 1-skip + subprocess ceremony) |
| Already has ~22.5k LOC of working GUI tests | |
| Lowest governance/licensing risk in the field | |

**Cost to switch:** nothing — it's the incumbent. The cost is *opportunity*: forgoing the AI/test ergonomics a web/Flutter stack would bring.

### Option 2 — **PyWebView** (in-process Python + HTML/CSS/JS in a native WebView2 window)
*For whom:* if you decide AI-assistability and design ergonomics are worth a rewrite, but refuse to give up in-process Python or take on a non-Python *host* language.

| Strengths | Weaknesses |
|---|---|
| **Keeps Python in-process** — no sidecar/IPC seam, no second backend language | Adds the entire HTML/CSS/JS surface — your weaker language |
| Maximal AI-assistability + design ceiling (full web stack, WebView2) | PyWebView is a single-maintainer project (bus factor of one) touching WebView2 COM/Win32 |
| Reuses 100% of `bench.py` + the Qt-free read-model layer over a thin JSON bridge | Full rebuild of 11.3k view LOC + theme transcription + test rebuild |
| Native standalone window, BSD-3, existing PyInstaller+Inno pipeline mostly carries over | |
| GUI tests port to mature Playwright/Vitest targets (**not** lost coverage) | |

**Cost to switch:** a **large**, capability-neutral rebuild that *relocates* a working UI rather than adding product value. *(Note: "Eel" — the other in-process-Python webview option — was archived June 2025. PyWebView is the only live path in this class.)*

### Option 3 — **PySide6 — Qt Widgets** (same-Qt, drop the visual ceiling)
*For whom:* if you conclude the Tier-B unique-look goal was over-specified, and would trade it for the strongest AI-assist + testability while staying entirely in Python and the Qt runtime you know.

| Strengths | Weaknesses |
|---|---|
| **Best-in-class AI-assist** (pure Python, huge corpus) and **testability** (pytest-qt/qtbot) | **Uniqueness ceiling drops 5→3** — surrenders the exact FOUNDER_INTENT goal |
| Same in-process Python, same packaging, same LGPL — **cheapest real move** | Distinctive look requires cumbersome `QPainter`/QSS/subclassing |
| Reuses 100% of facade + Qt-free read-model layer; only binding shells need light edits | Discards 11.3k QML view LOC + ~1k theme LOC |
| Lowest governance risk, tied with QML | |

**Cost to switch:** a **decent** rebuild of the view layer in QSS/widgets, for an AI/test upgrade but a polish downgrade.

### Option 4 — **Tauri v2** (lean web-shell, Python sidecar)
*For whom:* if you want the full web AI/community/polish ceiling **and** a lightweight standalone footprint, and you're willing to pay a JS+thin-Rust learning tax for the best long-term ecosystem bet.

| Strengths | Weaknesses |
|---|---|
| Maxed visual ceiling + strong, well-governed community (~108k stars, foundation + commercial backing) | **TWO unfamiliar ecosystems** (JS/TS frontend + Rust/cargo glue) — your weakest currencies |
| Tiny footprint (OS WebView2, no Chromium bundle); Win11 needs no runtime bundling | Python demoted to a PyInstaller sidecar over localhost IPC (~2s cold start, Windows PID quirks) |
| Mainstream test story (mockIPC + tauri-driver + Vitest/Playwright) | Second build pipeline; ~22.5k GUI tests fully discarded |
| `bench.py` JSON facade is exactly the sidecar seam ADR 0051 anticipated | |

**Cost to switch:** a full frontend **rewrite** in two new languages plus a new IPC/process-lifecycle seam and a doubled build stack — the highest learning cost of the realistic finalists.

---

## 7. Migration cost reality + the sunk-cost reframe

**Your facade caps the blast radius, but it does NOT make a swap cheap.** Both halves of that sentence are load-bearing.

**What's reusable in *every* scenario (the facade's gift):**
- `bench.py` facade (2,766 LOC) + 100% of CLI/risk/execution/promotion logic — untouched.
- ~1,665 LOC of verified Qt-free read-model query/format logic — reused unchanged.
- The PR12 decompose already split the god-module along exactly this GUI-agnostic seam. The trading core is *never* at risk in a frontend swap.

**What must be rebuilt for any non-Qt target (the cost the facade does NOT shrink):**
- **11.3k QML view LOC** → re-authored in the target's view language (large).
- **~18 binding shells** → re-wired per target (their *query bodies* already live in the Qt-free modules, so only the binding/lifecycle/polling wrapper is lost — less than it sounds).
- **~1,060 LOC of QML theme tokens** → the values transcribe as data (CSS vars / Python dicts / `.slint`), a small-to-decent transcription, not free.
- **~22.5k GUI test LOC** → **the hidden multiplier.** Toward web/PyWebView/Electron it ports to mature Playwright targets (rebuild). Toward **Flet/Slint/Dear PyGui it becomes lost coverage**, because their view-layer test tooling is weak-to-absent.

**The sunk-cost reframe — read this twice:**

> The **~22.5k LOC of GUI tests is a genuine reason to stay** — it is current, working, risk-coverage that a switch to a weak-view-test framework *destroys*. That is not sunk cost; that's live insurance you'd be throwing away.
>
> The **11.3k QML view LOC and ~1k theme LOC *are* sunk cost** — if a switch were independently justified on the other axes, "but I already wrote the QML" is not a reason to stay. The views would be rebuilt anyway, and the tokens transcribe cheaply as data.
>
> **The honest line:** the tests are a reason to stay; the view code is not. The facade is **insurance against a bad outcome, not a discount that makes switching free.** Any non-Qt target is a large, multi-evening, capability-*neutral* rebuild that produces zero new product.

---

## 8. Decision lenses — pick by what *you* weight most

The audit lands on "mild lean-to-stay," but the right answer depends on which axis dominates *your* priorities. Use this as the decision tool:

| If you weight this highest… | …then the answer is | Why |
|---|---|---|
| **Python-integration friction + migration cost** (the realistic solo-maintenance lens) | **Stay on QML** | Only zero-rewrite, zero-IPC option. Everything else adds a sidecar seam or rebuilds ~20k LOC for no new capability. |
| **AI-assistability** (your stated #1) **AND** insist on in-process Python | **PyWebView** | Maximal LLM corpus, no second *backend* language, Python stays in-process. Accept owning an HTML/CSS/JS surface + a single-maintainer dep. |
| **AI-assist + testability**, willing to drop the unique-look goal | **Qt Widgets** | Strictly better than QML on both, same Python, same runtime, cheapest real move. You lose the Tier-B ceiling. |
| **Unique-look ceiling + long-term ecosystem**, accept a two-language tax | **Tauri v2** (lean) or **Electron** (heavy) | Both max visual + AI + test. Tauri if footprint matters; Electron if you want the deepest ecosystem and zero Rust. |
| **Best testability + visual ceiling regardless of language**, don't mind C#/Kotlin | **Avalonia** or **Compose Multiplatform** | Technically strongest challengers — but full rewrite in a weak-for-you language + Python sidecar. Contradicts the maintenance-tail lens. |

**One caution that holds regardless of decision:** never depend on a **single-maintainer or pre-1.0 GUI layer** for an indefinite solo maintenance tail. That rule disqualifies CustomTkinter (abandoned), KivyMD (stale), Flet's in-flight 1.0 rewrite, Slint's beta Python bindings, and the Rust 0.x cluster — and it is the lesson PySimpleGUI taught the hard way.

---

## 9. What the audit would tell you to *do* (neutrally)

This is not "stay" or "switch" as an order — it's the action that follows from the evidence, with the switch trigger made explicit:

1. **Default action:** keep QML. Spend the migration budget you were *considering* on **polish** instead, and **habitually engage Qt's agentic tooling** (the `qt-development-skills` plugin + Qt docs MCP, both already in this environment) — that is what closes your real #1 gap. Keep `QObject` bridge classes thin, since the bridge (not QML markup) is the true AI-friction locus.
2. **Cheap testability win, no migration:** adopt Qt Quick Test (`qmltestrunner` + `SignalSpy`) for your densest rendered components, and use Qt's `qt-qml-test` skill to generate the `tst_*.qml` files. This removes the one legitimate standing irritant (subprocess ceremony for the hottest tests) without a rewrite.
3. **The switch trigger (be specific with yourself):** *if and only if* AI-assisted iteration **on the view layer specifically** becomes the measured bottleneck that's actually slowing you down — not a vague feeling — then migrate to **PyWebView**, because it is the only option that gains the web's AI/ceiling advantages while keeping Python in-process and reusing your entire Qt-free read-model layer. **Do not** jump to a sidecar/second-language stack (Tauri/Electron/Flutter/.NET) to solve a view-layer AI-speed problem; the cure is heavier than the disease.
4. **Never** adopt a single-maintainer or pre-1.0 GUI dependency as the load-bearing visual layer (see §8 caution).

---

## Appendix A — Suggested presentation arc (for Claude Design)

A deck that lands the insight cleanly:

1. **The question** — "Was QML right? What if we started today?" + the hard constraint (own window).
2. **The answer up front** — the one-line bottom line: *mild lean-to-stay, here's the honest tradeoff.* (Lead with the conclusion; this is an audit, not a mystery.)
3. **Milodex's real situation** — the grounded numbers (§2 table) + the killer fact: the facade means business logic is 0% coupled to the frontend.
4. **The 4-box mental model** — Python-Integration Class A/B/C/D (§3 diagram). The load-bearing visual.
5. **The matrix** — all 21 scored, grouped by class (§4). Let the colors do the talking.
6. **The four doubts, adjudicated** — one slide each (§5). This is the emotional core: where your intuition was right (AI gap is real) and where the evidence corrects you (testability, community, governance). Use the "TRUE / OVERSTATED / FALSE" labels as headers.
7. **The four finalists** — one slide each (§6), as tradeoff cards.
8. **The sunk-cost reframe** — the "tests are a reason to stay; view code is not" slide (§7). The most counterintuitive, most valuable idea.
9. **The decision lenses** — the "pick by what you weight" table (§8). Hands the decision back to you.
10. **What to do** — the action list (§9), with the switch trigger spelled out.

## Appendix B — Frameworks researched (exhaustiveness check)

**Scored (21 / clustered):** PySide6 Qt Quick (QML) · PySide6 Qt Widgets · Tkinter + CustomTkinter + ttkbootstrap · Kivy + KivyMD · Toga/BeeWare · wxPython · Dear PyGui · Flet · PyWebView (+ Eel) · NiceGUI · Electron · Tauri v2 · Wails + Neutralino + NW.js · Photino + Sciter · WPF + WinUI 3 / Windows App SDK · Avalonia + Uno · .NET MAUI · Flutter Desktop · Compose Multiplatform · Slint (+ GTK4/PyGObject) · Rust cluster (egui/eframe, Iced, Dioxus).

**Considered and excluded before scoring** (fail the standalone-window constraint — browser-only): Streamlit, Gradio, Dash/Plotly, plain FastAPI/Flask + browser frontend. **Cautionary archetype** (not a candidate): PySimpleGUI / FreeSimpleGUI — included only as the governance-failure worked example.

---

*Produced by a 27-agent web-grounded research workflow, 2026-06-20. All Milodex code facts verified by direct repository inspection. Framework facts current to mid-2026; AI-tooling landscape (Qt MCP, agent-skills, context7 coverage) verified live in the authoring environment.*
