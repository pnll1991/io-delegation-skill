# Context Gateway usage

Normal users should not run the MCP server manually. Use the product CLI:

```bash
io-delegation setup --project . --dry-run
io-delegation setup --project .
io-delegation status --project .
io-delegation doctor --project .
```

Setup installs a stable runtime under `~/.io-delegation/` and one global `io_context` MCP registration per host. The bootstrap resolves the current configured project from its private marker. Outside configured projects it exposes no tools.

The coding agent receives three primary read-only tools:

- `search` — bounded local discovery/literal matches
- `extract` — exact local HTML/JSON/range extraction
- `query` — bounded semantic routing over explicit selections

`query` always works. Without Jev it uses local rules and returns bounded evidence. With Jev, routing runs in the MCP host process. A semantic worker is experimental: an approved config alone does not auto-dispatch it. Only a `bulk_read` route plus explicit `context_auto_dispatch: true` can send selected fragments to that worker.

The TypeSafe API key remains in `TYPESAFE_API_KEY` (or the configured environment variable). Generated state and host configuration contain only variable names, never credential values.

The read guard is separate and **off by default**. Enable `--guard observe` to collect decisions without blocking or `--guard enforce` after deliberate host verification. Tracked hook configuration is never modified without `--allow-tracked-config`.

Lifecycle:

```bash
io-delegation remove --project . --dry-run
io-delegation remove --project .
io-delegation backups
io-delegation restore BACKUP_ID --dry-run
```

Multiple projects share the same host-level MCP registration. Removing one project does not break the others; the global registration disappears only when the last project using that host is removed.
