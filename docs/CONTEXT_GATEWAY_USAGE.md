# Context Gateway usage

Normal users should not run the MCP server manually. Use the product CLI:

```bash
io-delegation setup --project .
io-delegation status --project .
io-delegation doctor --project .
```

The coding agent receives three primary read-only tools:

- `search` — bounded local discovery/literal matches
- `extract` — exact local HTML/JSON/range extraction
- `query` — bounded semantic routing over explicit selections

`query` always works. Without Jev or a worker it uses local rules and returns bounded evidence. With Jev, routing runs in the MCP host process. With an approved semantic worker, only a `bulk_read` route can dispatch selected fragments to that worker.

The TypeSafe API key remains in `TYPESAFE_API_KEY` (or the configured environment variable). Setup writes only the variable name into agent MCP configuration; it does not persist the key value.

The read guard is separate. Product setup defaults it to `observe`; use `--guard enforce` only when blocking oversized supported reads is intentionally desired.
