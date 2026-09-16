---
name: io-delegation
description: Retrieve compact evidence through the configured MCP tools. Prefer deterministic search/extract; use an approved semantic worker only for interpretation of selected fragments. Never delegate by file size alone.
license: MIT
metadata:
  version: "0.4.0"
---

# I/O Delegation

Use only tools actually advertised by this session. Do not search guessed global skill paths, start worker launchers in the shell, install dependencies or change permissions.

With the `io_context` MCP server:
- `search`: bounded literal matches in explicit permitted files/globs; no model.
- `extract`: static HTML fields, JSON pointers or source ranges; batch compatible fields/files; no model.
- `semantic_query`: interpretation of explicit source selections, only when this tool is advertised. A worker is never required for routine extraction. Prefer a single narrow query or grouped related questions.

Send paths/selectors, not whole file contents. Results contain sources, coverage, omitted content and references. `partial`, `missing`, `insufficient_context`, `budget_exceeded` and `error` are not complete answers. A missing value is not an absence claim about an entire repository. Expand only the identified missing scope and within budget.

Verify evidence that supports decisions. The main agent retains debugging, architecture, security and final edits. A literal match proves location, not semantic correctness. Do not first read an entire corpus and then ask a worker to read it again.

If an explicitly approved TypeSafe router config is present, use `scripts/decision_router.py` only after localization and only when the route is still ambiguous. `current_rules`, low confidence or router errors fall back to these local rules. A `bulk_read` recommendation does not itself authorize a worker. See [TYPESAFE_ROUTER.md](references/TYPESAFE_ROUTER.md) only when configuring or evaluating the router.

The legacy `io_delegation.bulk_read` tool is usable only when advertised; it reads full files and is a compatibility/integration path, not the efficient default. Do not launch Python as a fallback when MCP is unavailable. Stop and report transport failures without retries, provider changes or sandbox changes.

[Setup and migration](references/CONTEXT_MCP.md) are for the user/operator, not instructions to repeat during tasks. Installing a skill does not connect a worker. Report total principal plus worker usage and task quality; never infer savings from model activation alone.
