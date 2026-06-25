# Handoff: Hermes — Research Co-Collaborator Dashboard & Workspace

## Overview
Hermes is a persistent, always-running scientific co-collaborator agent. This package specifies its **operator-facing UI**: a calm "home" surface where a returning scientist sees what happened while they were away, what needs their decision, and what the agent + its sub-agent fleet are doing right now — plus a **Workspace (IDE) mode** for hands-on review of the agent's file edits, runs, and output.

The product model (the spine of the design):
- **Calm by default, depth on demand.** The default is orientation, not a dense control panel. A previous "mission-control" version was rejected for being too busy — do not reintroduce that density on the home surface.
- **Two modes, one app:** a **Home** surface (this is where you land) ⇄ a **Workspace** (IDE drop-in). The conversation persists as a rail inside the Workspace so it always feels like a collaborator (Cursor pattern).
- **The agent narrates in the first person.** Copy is written as Hermes speaking ("I re-ran…", "I've paused on…"). Preserve this voice — it is the most-loved part of the design.

The Home surface ships with **three switchable views** of the same data (a view switcher in the top bar). They are alternative information architectures, not throwaway variants — but **Stream is the approved primary/default**. Build Stream first; Brief and Surface are secondary and can follow.

## About the Design Files
The files in this bundle are **design references created in HTML** (a streaming component format). They are prototypes showing the intended look, layout, copy, and interaction behavior — **not production code to copy directly**.

The task is to **recreate these designs in the target codebase's environment** using its established patterns and libraries. The handoff doc itself flags the likely target as `apps/desktop` (Electron + React + nanostores) with a Monaco editor and xterm for the Workspace. Use the codebase's existing component library, state layer, and styling system; treat the HTML/inline-styles here as a spec for visual + behavioral output, not as source to port line-by-line.

Ignore the prototype's internal framework details (the `.dc.html` format, `<sc-for>`/`<sc-if>` control-flow tags, the `LiveLog` child component, `support.js`). They are scaffolding for the prototype runtime only.

## Fidelity
**High-fidelity (hifi).** Final colors, typography, spacing, copy, and interactions are all intentional and specified below with exact values. Recreate the UI faithfully using the codebase's libraries. Where this doc gives a hex or px value, it is the intended value.

The only deliberately faked element is the volcano-plot image, shown as a diagonal-striped placeholder labeled `PNG`/`plot` — wire it to the real rendered figure artifact.

---

## Design Tokens

### Color — light surfaces
| Token | Hex | Use |
|---|---|---|
| `paper` | `#F6F5F2` | app background (soft off-white) |
| `raise` | `#FFFFFF` | cards / panels |
| `ink` | `#14161D` | primary text |
| `soft` | `#565C6B` | secondary text |
| `faint` | `#9398A6` | tertiary text, mono labels |
| `line` | `#E7E9EF` | card borders |
| `line-2` | `#EEF0F4` | inner row dividers |
| `segmented-bg` | `#ECEEF3` | view-switcher track background |
| `btn-border` | `#DDE0E8` | secondary button border |

### Color — accent (petrol teal — spend ONLY on active/working/primary-action)
| Token | Hex | Use |
|---|---|---|
| `accent` | `#0D6E78` | primary buttons, active state, "working" dots, active env |
| `accent-hover` | `#0A5660` | primary button hover |
| `accent-soft` | `#0D6E78` @ 7–14% alpha (`#0D6E7812`, `#0D6E7814`, `#0D6E7822`) | tinted fills, badges, avatar bg |

### Color — semantic (kept distinct from accent)
| Token | Hex | Use |
|---|---|---|
| `good` | `#2E9E66` | done / synced / success, jobs-finished |
| `warn` | `#B9802C` | decision pending / paused / queued-amber |
| `crit` | `#D8453F` | blocked / error / stalled |

