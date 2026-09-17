# Context Gateway V1 validation

This directory is the common evidence pipeline for dogfood, repeated routing A/B, semantic-worker experiments and cross-host validation.

## Rules

- Functional correctness comes before efficiency.
- Missing usage is `null`, never zero.
- Codex/principal, TypeSafe/Jev and worker token domains stay separate.
- Do not attribute an effect to Jev or the worker when that component was not called.
- Run records persist metadata/accounting, not raw source contents.
- Preserve negative runs and failed pairs; do not replace history with cleaner retries.

## Components

- `record.py` — versioned JSONL schema, validation, redaction and summaries.
- `experiment.py` — one-run worktree mechanics and explicit gateway/Jev/worker arms.
- `run.py` — immutable plan, schema/preflight modes and resumable orchestration.
- `paired.py` — paired deltas and causal-attribution warnings.
- `validators.py` — independent static gold calculation for real repositories.
- `run_real.py` — expands machine-local variables and invokes the resumable runner.
- `preflight_real.py` — computes every real-task gold at pinned commits with zero model calls.
- `host_validate.py` — authenticated Claude Code / Cursor headless host runner.
- `dogfood.real.template.json` — 20 tasks across 3 real repositories.

## Real dogfood

Set machine-local variables; never commit credentials or local paths:

```text
CAMARA_REPO=<path>
KUATROMETRIC_REPO=<path>
STEROID_REPO=<path>
IO_ROUTER_CONFIG=<approved TypeSafe router config outside project>
IO_WORKER_CONFIG=<approved semantic worker config outside project>
```

The TypeSafe API key remains only in the environment referenced by the router config.

Run the strong offline preflight first:

```bash
python benchmarks/v1-validation/preflight_real.py benchmarks/v1-validation/dogfood.real.template.json
```

It validates pinned commits/configs/task mix and calculates all validator golds in temporary detached worktrees. Expected `model_calls` is `0`.

Then execute into a new output directory:

```bash
python benchmarks/v1-validation/run_real.py benchmarks/v1-validation/dogfood.real.template.json --output <new-dir>
```

If interrupted, resume the exact immutable plan:

```bash
python benchmarks/v1-validation/run_real.py benchmarks/v1-validation/dogfood.real.template.json --output <same-dir> --resume
```

Completed run IDs are skipped; an incomplete run directory is archived under `interrupted/` before retry. Never delete a failure to make the sample cleaner.

## Generic schema/preflight/run

```bash
python benchmarks/v1-validation/run.py local-manifest.json --schema-only
python benchmarks/v1-validation/run.py local-manifest.json --preflight
python benchmarks/v1-validation/run.py local-manifest.json --output <new-dir>
```

Each task runs in a detached worktree at its pinned commit. A `bypass` arm does not attach the Context Gateway skill/MCP. Raw Codex JSONL is removed after accounting unless `--keep-raw` is explicitly used for debugging.

## Validate and summarize

```bash
python benchmarks/v1-validation/record.py validate <run-dir>/runs.jsonl
python benchmarks/v1-validation/record.py summary <run-dir>/runs.jsonl --output <run-dir>/summary.json
python benchmarks/v1-validation/paired.py <run-dir>/runs.jsonl --left control --right gateway-smart --output <run-dir>/paired.json
```

Paired efficiency excludes failed/incomplete pairs and warns if Jev or the semantic worker was never invoked.

## Causal arms

Typical arms:

- `control`: no gateway, Jev or worker.
- `gateway-local`: activation + local context tools, no external model.
- `gateway-jev`: gateway + Jev, no worker.
- `gateway-jev-worker`: gateway + Jev + approved semantic worker.

For worker-only measurement, keep Jev fixed/disabled and compare direct selected evidence against the same selected evidence sent through the worker.

## Host validation

`host_validate.py` supports `claude` and Cursor `agent` once those hosts are installed and authenticated. It does not use skip-all-permissions flags. Run the same deterministic/targeted/principal/multi-file mix and store results via `record.py` before closing #8.

## Security

Worker/router configs live outside target repositories. Task scopes are explicit. Sensitive paths are rejected from manifests. Secret-looking environment values are redacted from run records. The committed real-task template stores environment-variable placeholders rather than machine-local paths.
