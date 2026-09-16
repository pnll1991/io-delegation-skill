# Worker transports and compatibility

## Runtime contract

Use the MCP tools that are actually registered. The efficient v0.4 service is `io_context`: local `search` and `extract`, plus `semantic_query` only with an explicitly approved configuration. See [CONTEXT_MCP.md](CONTEXT_MCP.md).

Do not start `python io_delegate.py` from the principal's restricted shell. The old Windows failure occurred before that process could start. A successful direct worker smoke is not a successful Codex-to-MCP boundary test.

## Existing v0.3 integration

`worker_mcp.py`, `verify_worker_mcp.py`, and old manifests remain as compatibility controls. Their `io_delegation.bulk_read` reads full selected files. Their receipts are tied to that code/configuration; they do not certify the v0.4 context service. Historical results are not overwritten or reclassified.

The CLI runner `io_delegate.py` remains an operator command and test fixture interface, NOT an instruction for the principal inside MCP. Existing command and Chat Completions adapters remain supported.

## Authorization and backend selection

The operator chooses the executable/model/endpoint in a reviewed configuration. `approved: true` is required for inference. Remote HTTP destinations require explicit `allow_remote: true` and HTTPS. Keys are referenced by environment variable name. Do not copy ChatGPT credentials, alter ACLs or fall back to another provider.

`codex-cli` reuses the existing normal login in a separate read-only ephemeral turn. `chat-completions` provides a minimal inference transport using the supplied messages without launching an agent. A new provider is not automatically configured or approved. Lower effort or a different model does not guarantee lower cost or equal quality.

## Accounting

Attempts, dispatches, responses, rejected evidence and unknown consumption are separate. A timeout after dispatch is not free. Local extraction and exact cache hits make no inference calls. Provider cache hits remain a subset of input tokens, not a local-result cache. Native Codex subagents are separate and can make total accounting incomplete.

`required` is only an integration test. Efficiency comparisons use baseline vs deterministic MCP vs the same MCP with an optional semantic worker. Keep the main model, permissions, task, commit and validators fixed.
