# I/O Delegation V1.2 — Context Gateway

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
        targeted   principal   cheap-first T1
```

Critical reasoning and edits stay in the main agent. The route-selector Jev remains optional. When an approved worker exists, compute orchestration is automatic but conservative: local rules keep obvious T0/T2 work out of Jev, and only a bulk factual candidate can receive one validated T1 cheap-worker attempt.

## Real use cases

Core cases:

1. **Understand a large or unfamiliar repository.** Locate relevant sources, extract bounded evidence, then keep causal reasoning in the main agent.
2. **Audit many files.** HTML/JSON/config inventories should use deterministic extraction instead of making an LLM read every file.
3. **Compare behavior across files.** `query` returns bounded selected evidence; with an approved worker, cheap-first orchestration may answer a bounded factual comparison in T1 and escalates failures to the principal.
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

Semantic inference is internal to `query`; no separate semantic MCP tool is exposed in V1.

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
- registers one machine-local/global `io_context` MCP per host and resolves the active project at runtime
- auto-detects a safe source scope
- keeps the experimental Jev route selector off by default; explicit `--jev on`, `--jev auto`, or a reviewed router config is required
- when an approved worker is configured, enables Jev cheap-first compute orchestration in `auto` mode; `--orchestration off` is the persistent escape hatch
- keeps the older unconditional `context_auto_dispatch` path off unless explicitly enabled
- leaves the read guard off unless explicitly requested
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
- [x] Keep semantic workers selected-fragment only; leave unconditional auto-dispatch off and add separately gated T0/T1/T2 cheap-first orchestration
- [x] Fall back safely when Jev or the worker is unavailable
- [x] Add one-command `setup`
- [x] Add `status` and real-MCP `doctor`
- [x] Register portable/global MCP entries: Codex user config, Cursor global config resolved from workspace cwd, Claude user scope
- [x] Store machine-local state/audits/configs outside the project
- [x] Never persist the TypeSafe API-key value
- [x] Keep read guard off by default; tracked hook configs require explicit override
- [x] Add routing telemetry without source contents
- [x] Back up changed agent configuration
- [x] Refuse silent replacement of a modified installed skill
- [x] Install a stable runtime under `~/.io-delegation/` so the distributor clone can move/disappear
- [x] Give projects stable IDs that survive directory moves
- [x] Refresh the registered Python executable on setup reruns
- [x] Share one global MCP across multiple configured projects and remove it only after the last project
- [x] Validate the global bootstrap in a real Codex session (`io_context.extract` -> `42`)

Worker validation result (#15): the isolated 25-pair sample found no material principal-token compression, about +97% median principal+worker token overhead, roughly +16.3 s median wall-time overhead, 2/25 accepted worker responses, and one clean quality regression. V1 therefore keeps **unconditional** semantic-worker auto-dispatch out of the default path. The new cheap-first path is separately gated by Jev + deterministic risk thresholds + literal-evidence validation and requires its own paired cost evidence before claiming savings.

Jev validation result (#14): the repeated 20-pair isolated sample completed with 11/20 successes in each arm. Jev was actually called in 9 pairs. Among those called pairs there were no clean quality regressions and one quality gain; the only arm-level regression occurred in a pair where Jev was not called. The Jev arm still produced 7 effective-route mismatches overall, and the multi-file family never reached Jev. V1 therefore does not auto-enable Jev from `TYPESAFE_API_KEY`; Jev remains explicit opt-in while the local deterministic gateway is the default path. The machine-readable evidence is `benchmarks/v1-validation/evidence/jev-ab-20260918.json`.

Validation remaining before calling V1 generally useful:

- [ ] [#12](https://github.com/pnll1991/io-delegation-skill/issues/12) Complete the official 20-task / 40-run dogfood sample across 3 pinned repositories
- [x] Include large-repo, multi-file audit, debugging/security and small-task negative controls in the frozen dogfood manifest
- [x] [#14](https://github.com/pnll1991/io-delegation-skill/issues/14) Repeat the Jev A/B families 5 times and preserve all negative runs
- [x] Measure selected bytes/context returned separately from principal, router and worker token domains
- [x] Record routing mismatches, router calls and unnecessary worker calls
- [x] Add T0/T1/T2 compute telemetry, escalation accounting and a fail-closed orchestration A/B evaluator
- [ ] Collect at least 20 paired quality-passing real-host orchestration cases with complete principal + worker + Jev accounting

Distribution / UX follow-ups:

- [x] Add explicit `setup --dry-run` preview with zero writes
- [x] Add surgical `remove`, config snapshots, `backups` and guarded `restore`
- [x] Keep the cross-agent repo CLI for beta; defer package distribution until dogfood proves utility
- [ ] [#18](https://github.com/pnll1991/io-delegation-skill/issues/18) Complete authenticated Claude Code and Cursor parity/lifecycle, not only MCP handshake
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
