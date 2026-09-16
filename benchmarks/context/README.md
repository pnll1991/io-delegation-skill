# Context MCP evaluation (v0.4)

The old worker integration and measurements are preserved. This suite separates
A (current-user Codex), B (local context tools), and C (the same tools plus an
optional approved semantic backend). No compulsory worker call in performance tests.

## First: tests and local handshake (no model)

```powershell
python -m unittest discover -s tests -p "test_context*.py" -v
python benchmarks/context/verify_context.py --output D:/context-check-new
```

The handshake starts the real MCP process but is NOT an authenticated Windows
boundary receipt. Add `--live` to authorize one synthetic principal turn. Add an
already approved `--config D:/.../worker.local.json` to test semantic extraction:
one intended auxiliary call. This does not change global configuration, select a
new provider, reuse private ChatGPT credentials as API keys, or launch an A/B.
Inspect the saved receipt and logs before proceeding. Preserve older receipts.
A JSON-schema capable HTTP backend is opt-in via `structured_output: "json_schema"`.

## Prepare held-out synthetic tasks without touching a website

```powershell
python benchmarks/context/fixtures.py create --output D:/context-suite-new --model gpt-5.6-luna
python benchmarks/context/evaluate.py D:/context-suite-new/manifest.json --output D:/context-plan-new --preflight
```

The fixtures create their own small Git repository and freeze answers for HTML,
JSON values and two-file policy interpretation. Independent positive and negative
validator checks run before any model. The fixture generator never calls a model.
Add `--config` at creation to include arm C; absent it, only A/B are created.
Use new directories. Neither results nor historical worktrees are reset or erased.

After a passing **authenticated** boundary check, operator-authorized execution:

```powershell
python benchmarks/context/evaluate.py D:/context-suite-new/manifest.json --output D:/context-live-new --live
```

That command uses model quota: one run per declared task and arm by default. It
is not executed by creating a plan. Exact model, effort, commit, task prompts,
acceptance/regression argv and source allowlists are pinned in the manifest.
To evaluate a real project, use this same manifest schema with its explicit
operator-selected repo, commit, prompts and validators. Do not compare changed
protocols or reclassify historical failures after adjusting validators.

No estimate is substituted for internal principal inference count. CLI completed
turns, tool calls, operation bytes and actual worker usage are separate fields.
A quality failure stays in cost-per-success's numerator. Unknown dispatched cost
stops further runs. Native subagent cost is not silently treated as fully known.
Reports: `runs.json`, `summary.json`, `by-task.json`, `summary.md`, per-run logs,
worker/operation journals, captured outputs and Git diffs. Logs/results may contain
project content: keep them private; nothing uploads them automatically.

`adoption_target` documents an engineering target (20% fewer system tokens with
no quality drop), not a proven result. Read per-task and per-arm counts and
variance. Start small; increase repetitions only if the pilot justifies its cost.
Differences must be interpreted relative to both A and B. Zero worker calls in C
can be correct, but any improvement then is not work performed by the auxiliary.

## Microcomparison before complete agent runs

`microbench.py` compares one explicit selected-evidence request over approved
backends. By default it only writes a plan. With `--live` it consumes quota.
`--mode cycle --changed-question "..."` measures cold, exact local repeat and a
different question. `--no-cache` disables the result cache, not provider caching.
Never infer token savings from corpus byte reduction alone. CLI and endpoint
pricing are different from a ChatGPT subscription and require separate evidence.

## Limits

Local tests use real subprocesses plus **synthetic** model responses. They prove
protocol/accounting mechanics, not savings. Python AST symbol selection is
implemented; other languages use explicit lines/spans/literal regions. Static
HTML is not a rendered browser DOM. JS/LSP and learned compression are deferred
until their own benefit is measured; no silent heuristic substitutes for them.

## Migrate the existing Kuatrometric benchmark without another setup loop

`migrate_manifest.py OLD_MANIFEST --output NEW_MANIFEST` reuses the explicitly
approved worker config and task/validators/allowlist, pins the source commit, and
creates A/B/C with one repetition. It never calls a model, copies keys, edits the
old manifest or changes global Codex configuration. It refuses mixed models,
different commits or legacy shell-string validators that cannot be safely copied.
Run the new boundary verification before evaluating the new manifest live.
