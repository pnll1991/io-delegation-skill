# I/O Delegation V1 — Context Gateway

## Product definition

I/O Delegation is a context gateway for coding agents. Its job is not to maximize delegation or model calls. It gives the main agent the smallest useful evidence and chooses the cheapest appropriate path to obtain it.

**Product promise:** give coding agents the right context, not the whole repository.

The user keeps working normally in Codex, Claude Code or Cursor. The gateway is exposed through MCP and should usually be invisible except for tool calls and diagnostics.

```text
coding agent
    |
 io_context
    |
 search | extract | query
                    |
              local rules / Jev
               /      |       \
        targeted   principal   semantic worker
```

Critical reasoning and edits stay in the main agent. Jev and semantic workers are optional accelerators.

## Real use cases

Core cases:

1. **Understand a large or unfamiliar repository.** Locate relevant sources, extract bounded evidence, then keep causal reasoning in the main agent.
2. **Audit many files.** HTML/JSON/config inventories should use deterministic extraction instead of making an LLM read every file.
3. **Compare behavior across files.** `query` may route selected evidence to a semantic worker when compact cross-file extraction is useful.
4. **Protect critical reasoning.** Security, architecture and debugging should normally route back to the principal agent with bounded evidence.
5. **Control context and observe routing.** Audit records expose routes, cache/model calls and errors without logging source contents.

Explicit bypass cases:

- `git status`, tests, compilers, parsers and exact counts
- simple literal search
- one known function or small range
- already-focused context

These should not pay for unnecessary Jev or worker calls.

## Public agent interface

The V1 MCP surface is intentionally small:

- `search`
- `extract`
- `query`

`semantic_query` remains for compatibility but is not part of the primary product story.

## User workflow

Install for the current project:

```bash
# Windows
io-delegation.cmd setup --project .

# macOS / Linux
./io-delegation setup --project .
```

By default setup:

- detects supported agents
- installs the self-contained skill
- registers `io_context` as a project MCP server
- auto-detects a safe source scope
- enables Jev only when `TYPESAFE_API_KEY` is already available
- leaves the semantic worker off unless an approved config is supplied
- installs the read guard in `observe` mode
- runs `doctor`

The API key value is never written to generated configuration. Agent configs reference only its environment-variable name.

Operational commands:

```bash
io-delegation status --project .
io-delegation doctor --project .
```

## V1 task board

Implemented:

- [x] Make `query` a core MCP tool even without external models
- [x] Keep `search` and `extract` deterministic and local
- [x] Route `query` with local fallback rules and optional Jev
- [x] Call Jev from the MCP/host process, not the agent shell
- [x] Keep semantic worker optional and selected-fragment only
- [x] Fall back safely when Jev or the worker is unavailable
- [x] Add one-command `setup`
- [x] Add `status` and real-MCP `doctor`
- [x] Register Codex, Claude Code and Cursor project MCP configs
- [x] Store machine-local state/audits/configs outside the project
- [x] Never persist the TypeSafe API-key value
- [x] Default read guard to `observe` in product setup
- [x] Add routing telemetry without source contents
- [x] Back up changed agent configuration
- [x] Refuse silent replacement of a modified installed skill

Validation remaining before calling V1 generally useful:

- [ ] [#5](https://github.com/pnll1991/io-delegation-skill/issues/5) Dogfood at least 20 real tasks across 3+ repositories
- [ ] Include large-repo, multi-file audit, debugging/security and small-task negative controls
- [ ] [#6](https://github.com/pnll1991/io-delegation-skill/issues/6) Repeat key A/B task families at least 5 times to characterize variance
- [ ] Measure selected bytes/context returned in addition to token totals
- [ ] Record false routing decisions and unnecessary worker calls

Distribution / UX follow-ups:

- [ ] Add explicit `setup --dry-run` preview
- [ ] Add uninstall / managed-config removal with backup restore guidance
- [ ] Decide packaging path (`pipx`, standalone binary, or signed installer)
- [ ] [#8](https://github.com/pnll1991/io-delegation-skill/issues/8) Test real Claude Code and Cursor authenticated sessions, not only MCP handshake
- [ ] Evaluate a Cursor plugin package after the cross-agent CLI is stable
- [ ] Add optional richer session report only if users need it; no dashboard-first work

## Product success gates

Before promoting this beyond beta:

- same or better functional result on the dogfood set
- measurable main-context reduction on large/multi-file tasks
- no meaningful penalty on small tasks because the gateway bypasses expensive routing
- zero critical misroutes that delegate security/debugging decisions away from the principal
- setup + doctor completes in under three minutes on a clean supported project
- no credential values persisted by setup or telemetry

If those gates fail, reduce scope rather than adding more routing complexity.
