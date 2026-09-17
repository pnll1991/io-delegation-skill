# Context Gateway V1 validation

This directory is the common measurement layer for dogfood, repeated routing A/B and semantic-worker experiments.

## Rules

- Functional correctness comes before efficiency.
- Missing usage is `null`, never zero.
- Codex/principal, TypeSafe/Jev and worker token domains stay separate.
- Do not attribute an effect to Jev or the worker when that component was not called.
- Run records persist metadata and accounting, not raw source contents.
- Preserve negative runs and failed pairs; do not replace history with cleaner retries.

## Files

- `record.py` — versioned JSONL schema, validation, redaction and aggregate summaries.
- `experiment.py` — worktree-based Codex runner with a deterministic activation gate and explicit gateway/Jev/worker arms.
- `paired.py` — paired deltas and causal-attribution warnings.
- `manifest.example.json` — shape of a local experiment manifest. Real local repository paths/configs should stay outside committed manifests.

## Preflight

```bash
python benchmarks/v1-validation/experiment.py local-manifest.json --preflight
```

Preflight resolves pinned commits and validates approved worker/router configuration without making model calls.

## Run

```bash
python benchmarks/v1-validation/experiment.py local-manifest.json \
  --output benchmark-output/v1-run-001
```

The output directory must be new/empty. Each task runs in a detached temporary worktree at its pinned commit. The activation gate executes before Codex; a `bypass` run does not attach the Context Gateway skill or MCP.

Raw Codex JSONL is removed by default after accounting. Use `--keep-raw` only for an explicitly reviewed debugging run because raw agent logs may contain repository content.

## Validate and summarize

```bash
python benchmarks/v1-validation/record.py validate benchmark-output/v1-run-001/runs.jsonl
python benchmarks/v1-validation/record.py summary benchmark-output/v1-run-001/runs.jsonl \
  --output benchmark-output/v1-run-001/summary.json
```

For paired experiments:

```bash
python benchmarks/v1-validation/paired.py benchmark-output/v1-run-001/runs.jsonl \
  --left control --right gateway-jev \
  --output benchmark-output/v1-run-001/paired.json
```

The paired report excludes incomplete/failed pairs from efficiency deltas and emits warnings when Jev or the semantic worker was never invoked.

## Experimental arms

Typical arms:

- `control`: no gateway, no Jev, no worker.
- `gateway-local`: activation gate + local `search/extract/query`, no external models.
- `gateway-jev`: gateway + Jev, no worker.
- `gateway-jev-worker`: gateway + Jev + approved semantic worker.

For a worker-only causal experiment, keep Jev disabled/fixed and compare `gateway-local` against a gateway arm with `worker=true` on tasks deliberately eligible for `bulk_read`.

## Security

Worker/router configs must live outside the target repository. Scopes are explicit. Sensitive-path patterns are rejected from manifests. Secret-looking environment values are redacted from records and summaries. The runner stores no raw agent logs by default.
