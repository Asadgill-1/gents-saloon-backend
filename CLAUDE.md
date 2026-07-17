# Rules for any AI working in this repo

Read [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) first. Then follow these rules for every change.

## Skills to use (if installed)

This repo's workflow assumes these skills. Invoke them at the stated moments. **Skills are helpers, not gates** — if one isn't installed, the fallback below is complete and binding; never skip a task because a skill is missing.

| Skill | Invoke when | What it does | Fallback if not installed |
|---|---|---|---|
| `/ponytail` | Start of ANY coding task (write, add, refactor, fix, review, choose dependency) | Enforces the lazy-senior sizing ladder: YAGNI → reuse repo code → stdlib → platform feature → installed dep → one line → minimum code | "Sizing ladder" section below — same rules, follow manually |
| `/karpathy-guidelines` | Before writing or reviewing any code | Anti-LLM-mistake rules: understand first, state assumptions, surgical edits, verifiable success criteria | "Before writing code", "Surgical edits", "Verify" sections below |
| `/ponytail-debt` | End of every phase + before any release/deploy (phase docs' "Ponytail ledger" / completion notes expect this) | Harvests every `# ponytail:` comment in the repo into a debt ledger report so shortcuts don't rot | `grep -rn "ponytail:" backend/ frontend/` → list every hit (file:line, what's cut, upgrade path) in the phase completion note |
| `/ui-ux-pro-max`, `/frontend-design`, `/design` | Start of Phase 2 and Phase 3 only | Design intelligence refinement | Tokens/wireframes/specs already embedded in [docs/DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) — locked, sufficient alone |

Usage pattern per phase: `/ponytail` + `/karpathy-guidelines` at task start → build with rules below → `/ponytail-debt` at phase end, paste ledger into completion note.

## Sizing ladder — stop at first rung that holds

1. Does this need to exist? Speculative → skip, say so in one line. (YAGNI)
2. Already in this repo? Reuse existing helper/util/pattern instead of rewriting.
3. Stdlib/Python builtin does it? Use it.
4. Native platform/framework feature covers it? (Supabase RLS over app-level auth checks, DB constraint over app code, React built-ins over a lib.)
5. Already-installed dependency solves it? Use it. Never add a new dep for what a few lines do.
6. Can it be one line? One line.
7. Only then: minimum code that works.

Two rungs work → take the higher one, move on.

## While coding

- No unrequested abstractions: no interface with one impl, no factory for one product, no config for a value that never changes.
- No features beyond what was asked. No speculative flexibility.
- No boilerplate/scaffolding "for later."
- No error handling for impossible scenarios — but always handle real failures (bad Supabase response, Celery task failure, Telegram API down, etc).
- Deletion over addition. Boring over clever.
- Fewest files, shortest working diff — but only after understanding the change.
- Mark deliberate shortcuts inline as `# ponytail: <what's cut> — <upgrade path>` (e.g. `# ponytail: no retry on telegram send, add exponential backoff if delivery becomes unreliable`). These get collected later via the ponytail-debt process below — don't skip the comment.

## Before writing code

- Read the task AND the code it touches. Trace real flow end to end first.
- State assumptions explicitly. Multiple valid interpretations → present them, don't silently pick one.
- Simpler approach exists → say so, push back if warranted.
- Unclear requirement → stop and ask, don't guess and build.

## Surgical edits

- Touch only what the request requires. Don't refactor, reformat, or "improve" adjacent code.
- Match existing style even if you'd do it differently.
- Notice unrelated dead code → mention it, don't delete it.
- Your change orphaned an import/var/function → remove it. Leave pre-existing dead code alone.

## Bug fixes

- Root cause, not symptom. Before editing, grep every caller of the function being touched.
- Fix once in the shared function — not patched separately in every caller.

## Verify

- Turn every task into something checkable: "add validation" → test invalid inputs; "fix bug" → write a failing test first, then make it pass.
- Non-trivial logic (branch, loop, parser, money/booking/auth path) leaves one runnable check behind — assert-based self-check or one small test file. No test frameworks/fixtures unless asked.
- Multi-step task → state a short plan with a verify step per stage before starting.

## Never simplify away

- Input validation at trust boundaries (anything coming from frontend, Telegram, or webhook).
- Error handling that prevents data loss (booking/payment/queue state).
- Auth/security checks (Supabase RLS, API auth).
- Anything the owner explicitly asked for — build it in full, no re-arguing scope.

## Ponytail debt

Shortcuts marked with `# ponytail:` comments are debt, not forgotten work. When asked to review debt (or via `/ponytail-debt`), collect every `ponytail:` comment in the repo into a ledger instead of leaving them to rot.

## Communicating the work

- Code first. Then at most ~3 lines: what was skipped, when to add it.
- No essays defending a simplification — if the explanation is longer than the code, cut the explanation.
- Report/walkthrough explicitly requested by the owner → give it in full, that's not the same as unrequested prose.

## Status tracking

Nothing is built yet — check [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) for what's actually been asked before assuming scope. Append new decisions to [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) as they happen.
