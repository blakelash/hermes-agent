# Hindsight `hermes` bank — tagging convention

One shared brain, sliced by tags rather than partitioned into separate banks. Tags are a
**soft, multi-dimensional** classifier: they let recall scope results and let us decide at
exit-time what travels vs. what stays — without forcing a premature work/personal split
(see [[shared-memory-hindsight]] and the independent-fellow portability discussion).

Format: namespaced `key:value`, lowercase, hyphenate multi-word values. Recall filters via
the `tags` + `tags_match` (any/all) args; mental models/directives accept `tags` too.

## The three namespaces

### `src:` — provenance (where the fact came from)
`src:telegram` · `src:claude-code` · `src:claude-desktop` · `src:gateway` ·
`src:claude-web` (future export) · `src:manual` (you/an agent deliberately saving)

Mostly auto-derivable. Where a surface supports default tags (Hindsight `retain_tags`),
stamp the `src:` there so provenance is automatic; otherwise include it in explicit
`retain` calls. (Auto-retain already records `session:` / `platform:` in metadata.)

### `owner:` — the exit / portability dimension
`owner:personal` — clearly yours (personal context, your own ideas/notes).
`owner:fh` — clearly FutureHouse-proprietary (internal data, FH projects under IP).
**Leave ambiguous facts untagged.** The boundary is fuzzy (independent fellowship), so we
only tag the unambiguous ends and negotiate the rest at exit, informed by your fellowship/
IP agreement. This makes "what's mine vs. FH's" a *slicing* exercise, not a scramble.

### `project:` — topic threading
`project:hermes` · `project:hindsight` · `project:<research-topic>`. Optional but useful
for pulling a coherent thread and for scoping mental models.

## Usage notes
- **Apply sparingly and consistently.** Three namespaces max; over-tagging = nobody tags.
- **Recall scoping example:** `recall(query, tags=["owner:personal"])` to pull only the
  clearly-yours slice; `tags=["project:hindsight"]` for one thread.
- **Exit slicing:** at offboarding, export by `owner:personal` (+ whatever your agreement
  covers) rather than the whole bank. The full `pg_dump` runbook is the physical backstop.
- **The one hard rule:** you must extract before Fly-org access ends — there is no
  retroactive pull (the org/app/volume are on the FH account).
