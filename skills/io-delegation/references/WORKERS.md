# Worker transports and compatibility

## Runtime contract

Use the MCP tools that are actually registered. The V1 service is `io_context`: local `search`, exact `extract`, and `query`. Semantic inference is internal to `query`; there is no public `semantic_query` bypass. See [CONTEXT_MCP.md](CONTEXT_MCP.md).

Do not start `python io_delegate.py` from the principal's restricted shell. The old Windows failure occurred before that process could start. A successful direct worker smoke is not a successful Codex-to-MCP boundary test.

## Existing v0.3 integration

`worker_mcp.py`, `verify_worker_mcp.py`, and old manifests remain as compatibility controls. Their `io_delegation.bulk_read` reads full selected files. Their receipts are tied to that code/configuration; they do not certify the v0.4 context service. Historical results are not overwritten or reclassified.

The CLI runner `io_delegate.py` remains an operator command and test fixture interface, NOT an instruction for the principal inside MCP. Existing command and Chat Completions adapters remain supported.

## Authorization and backend selection

The operator chooses the executable/model/endpoint in a reviewed configuration. `approved: true` is required for inference. Normal setup can additionally enable Jev cheap-first compute orchestration: Jev scores only metadata, deterministic local thresholds decide whether one T1 worker attempt is justified, and failed/unknown evidence escalates to the principal. The older unconditional worker path remains separate and still requires `context_auto_dispatch: true`. Remote HTTP destinations require explicit `allow_remote: true` and HTTPS. Keys are referenced by environment variable name. Do not copy ChatGPT credentials, alter ACLs or fall back to another provider.

`codex-cli` reuses the existing normal login in a separate read-only ephemeral turn. `chat-completions` provides a minimal inference transport using the supplied messages without launching an agent. An optional reviewed `compute_profiles.cheap` can select a cheaper model/effort on the same approved adapter. Jev cannot invent providers or model IDs. A new provider is not automatically configured or approved. Lower effort or a different model does not guarantee lower cost or equal quality. See [ORCHESTRATION.md](ORCHESTRATION.md).

## Accounting

Attempts, dispatches, responses, rejected evidence and unknown consumption are separate. A timeout after dispatch is not free. Local extraction and exact cache hits make no inference calls. Provider cache hits remain a subset of input tokens, not a local-result cache. Jev routing/orchestration tokens are part of system accounting; if a control-plane call does not report usage, the total stays incomplete rather than treating it as free. Native Codex subagents are separate and can also make total accounting incomplete.

`required` is only an integration test. Efficiency comparisons use baseline vs deterministic MCP vs the same MCP with an optional semantic worker. Keep the main model, permissions, task, commit and validators fixed.
