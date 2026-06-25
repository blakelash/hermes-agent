# Work-fleet memory policy: exclude personal facts

The shared `hermes` Hindsight bank holds both work and personal facts. Personal facts are
tagged `owner:personal` (see `tagging-convention.md`). **Work agents must not surface Blake's
personal life; personal surfaces keep full access.** This is a per-context read filter, not a
deletion — the data stays in one bank.

## Mechanism (verified on server 0.6.1)

Hindsight `recall` and `reflect` support `tag_groups` — a boolean AND/OR/NOT tag filter. To
exclude all personal facts from a work-context recall/reflect, pass:

```json
"tag_groups": [{"not": {"tags": ["owner:personal"], "match": "any"}}]
```

Verified live: a recall that returned condo/mortgage, relationship status, job-search, the
Edison signing, marathon training, and travel facts returned **zero** of them once this filter
was applied (work facts only).

## Who applies it

- **Apply (work context):** the worker fleet (researcher / coder profiles) and any work-mode
  tool (e.g. Claude Code in the hermes repo). Default recall/reflect to the filter above; retain
  only work facts (`src:<surface>` + `project:<slug>`, never `owner:personal`).
- **Do NOT apply (personal context):** the Telegram DM brain and Blake's personal Claude
  Desktop — these read the full bank (it's him).

## Caveats

- Exclusion is only as complete as the tagging. All **sensitive** personal facts are
  `owner:personal` (job search, Edison, finances, relationship status), so they are reliably
  excluded. A few **non-sensitive** stragglers are untagged and may leak (Tampa hometown, `iad`
  region preference) — acceptable.
- 0.6.1 MCP has no `update_memory` / single-`delete_memory` (only append, `delete_document`,
  `clear_memories`), so existing memories cannot be re-tagged in place. Fully tagging the
  stragglers would need a DB-level update on the box — not worth it for non-sensitive items.

## Work-agent memory instruction (paste into fleet system prompts / work-mode CLAUDE.md)

```
Shared long-term memory via the "hindsight-hermes" tools. This is a WORK context:
• On every recall and reflect, pass: tag_groups: [{"not": {"tags": ["owner:personal"]}}]
  so personal facts (fitness, travel, relationships, finances, career/job-search) never surface.
• Ground via the work-scoped "active-research" mental model (list_mental_models to find it).
• Before answering about projects, decisions, or prior work, recall (with the filter) first.
• Retain durable WORK facts only; tag src:<surface> and project:<slug>. NEVER tag work facts
  owner:personal, and do not retain personal facts from this context.
```

The **personal** surfaces use the unfiltered block in `connect-claude-code.md` instead.

## Mental models

- `active-research` — **work-scoped**: its source_query strictly excludes personal life, so it's
  safe for the fleet to read.
- `profile-blake` — full personal profile (includes personal context); personal surfaces only.

See `tagging-convention.md` for the tag namespaces and exit/portability rationale.
