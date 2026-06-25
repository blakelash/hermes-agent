# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Read AGENTS.md first

`AGENTS.md` (~1400 lines) is the canonical, detailed development guide for this repo:
the contribution rubric, the "Footprint Ladder" for new capabilities, prompt-caching
rules, plugin/skill/toolset authoring, profiles, cron, kanban, curator, and a long list
of known pitfalls. **Read it before any non-trivial change.** This file is the quick
orientation layer; AGENTS.md is the source of truth when the two overlap.

## What Hermes is

A personal AI agent that runs the **same agent core** across a CLI, a messaging gateway
(~20 platforms: Telegram, Discord, Slack, …), an Ink TUI, and an Electron desktop app.
It learns across sessions (memory + skills), delegates to subagents, runs scheduled
cron jobs, and drives a real terminal/browser. Capability is extended primarily through
**plugins and skills**, not by growing the core. Mixed Python (agent core + CLI +
gateway) and TypeScript (TUI, desktop, web dashboard, docs site).

## Two invariants that govern almost every change

1. **Per-conversation prompt caching is sacred.** A long-lived conversation reuses a
   cached prefix every turn. Do NOT mutate past context, swap toolsets, or rebuild the
   system prompt mid-conversation — it invalidates the cache and multiplies user cost.
   The *only* sanctioned exception is context compression. Cache-affecting slash
   commands default to deferred invalidation (next session) with an opt-in `--now`.
2. **The core is a narrow waist; capability lives at the edges.** Every model tool ships
   on every API call, so a new *core tool* is the last resort. Prefer, in order: extend
   existing code → CLI command + skill → service-gated tool (`check_fn`) → plugin → MCP
   server in the catalog → new core tool. The product is expansive at the edges
   (platforms, providers, skills) and conservative at the waist (core agent + tool schema).

Also preserve: strict message-role alternation (never two same-role messages in a row;
no synthetic user message injected mid-loop) and a byte-stable system prompt for the
life of a conversation.

## Commands

```bash
# Setup (Python 3.11, <3.14)
uv venv .venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[all,dev]"

# Tests — ALWAYS use the wrapper, never bare `pytest` (it enforces CI parity:
# unset credentials, TZ=UTC, LANG=C.UTF-8, per-test subprocess isolation)
scripts/run_tests.sh                                   # full suite
scripts/run_tests.sh tests/gateway/                    # one directory
scripts/run_tests.sh tests/agent/test_foo.py::test_x   # one test
scripts/run_tests.sh -- -v --tb=long                   # pass-through pytest flags
scripts/run_tests.sh --no-isolate tests/foo/           # faster, for interactive debugging

# Lint / typecheck (matches .github/workflows/lint.yml)
ruff check .          # BLOCKING in CI — only PLW1514 (unspecified-encoding) is enforced
ty check              # type diagnostics (advisory diff in CI)

# Dependency changes: after editing pins in pyproject.toml, regenerate the lockfile
uv lock

# TypeScript (TUI)
cd ui-tui && npm install && npm run dev      # watch; also: build / typecheck / lint / test (vitest)
```

Integration tests are excluded by default (`addopts = -m 'not integration'`).

## Architecture: the big picture

**Import/dependency chain (Python core):**
```
tools/registry.py            # no deps; imported by every tool file
   ↑  tools/*.py             # each calls registry.register() at import time (auto-discovered)
   ↑  model_tools.py         # tool orchestration + triggers tool discovery + plugin discovery
   ↑  run_agent.py, cli.py, batch_runner.py, tools/environments/
```

**Key entry points you'll actually edit (the tree shifts; the filesystem is canonical):**
- `run_agent.py` — `AIAgent` class, the synchronous conversation loop (`run_conversation()`),
  tool dispatch, budget/interrupt handling. (~12k LOC)
- `cli.py` — `HermesCLI`, interactive CLI orchestrator (prompt_toolkit + rich). (~11k LOC)
- `model_tools.py` — `discover_builtin_tools()`, `handle_function_call()`, `get_tool_definitions()`.
- `toolsets.py` — `TOOLSETS` dict + `_HERMES_CORE_TOOLS` (default bundle most platforms inherit).
- `hermes_state.py` — `SessionDB`, the SQLite session store with FTS5 search.
- `hermes_constants.py` — `get_hermes_home()` / `display_hermes_home()` (profile-aware paths).
- `agent/` — extracted internals: `prompt_builder.py`, `context_compressor.py`,
  `auxiliary_client.py`, `memory_manager.py` + `memory_provider.py`, `curator.py`, `display.py`.
