# Context MCP / Gateway V1.2

The service prepares evidence before inference. It never edits project source, changes permissions, starts an arbitrary command from a tool argument, or chooses a provider.

## Local runtime

The product CLI installs a stable `scripts/context_bootstrap.py` runtime under `~/.io-delegation/` and registers one host-level `io_context` server. The bootstrap resolves the current configured project from `.io-delegation/project.json`, loads its private state, and then starts `ContextService`. Outside configured projects it advertises zero tools. The lower-level `scripts/context_mcp.py --root ...` interface remains available for tests and manual integration.

`search`, `extract` and `query` are always the complete public MCP surface. Without `--config`, no worker is available and no worker login/model is needed. `search` takes `paths` and an optional `needle`; omitting it lists the scoped paths. Only *, ? and ** path globs are accepted, within the operator allowlist. Excerpts and output are bounded. `extract` takes `paths` and one `projection`, or up to eight distinct `projections` sharing the same snapshots.

Projections:
- `{"kind":"html","fields":["title","h1","canonical","description"]}`: first static source occurrence; BR is a space, entities decode once. Missing and duplicates are declared. This does not render JavaScript or calculate CSS visibility.
- `{"kind":"json","pointers":["/a/0","/escaped~1key"]}`: RFC 6901 paths; null is different from missing; duplicate JSON keys are rejected.
- `{"kind":"lines","start":1,"end":10}`: inclusive, one-based lines.
- `{"kind":"span","start":0,"end":120}`: half-open Unicode character offsets after UTF-8 BOM removal and CRLF/CR normalization to LF. NOT raw byte offsets.

Large minified lines must use bounded search/spans, not whole-line output. Oversized results are explicitly rejected, never silently truncated and accepted.

## Smart `query` tool

V1 exposes `query` as the primary semantic context interface. It accepts explicit selected fragments plus a question and operation hint. It always works: without external services it applies local routing rules and returns bounded evidence. With an approved TypeSafe router config, Jev can score ambiguous route selection. Separately, when setup has an approved worker, the generated compute orchestrator can score whether a bulk factual candidate is safe for one cheap T1 attempt. Both Jev paths receive task text plus aggregate file metadata only.

A `principal` route returns bounded evidence for reasoning in the main agent. `targeted_read` returns selected fragments without another model. T1 results must pass literal-evidence validation and contain no unknowns; otherwise `query` escalates to T2/principal. Router errors fall back to local route rules; compute-scorer errors fail closed to T2/principal. Semantic inference remains internal to `query`; callers cannot bypass policy with a separate semantic MCP tool.

## Optional semantic worker

An already reviewed worker config outside the project makes the internal semantic worker available. Normal product setup defaults compute orchestration to `auto` when such a worker is present: only a bulk factual candidate can reach the Jev compute scorer, and only deterministic low-risk/sufficiency gates can authorize one T1 attempt. The older unconditional semantic path is still available for experiments and still requires `"context_auto_dispatch": true`. See [ORCHESTRATION.md](ORCHESTRATION.md).

`query` accepts explicit `selections` of `{path, select}`, plus `question` or up to four related `questions`. Selectors support lines/span, literal windows and Python symbols. Qualified Python methods retain the containing class; imports and module bindings are included. This is not whole-program dependency resolution. Other languages use explicit ranges/windows rather than a pretend AST parser.

The worker receives only selected fragments, with stable local references, scope and omissions. Evidence is checked against those fragments AND unchanged original files. It cannot quote unseen content merely because it exists elsewhere in a file. Missing selections abstain before model dispatch.

## Minimal backend vs CLI

The existing `chat-completions` adapter is the minimal backend: supplied system/corpus messages and an output limit, not a nested agent. Configure it only with an endpoint/model/credentials explicitly approved by the operator. Local loopback is supported; remote destinations require HTTPS plus `allow_remote: true`. Existing `codex-cli` remains available with its normal login and read-only worker. No login tokens are copied into an API request.

Run `benchmarks/context/microbench.py --help` for a single-operation comparison across reviewed configurations. Without `--live` it makes ZERO model calls. Explicit request bytes and selected source bytes are diagnostics, not token estimates. Do not replace Codex base instructions or disable the sandbox to lower an apparent token count.

The legacy full-file MCP and old benchmark remain separate compatibility controls. Their boundary receipts are not evidence that v0.4 is already authenticated and measured on Windows.

## Local result cache, budget and prefix

Exact results are cached under the operator-owned audit directory, outside the
agent worktree. Entries can contain source excerpts: keep this directory private.
Defaults: 30-minute TTL, 128 entries, 4 MB total. Config/source/scope/code changes
invalidate reuse. Failed or insufficient semantic responses are not cached.
`--no-cache` disables this layer, not provider caching. No cross-project reuse.

`context_limits` in the approved adapter can constrain `max_selected_bytes`,
`max_request_bytes`, `max_output_tokens`, `max_task_tokens` and `max_calls`.
Only the operator config sets these bounds, never tool arguments. Selected input
and request bytes are checked before dispatch. HTTP generation receives the token
limit; Codex CLI keeps its verified flags and has no exact output-token cap here.
The cumulative reported-token limit is a **between-call circuit breaker**: the
last call can cross it. Dispatched calls with unknown usage stop further inference.
Rejected responses are charged. Cached results dispatch no model.

The selected prompt orders stable coverage/source fragments before the variable
question. This avoids incidental UUIDs and absolute paths in our prompt; it does
not remove CLI-managed context or guarantee provider cache hits. The microbench
has `--mode cycle --changed-question "..."`: cold request, exact local repeat and
changed question on the same source. Without `--live`, this only writes the plan.
`--no-cache` permits a separate provider-cache observation; never mix the two.
The optional `structured_output: "json_schema"` config is for compatible approved
Chat Completions backends only. No automatic provider fallback or key transfer.
