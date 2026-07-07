# Design handoff — Hermes scientific co-collaborator UI

## What this is
A UI for a personal **scientific co-collaborator** agent (built on Hermes). It's an always-on
research partner: you chat with it, it searches literature, runs analyses on cloud sandboxes /
HPC clusters, tracks its work as a lab notebook, runs a fleet of specialist sub-agents, and
remembers you across sessions. Audience: working scientists (bio / drug-discovery flavored).

## Prototypes produced (clickable, self-contained HTML)
1. **v1 cockpit** (rejected — too busy; keep as the *admin console* blueprint)
   `scratchpad/cocollaborator-cockpit.html` → was the 3-rail mission-control.
2. **v2 calm chat — THE MAIN SURFACE** (approved)
   URL: https://claude.ai/code/artifact/01266422-3f37-4058-85bc-ec2477372537
   One centered conversation column; the agent's work folds into a single quiet "✓ Looked into it"
   disclosure; all machinery hidden behind a "Show what's running" **admin drawer**.
3. **v3 Workspace (IDE mode)** (approved direction)
   URL: https://claude.ai/code/artifact/1ae176c8-f4ca-4c5d-b480-1b73cd7b4184
   Chat rail + file tree + editor with **live agent diff (Accept/Modify/Discuss)** + Output/Terminal
   panes + an **environment switcher** (sandbox / cluster / sftp / local).

## Product model (the spine of the design)
- **Calm by default, depth on demand.** Default = conversation. Everything dense is opt-in.
- **Two modes, one app:** `Chat` (home) ⇄ `Workspace` (IDE drop-in). Conversation persists as a
  rail inside Workspace (Cursor pattern) so it always feels like a collaborator.
- **Three tiers of disclosure:** (1) chat + one-line work receipt → (2) admin drawer / Workspace →
  (3) full admin console (= v1).
- **Mobile / Telegram = chat-only.** IDE/admin live on desktop.

## Design system (tokens — keep consistent)
- Color: `--paper #FBFBFC`, `--ink #1E2230`, `--soft #6A7180`, `--line #ECEEF2`,
  one accent `--accent #5765FF` (spend only on active/working state). Clinical near-white —
  deliberately NOT the AI-cream cliché. Semantic kept separate: good #2E9E66, warn #B9802C, crit #D8453F.
- Type triad: **serif display** (Iowan/Palatino/Georgia) for collaborator name + headings;
  **system-sans** body; **monospace** for data/code/labels/readouts. (CSP blocks webfonts → system stacks only;
  if the designer wants a custom face, inline it as a `@font-face` data URI.)
- Motion: minimal, gated by `prefers-reduced-motion`. Calm > flashy.

## Feature surfaces → where they live
| Capability | Surface |
|---|---|
| Talk to it (answers, not dumps) | Chat (v2) |
| Show-your-work (Route A lab notebook) | One-line receipt in chat → expand; full notebook in Workspace |
| File access (sandbox / SLURM cluster / SFTP / local) | Workspace env switcher + file tree |
| Watch the agent work | Workspace: focus chip, live diff, Output/Terminal panes |
| Modify files / review edits | Workspace editor: reviewable diffs + shared filesystem |
| Specialist fleet, jobs (SLURM/Modal/cron), model chain, memory, security | Admin drawer / full console |
| Memory ("what I know about you", Honcho, shared local⇄remote) | Drawer; subtle, not a panel |

## Architecture hooks (so design stays buildable)
- Files/editor ride Hermes's terminal-backend + `file_tools` (already route through the active env).
- Live viz needs the gateway/`tui_gateway` event + PTY stream (start/progress/complete) → panes subscribe.
- Diffs = `patch`/`write_file` surfaced as hunks; Accept/Reject ties into the command/edit **approval** gate.
- Notebook output = the `.ipynb` artifact rendered.
- Likely build target: **`apps/desktop`** (Electron + React + nanostores) + Monaco editor + xterm.
- Backend contract for a standalone build: OpenAI-compatible API (`:8642`) + kanban/dashboard APIs via `fly proxy`.

## Open questions for the designer
1. Editor: **full co-author** (Monaco, scientist types too) vs **review-only** (agent types, human accepts diffs)?
2. Is Workspace a **mode in the main app**, or does it justify being the **desktop app** while phone stays chat-only?
3. Admin: inline **drawer** vs a genuinely **separate console** app? (Both stubbed.)
4. How "live" should viz be — full streaming editor/terminal, or periodic snapshots?

## Constraints
Calm-by-default is a hard requirement (user rejected the dense v1). Self-contained (CSP: no external
fonts/scripts). Security posture matters: code runs in sandboxes, edits/shell are approval-gated — the
diff-review UI is also the security surface.