- `hermes_cli/` — CLI subcommands, setup wizard, `commands.py` (central slash-command registry),
  `config.py` (`DEFAULT_CONFIG`, `OPTIONAL_ENV_VARS`), `plugins.py`, `skin_engine.py`.
- `gateway/` — `run.py` + `session.py` + `platforms/<platform>.py` (one adapter per platform).
- `tools/environments/` — terminal backends: local, docker, ssh, singularity, modal, daytona.
- `cron/`, `plugins/`, `skills/` + `optional-skills/`, `ui-tui/` + `tui_gateway/`, `apps/desktop/`.

**Agent loop (run_agent.py):** synchronous `while` over `client.chat.completions.create(...)`,
appending tool-result messages each iteration until the model returns content with no tool calls.
Messages are OpenAI format (`system`/`user`/`assistant`/`tool`); reasoning lives in
`assistant_msg["reasoning"]`.

## Conventions that bite if you miss them

- **Never hardcode `~/.hermes`.** Use `get_hermes_home()` for state paths and
  `display_hermes_home()` for user-facing strings — both from `hermes_constants`. Hardcoding
  breaks the profiles feature (each profile has its own `HERMES_HOME`).
- **`.env` is for secrets only** (API keys, tokens, passwords → `OPTIONAL_ENV_VARS`). All
  behavioral config (timeouts, thresholds, flags, display prefs) goes in `config.yaml` via
  `DEFAULT_CONFIG`. PRs telling users to "set `HERMES_*` in .env" for non-secrets are rejected.
- **Adding a core tool = 2 files:** create `tools/<name>.py` with a `registry.register(...)`
  call (handlers MUST return a JSON string), then add the tool name to a toolset in
  `toolsets.py` (auto-discovery imports it, but it's only exposed if listed in a toolset).
  For local/custom tools, use the plugin route instead — never edit core.
- **Adding a slash command:** add a `CommandDef` to `COMMAND_REGISTRY` in
  `hermes_cli/commands.py` (CLI/gateway/Telegram/Slack/autocomplete all derive from it),
  then a handler in `cli.py`'s `process_command()` and, if gateway-available, in `gateway/run.py`.
- **Plugins must not modify core files.** If a plugin needs something core doesn't expose,
  widen the generic plugin surface (new hook / ctx method) — don't special-case it in core.
  In-tree `plugins/memory/` is closed to new providers (ship them as standalone plugin repos).
- **Tests:** don't write change-detector tests (snapshots of model catalogs, config version
  literals, enumeration counts) — assert invariants/relationships instead. Tests never write
  to `~/.hermes/` (the `_isolate_hermes_home` autouse fixture redirects it).
- **Dependency pins:** every dep needs an upper bound (supply-chain hardening). Provider/
  backend-specific deps are lazy-installed via `tools/lazy_deps.py`, not added to `[all]`.
- **Cross-platform:** Windows, macOS, and Linux are all first-class. Avoid POSIX-only idioms
  (`os.kill(pid, 0)`, `os.killpg`); prefer `psutil`. Always pass explicit `encoding=` to file I/O
  (enforced by ruff PLW1514).

## Memory: hindsight-hermes (work context)

If the `hindsight-hermes` MCP (Blake's shared agent memory) is connected, treat Claude Code in
this repo as a **work context** against the shared Hindsight bank:
- Recall/reflect with `tag_groups: [{"not": {"tags": ["owner:personal"]}}]` so personal facts
  (fitness, travel, relationships, finances, career/job-search) never surface.
- Retain durable **work** facts only; tag `src:claude-code` + `project:<slug>`; never tag work
  facts `owner:personal`, and don't retain personal facts from this context.
- Full policy + rationale: `deploy/hindsight/work-fleet-memory-policy.md`;
  tag namespaces: `deploy/hindsight/tagging-convention.md`.
