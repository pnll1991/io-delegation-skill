# Worker transports and compatibility

## Runtime contract

Use the MCP tools that are actually registered. The V1 service is `io_context`: local `search`, exact `extract`, and `query`. Semantic inference is internal to `query`; there is no public `semantic_query` bypass. See [CONTEXT_MCP.md](CONTEXT_MCP.md).

Do not start `python io_delegate.py` from the principal's restricted shell. The old Windows failure occurred before that process could start. A successful direct worker smoke is not a successful Codex-to-MCP boundary test.

## Existing v0.3 integration

`worker_mcp.py`, `verify_worker_mcp.py`, and old manifests remain as compatibility controls. Their `io_delegation.bulk_read` reads full selected files. Their receipts are tied to that code/configuration; they do not certify the v0.4 context service. Historical results are not overwritten or reclassified.

The CLI runner `io_delegate.py` remains an operator command and test fixture interface, NOT an instruction for the principal inside MCP. Existing command and Chat Completions adapters remain supported.

## Authorization and backend selection

The operator chooses the executable/model policy/endpoint in a reviewed configuration. `approved: true` is required for inference. Normal setup can enable Jev host-aware orchestration: Jev scores only metadata and local policy selects an approved Codex/Cursor model profile under the operator's ceiling. Valid-but-incomplete evidence may escalate within `max_escalations`; transport/config/budget failures return to the principal. The older unconditional worker path remains separate and still requires `context_auto_dispatch: true`. Remote HTTP destinations require explicit `allow_remote: true` and HTTPS. Keys are referenced by environment variable name. Do not copy credentials, alter ACLs or silently fall back to another provider.

`host-cli` reuses the authenticated CLI belonging to the MCP caller: Codex gets an isolated ephemeral read-only `codex exec`; Cursor gets an isolated temporary Ask-mode workspace with shell/write/web/MCP denied. Dedicated `codex-cli` and `cursor-cli` configs are also supported. `chat-completions` remains a minimal explicit endpoint transport but does not receive automatic host model IDs. The legacy `compute_profiles.cheap` format remains compatible; new dynamic policies use `model_policy`. Jev cannot invent providers or model IDs. See [ORCHESTRATION.md](ORCHESTRATION.md).

## Cursor CLI trust boundary

The Cursor worker runs in a temporary workspace, Ask mode, with Cursor sandbox enabled and project CLI permissions that allow only the prompt file while denying shell, writes, web, MCP and common outside-workspace read forms. This is defense in depth, **not an OS sandbox/chroot guarantee**. Cursor's read/search tooling is controlled by Cursor permissions rather than by a filesystem namespace, so run this adapter only in the same trusted local environment where Cursor itself is already authorized.

## Accounting

Attempts, dispatches, responses, rejected evidence and unknown consumption are separate. A timeout after dispatch is not free. Local extraction and exact cache hits make no inference calls. Provider cache hits remain a subset of input tokens, not a local-result cache. Jev routing/orchestration tokens are part of system accounting; if a control-plane call does not report usage, the total stays incomplete rather than treating it as free. Native Codex subagents are separate and can also make total accounting incomplete.

`required` is only an integration test. Efficiency comparisons use baseline vs deterministic MCP vs the same MCP with an optional semantic worker. Keep the main model, permissions, task, commit and validators fixed.
