# Agent Notes — shared state for AI agents working on tf2-demo-viewer

This directory is the **low-cost coordination layer** between agents. Before
starting work on a task, read `STATUS.md` and any note tagged with your task
area. After finishing work, **append or update a note here** (see rules below)
so the next agent does not have to re-read the codebase.

## Why this exists

Context is expensive. A fresh agent asked to "fix the timeline bug" should not
re-explore 4000 lines of `main.ts`. It should read `STATUS.md` (2 min), find the
note tagged `#timeline` if one exists, do the job, and write back what changed.

## Directory contract

| File | Purpose |
|---|---|
| `STATUS.md` | **Living document.** Current state of the project, what works, what is broken, what is in flight. Updated after every meaningful change. |
| `NN-topic.md` | Focused notes on a specific area or investigation (e.g. `10-replay-format.md`, `20-gltf-pipeline.md`). Numbered for stable sorting. |
| `decisions.md` | Append-only log of non-obvious decisions and their reasons. Never delete entries. |

## Strict rules for writing notes

1. **Format is mandatory.** Every note file starts with this header:

```markdown
# <Topic>

- Date: YYYY-MM-DD
- Author: <agent id or "human">
- Scope: <files/dirs this note covers, e.g. `web/src/replay.ts`, `preprocess/`>
- Tags: <#replay #gltf #camera ...>

## State
<2-5 sentences: what is true NOW. No history.>

## Gotchas
<Non-obvious facts an agent would waste time rediscovering. Bullet list.>

## Open questions
<What is unknown or undecided. Delete items when resolved.>
```

2. **`STATUS.md` is always current.** If you change behavior, you update
   `STATUS.md` in the same commit. If you discover something is broken that
   STATUS claims works, fix STATUS first, then the bug.
3. **No duplication.** Binary format details live in `../PLAN.md`; project rules
   live in `../AGENTS.md`. Notes reference them, never copy them. If the format
   changes, PLAN.md changes — a note only says "format bumped to v3, see PLAN.md".
4. **Notes are disposable.** When a topic is fully resolved and its facts have
   moved into code comments / PLAN.md / STATUS.md, delete the note file. Stale
   notes are worse than no notes.
5. **Facts, not plans.** Notes describe what IS, not what should be. Roadmap
   items go to `../TODO.txt`, not here.
6. **Every claim is checkable.** Write the exact command that verifies a claim
   (`python3 validate.py ... → OK`). Agents in the future must be able to
   re-verify without trusting you.
7. **English only** in this directory (project-wide rule from AGENTS.md).
8. **Appending to `decisions.md`:** one line per decision:
   `YYYY-MM-DD | <decision> | <why, 1 sentence> | <alternative rejected>`.

## What goes where — quick reference

- "How do I regenerate the GLB?" → `../AGENTS.md` (recipes section)
- "What is the replay.bin layout?" → `../PLAN.md`
- "Why does the dumper emit frames for skipped ticks?" → `10-replay-format.md`
- "Why are props boxes?" → `STATUS.md` + `../TODO.txt`
- "What is currently in flight / broken?" → `STATUS.md`
- "How should I format my note?" → this file