### Color — dark surfaces (Surface view, editor, terminal)
| Token | Hex | Use |
|---|---|---|
| `surface-bg` | `#0C0E13` | Surface view background |
| `code-bg` | `#101219` | editor + terminal + LiveLog box background |
| `panel` | `#14171F` | dark cards (constellation, night tape) |
| `panel-2` | `#181B24` | focus card, peek layers |
| `panel-3` | `#161922` | deepest peek layer |
| `dark-border` | `#232733` | borders on dark |
| `dark-text` | `#D6DBEA` | code text |
| `dark-soft` | `#8A93AB` | secondary on dark |
| `dark-faint` | `#5E657A` | tertiary / comments on dark |
| `accent-on-dark` | `#5FCAD4` / `#6CCFD8` / `#BEE8ED` | teal text/labels on dark (light → lighter) |

### Code syntax tokens (on `code-bg`)
keyword `#8E97E8` · string `#7ED3A6` · function `#E0A85A` · number `#E08AA8` · comment/dim `#5E657A` · base text `#D6DBEA` · diff-add bg `#2e9e6614` (gutter `#2e9e6610`, `+` `#3fae74`) · diff-del bg `#d8453f12` (`-` `#e0635f`).

### Typography
Three families (load via the codebase's font pipeline):
- **Newsreader** (serif, opsz 6–72, wght 300–600) — **reserved for the agent's voice**: headlines, the agent name "Hermes", and the "H" avatar glyph. Tracking `-0.022em` to `-0.026em` on large sizes.
- **Geist** (sans, wght 300–700) — all functional UI text, body, buttons.
- **Geist Mono** (mono, wght 400–600) — data, timestamps, file paths, status labels, code, and all uppercase micro-labels.

Type scale (key sizes):
- Agent hero headline (serif): 27px / line-height 1.26 / `-0.026em`
- Stream agent headline (serif): 20px / 1.32 / `-0.024em`
- Surface hero (serif, on dark): 23px / 1.28 / `-0.022em`
- Section/body emphasis (sans): 14–16px / 1.55–1.6, weight 600 for titles
- Stat numbers (mono): 24px, weight 600, tabular-nums
- Micro-label (mono, uppercase): 10px, `letter-spacing 0.13em`, color `faint`
- Timestamps / status (mono): 10–11.5px, tabular-nums

### Radius / borders / shadow
- Cards: `border-radius` 16px (main), 14px (rail), 12px (small/feed cards), 18px (dark panels)
- Borders: hairline 1px `line` (light) / `dark-border` (dark). **Flat — no drop shadows on cards** (an earlier shadowed/depth treatment was explicitly rejected).
- Buttons: radius 7–11px. Pills/chips: `border-radius 999px`.
- Toast: radius 11px, shadow `0 12px 34px -16px rgba(20,22,29,.5)`.

### Motion (minimal — gate everything behind `prefers-reduced-motion: reduce`)
- `breathe` (opacity 0.4→1, 2.2–2.6s ease-in-out) — "working"/live dots.
- `wave` (3 dots, translateY, staggered 0/.16/.32s) — agent typing/working indicator.
- `glow` (expanding box-shadow ring, 2.4s) — the "now" node and Surface core.
- `blink` (1.1s steps) — editor/terminal caret.
- Toast/item entrance: ~0.22s ease. **Do not loop entrance animations** on list items that re-render (causes flicker/freeze) — entrances should run once.

---

## Screens / Views

### TOP BAR (shared on Home)
Sticky, `paper` bg with blur, 1px bottom border `line`, padding 11px 22px.
- Left: "Hermes" (Newsreader 17px/600) + "· research collaborator" (Geist 12.5px, `faint`).
- View switcher (segmented control, `segmented-bg` track, radius 9px): **Brief · Stream · Surface**. Active pill = white bg, `accent` text, subtle 1px shadow; inactive = transparent, `soft` text.
- A `Workspace →` button (secondary: white, `btn-border`, `soft`) — navigates to Workspace mode.
- Right: active environment chip (`sb-9f2a`, teal pill with breathing dot), a "working" status (breathing dot + `soft` text), and an "H" avatar (28px, radius 9px, `accent-soft` bg, Newsreader).

---

### VIEW 1 — STREAM  *(primary / default)*
**Purpose:** Catch up on what happened overnight and clear what needs you, as a time-ordered narrative from the agent, with actions always reachable.

**Layout:** centered max-width 1100px; CSS grid `1fr 312px`, 34px gap, `align-items:start`. Left = feed column; right = sticky action rail (`position:sticky; top:78px`).

**Feed column (top → bottom):**
1. **Agent summary** — "H" avatar + a block: name "Hermes" (sans 14/600) + "caught you up · 7:04 AM" (mono, faint); a serif headline (the day's finding); a first-person paragraph (`soft`, max 58ch) summarizing jobs landed / findings ready / things needing a call.
2. **Vertical timeline** — a 2px `line` rail at left (`padding-left:30px`); each event = a 9px dot (color by tone: good/crit/accent) with a 4px `paper` ring, a mono timestamp (faint), and a line of text with the actor in bold ink + rest in `soft`. Events are oldest-first.
3. **"delivered overnight"** node — a green dot, then a stack of **finding cards** (white, 1px `line`, radius 12px): type badge (DOC/IMG/DATA/LIT — tinted square, mono), title (13.5/600), mono meta, mono time.
4. **"now" node** — a teal dot with the `glow` animation; "right now" mono label (accent) + current focus title; then the **live log box** (see LiveLog component).

**Sticky rail (3 cards, each white / 1px `line` / radius 14px):**
- **Needs you** `[count]` — one row per item: kind label (mono 9px, colored by item accent), title (13/600), and a compact primary button (`accent`, white text) + a text secondary (`faint`). See *Needs-you items* below.
- **Working now** — one row per specialist: status dot (color by status, breathing if working) + name (mono) + status word (mono, colored).
- **Environments** — rows: dot + name (mono) + status word. The SFTP row is the actionable/error one (see *SFTP* below).

---

### VIEW 2 — BRIEF  *(conservative)*
**Purpose:** The same information as a calm document — narrative hero + a classic panel dashboard. This is the "combine" of the agent's narration with structured panels.

**Layout:** centered max-width 1180px. A full-width **narrative hero card**, then a `1.7fr 1fr` grid.

**Narrative hero (white card, radius 16, padding 22/24):**
- Header row: "While you were away" (mono micro-label) + "7h 20m · since 11:40 PM" (mono) + a right-aligned toggle button "Hide/Show overnight log".
- "H" avatar (34px) + serif headline (27px) + two first-person paragraphs.
- **Stat strip** (4 cells, divided by 1px `line-2`): big mono numbers + mono micro-labels — `3 jobs finished` (good), `2 findings ready` (ink), `[count] need you` (accent), `$0.31 compute spent` (ink).
- Collapsible **overnight log** (toggled): one row per event — mono timestamp (62px col) + actor-bold text + a tone dot at right.

**Left column:** *Needs you* panel (full item cards with two buttons), *Fresh findings & deliverables* panel (rows with type badge).
**Right column:** *Right now* panel (breathing dot + focus title + LiveLog), *Working for you* panel (specialist rows with one-line description), *Active projects* panel (rows: status dot + name + mono meta), *Environments* panel.

---

### VIEW 3 — SURFACE  *(experimental)*
**Purpose:** A dark "command surface" — see the fleet as a living system and clear decisions one at a time. Highest visual intensity; optional.

**Layout:** dark (`surface-bg`), max-width 1180px, text `dark-text`. A mono "Command surface" label + run meta, a serif hero (on dark), then a `1fr 1fr` grid:
- **Constellation** (left, `panel` card): a 312px-tall relative box. A faint 222px ring centered; a 64px teal **orchestrator core** ("H", Newsreader, with `glow`); 4 **specialist nodes** absolutely positioned around it (top / right 84% / bottom / left 16%): a status dot (ringed; breathing if working) + name (mono 11/600) + status word (mono, colored).
- **Focus-triage stack** (right): "Clear these `[count]`" + "one at a time". The first need renders as a large card (`panel-2`, radius 16) with kind label, big title (18/600), detail, and large Approve/secondary buttons. Behind it, 1–2 **peek layers** (`panel-2`/`panel-3` offset down + inset) imply a stack. Acting on a card removes it and the next surfaces; when empty → a "Queue cleared" check state.

Below the grid (full width): the **night tape** (a thin dark timeline 11:40 PM → now, with colored job bars on 3 lanes — green literature / teal compute / amber dashed queued / teal active leading edge — and a teal "now" line with glow), then **environment chips** (pills on dark; SFTP error chip is red with inline re-auth).

---

### WORKSPACE (IDE mode) — separate route/page
**Purpose:** Hands-on review of the agent's edits, runs, and output. Reached via the `Workspace →` button or any `Open diff` action; returns home via the top-bar **Stream** toggle.

**Layout:** full viewport (`height:100vh`, no body scroll). Sticky top bar, then a `322px 1fr` grid (conversation rail | workspace). The workspace column is a `1fr 232px` grid (editor area over output dock); the editor area is a `214px 1fr` grid (file tree | editor).

**Top bar:** "Hermes" + a **Stream ⇄ Workspace** mode toggle (Stream navigates home; Workspace active) + an **environment switcher** — 4 pills `sandbox · sb-9f2a` / `cluster:/scratch` / `files · sftp` / `local`; active = teal pill + teal dot, others = white + faint dot. + "watching Hermes work" (breathing) + avatar.

**Conversation rail (left, `paper`, scrolls):** continues the Stream conversation beside the code — a user bubble, an agent reply, and a **focus chip** (accent-soft box: "Editing analysis.py · lines 22–34" + the `wave` typing dots + "then re-running in sandbox sb-9f2a"). On Accept, an agent turn is appended ("Applied your accept — re-running…"). Composer docked at bottom (contenteditable field + teal send button).

**File tree (`raise`, scrolls):** mono header `sb-9f2a · /root/work`; rows with a mono glyph + filename; `analysis.py` is selected (accent-soft bg + 2px inset accent bar) with an `editing` badge; `volcano_validation.png` has a green `new` badge.

**Editor (`code-bg`, dark):**
- Tabs: `analysis.py` (active, accent underline) + `notebook.ipynb`.
- **Proposal banner** (state-dependent): *pending* = "Hermes proposes an edit — +9 −1, adds volcano()" with **Accept** (teal) / **Modify** / **Discuss** buttons; *applied* = green "You accepted this edit — re-running analysis.py in sb-9f2a" + a "View run →" button.
- **Diff body:** monospace, 46px line-number gutter (1px right border), syntax-highlighted tokens, add/del line backgrounds, a `+`/`-` prefix per changed line, and a blinking caret on the last added line.

**Output dock (`raise`):** tabs **Output** / **Terminal** (+ a right-aligned run-meta mono label, e.g. "cell [3] · 0.8s · sandbox").
- *Output* pane: a results table (Gene / log2FC / q — up = `crit`, down = `accent`, mono tabular) + a plot card (mono caption + striped `PNG` placeholder → real figure).
- *Terminal* pane: monospace run log (prompt `work ▸` in accent, `✓` in good, dim detail lines, blinking caret). On Accept, extra re-run lines append.

---

## Interactions & Behavior

### Navigation
- Home `Workspace →` button and the Stream **Needs you → REVIEW → Open diff** action both navigate to the Workspace route (open with `analysis.py` + its diff focused).
- Workspace top-bar **Stream** toggle navigates back to Home (lands on Stream).
- Home view switcher swaps Brief/Stream/Surface in place (no navigation).

### Needs-you items (shared across Stream / Brief / Surface)
Each item has a `kind`, title, detail, meta, a primary and a secondary action. Acting on an item **removes it from the queue**, decrements the count everywhere, and fires a confirmation **toast** (bottom-center, ink pill, auto-dismiss ~2.6s). The four seeded items:
| id | kind (color) | title | primary → toast | secondary → |
|---|---|---|---|---|
| n1 | APPROVAL (accent) | Run esm2_finetune on the cluster | Approve → "Approved — queued on the A100" | Decline → "Declined" |
| n2 | DECISION (warn) | Pick the resistance panel | 12-gene → "Locked in the 12-gene panel" | 18-gene → "Locked in the 18-gene panel" |
| n3 | REVIEW (accent) | Edit to analysis.py — +9 −1 | Accept → "Accepted the edit" | **Open diff → navigates to Workspace** |
| n4 | BLOCKED (crit) | Files server (SFTP) auth expired | Re-authenticate → "Re-authenticated — cohort pull resumed" (also flips SFTP env to synced) | Dismiss |
When the queue empties, show the "all caught up / queue cleared" state in each view.

### SFTP re-auth
The SFTP environment row/chip renders in an **error state** (crit dot/tint + a "Re-authenticate" affordance) until resolved; resolving (via the env row OR the n4 item) flips it to a **synced** state (good dot, "synced just now") and clears n4. Single source of truth — both entry points hit the same handler.

### Workspace diff actions
- **Accept** → banner flips to the green "applied/re-running" state, the Output dock auto-switches to **Terminal**, re-run lines append, and the conversation rail appends the agent's "applied" turn. Toast.
- **Modify** → toast "Opening the hunk to edit inline…" (stub for inline edit).
- **Discuss** → toast "Reply in the conversation to steer the edit" (focus composer).

### Live log (the "Right now" / "leading edge" box)
A dark box that **streams new monospace lines on a timer** (~2.8s), newest at bottom, capped at ~6 visible lines, each `HH:MM` + tinted text (dim / good / accent). Seeded with 3 lines, then cycles a pool of plausible analysis steps. **Isolate this in its own component with its own timer** so ticking it does not re-render the rest of the dashboard. Honor `prefers-reduced-motion` (no ticking) and a `live` flag (pausable).

### Overnight digest (Brief)
Toggle button shows/hides the overnight event log inside the hero card.

---

## State Management
Home (per session):
- `view`: `'stream' | 'brief' | 'surface'` (default `stream`).
- `needs`: array of pending action items (removal-driven; everything derives count/all-clear from it).
- `sftpFixed`: boolean (drives the SFTP env state + removes n4).
- `digestOpen`: boolean (Brief overnight log).
- `toast`: transient message string (auto-clears).
- Shared read-only data: `specialists` (fleet + status), `events` (overnight timeline), `findings` (deliverables), `projects` (active threads).

Workspace:
- `outTab`: `'out' | 'term'`.
- `env`: active environment id.
- `applied`: boolean (proposal accepted → re-run state).
- `toast`.

Live log component: own `lines` array + interval; props `live`.

## Data contracts / backend hooks (map to Hermes)
The prototype data is static. In production these surfaces subscribe to the agent's runtime:
- **Needs-you queue** = the command/edit **approval gate** + decisions the agent surfaces. Primary/secondary map to approve/deny/choose; n3 review ties to a `patch`/`write_file` hunk.
- **Specialists / fleet** = sub-agent roster + status (e.g. lit-searcher, code-writer, verifier, synthesizer).
- **Events / "while you were away"** = the lab-notebook / activity history since last seen ("since you left" digest is central).
- **Live log + focus chip** = the gateway/PTY event stream (start/progress/complete) for the active task.
- **Workspace files/tree/editor** = the terminal-backend + `file_tools` over the active environment; diffs = `patch`/`write_file` surfaced as hunks; Output = rendered artifacts (table + `.png`/`.ipynb`); Terminal = PTY stream.
- **Environments** = sandbox / SLURM cluster / SFTP / local; switching changes the file-access source. SFTP error state = real auth/credential failures.
- **Compute/cost + jobs** (SLURM/Modal/cron) feed the stat strip and the Surface night tape.

## Assets
No raster assets ship in this package. The only image is the volcano plot, shown as a **striped placeholder** — wire to the real rendered figure (`figures/volcano_validation.png`). Fonts: **Geist**, **Geist Mono**, **Newsreader** (Google Fonts / OFL). Icons used are simple text glyphs/Unicode (▾ ◦ ›_ ▦ ◆ ✎ ✓ ↑ →) — swap for the codebase's icon set.

## Files
- `Hermes Dashboard.dc.html` — Home surface: Brief / Stream / Surface views + top bar + toast + navigation. Contains the shared data model.
- `Hermes Workspace.dc.html` — Workspace (IDE) mode.
- `LiveLog.dc.html` — the isolated streaming-log component (own timer) embedded by the dashboard.

> These are prototype references. Recreate them in the target environment (likely `apps/desktop`: Electron + React + nanostores, Monaco + xterm) using its component library and design system. Reproduce the visual + behavioral spec above; do not port the `.dc.html` runtime.
