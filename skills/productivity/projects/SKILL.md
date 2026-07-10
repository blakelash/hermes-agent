---
name: projects
description: "Organize sessions into projects: shared persistent workdirs, binding, artifacts."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [projects, workspace, organization, sessions, volumes, modal, persistence]
    related_skills: [hermes-agent]
---

# Projects

A **project** is a named workspace that groups related sessions and gives them
a shared, persistent working directory. When a chat is bound to a project,
your terminal and file tools operate inside the project's per-session
directory, your work survives environment teardown, and everything you (and
sibling sessions) produce is organized under one browsable root — instead of
scattering across throwaway sandboxes.

Use projects for **named, ongoing workstreams** the user intends to return to
("the RNA analysis", "the Q3 report", "the fusion sim sweep"). Do NOT create
projects for one-off questions or short factual tasks.

## How to bind (agent side)

You have a `project_bind` tool in gateway sessions:

| Call | Effect |
|---|---|
| `project_bind(action="status")` | Current binding + working directory |
| `project_bind(action="list")` | All registered projects (name + slug) |
| `project_bind(action="bind", name="rna")` | Attach this conversation to an existing project (fuzzy name match) |
| `project_bind(action="create", name="RNA Analysis")` | Create a project, then bind |

Binding is **immediate and does not reset the conversation**: the tool result
returns your new `workdir` — do all further work there. The binding is also
**sticky for the chat**: future sessions in this chat start in the same
project automatically (if the result carries `"sticky": false`, warn the user
the binding applies to the current session only).

When the user starts a distinct workstream ("let's work on the protein
folding project"), check `list` first and `bind` to an existing project
before creating a new one — `create` always mints a new project (a name
collision gets a `-2` slug, it does not merge).

Users can do the same from their side: `/project list|set|new|clear` in any
chat, the desktop Dashboard's "Active projects" card, the sidebar's "+"
button, and "Move to project" on any session row. `/project set` on an
active conversation starts a fresh session in the project; your tool bind
never does.

## Where the work lives

- **Remote terminal backends (Modal, docker, ssh):** your working directory
  is `<volume_root>/<project-slug>/<session-id>` inside the environment —
  on Modal that's the persistent volume (default `/work`), so files survive
  sandbox teardown and are shared across the project's sessions.
- **Local backend:** a host directory `<hermes-home>/projects/<slug>/<session-id>`.

Conventions that matter:

- **Keep this session's files under your workdir.** The project root
  (`/work/<slug>/`) is the shared boundary — other sessions' directories sit
  beside yours.
- **Look before you redo:** `ls /work/<slug>/` shows prior sessions' work.
  Check for existing datasets, checkpoints, or results before regenerating
  anything expensive.
- **`cd` does not persist between terminal calls** in a bound session (each
  command starts back in your workdir). Use absolute paths, or chain steps
  in one command: `cd sub && make`.
- **Delegated subagents inherit automatically** — each child works in its own
  subdirectory of your workdir, so parallel children can't clobber each
  other. Collect their outputs from those subdirs.

## Getting artifacts to the user

Emit `MEDIA:<absolute path>` for a file the user should receive (figure,
CSV, PDF), exactly as usual. For remote backends the gateway automatically
fetches the file out of the environment and delivers it to the chat — up to
the configured cap (`terminal.media_fetch_max_mb`, default 25 MiB). For
larger artifacts, leave them in the project and tell the user the path
(e.g. `/work/rna/sess123/alignment.bam`) — the volume is browsable via
`modal volume ls` and the desktop workspace.

## Switching and unbinding

- Rebinding mid-conversation with `project_bind` moves your working directory
  immediately; mention it to the user so there's no surprise about where new
  files land.
- `/project clear` (user command) unbinds the chat; new sessions start
  unassigned.
- Renames change a project's display name only — the slug and directories
  are stable, so paths in notes and manifests stay valid.
