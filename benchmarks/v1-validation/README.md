# Context Gateway V1 validation

This directory is the common evidence pipeline for dogfood, activation-gate measurement, repeated Jev routing A/B, semantic-worker isolation, cheap-first compute orchestration A/B, three-host parity and release readiness.

## Rules

- Functional correctness comes before efficiency.
- Missing usage is `null`, never zero.
- Principal, TypeSafe/Jev and worker token domains stay separate.
- Do not attribute an effect to Jev or the worker unless that component was actually called.
- Run records persist metadata/accounting, not raw source contents.
- Preserve negative runs and failed pairs; do not replace history with cleaner retries.
- Threshold calibration never edits production config automatically.

## Core components

- `record.py` — versioned JSONL records, redaction and summaries.
- `experiment.py` — detached-worktree mechanics and explicit gateway/Jev/worker arms.
- `run.py` — immutable plans, preflight and resume.
- `run_real.py` — expands machine-local variables without persisting them.
- `paired.py` — paired deltas and causal-attribution warnings.
- `validators.py` — independent scope-aware static gold calculation.
- `preflight_real.py` — validates all real dogfood golds with zero model calls.
- `security_audit.py` — bounded secret/content telemetry scan.
- `release_evidence.py` — fail-closed product/release gates.

## Machine-local variables

Never commit credentials or local paths:

```text
CAMARA_REPO=<path>
KUATROMETRIC_REPO=<path>
STEROID_REPO=<path>
IO_ROUTER_CONFIG=<approved TypeSafe config outside project>
IO_WORKER_CONFIG=<approved worker config outside project>
```

The TypeSafe key remains only in the environment named by the router config.

## T11 activation gate

First classify the 20 real tasks without Codex/Jev/worker calls:

```bash
python benchmarks/v1-validation/activation_preflight.py \
  benchmarks/v1-validation/dogfood.real.template.json
```

Small controls must bypass; broad understanding/audit/cross-file families must enable. Mismatches are evidence, not an instruction to auto-tune thresholds.

Evidence is accepted only when every run's persisted `command.json` satisfies the `codex-isolated-v2` command contract; this prevents pre-isolation historical samples from satisfying release gates.

The held-out overhead sample is:

```text
activation.real.template.json
3 tasks × 2 arms × 5 repetitions = 30 runs
gate-auto vs gate-always
Jev off, worker off
```

Execute it through `run_real.py`, then evaluate:

```bash
python benchmarks/v1-validation/activation_evidence.py <run-dir>/runs.jsonl \
  --run-root <run-dir> --output <run-dir>/activation-evidence.json
```

## Real dogfood

Strong offline preflight:

```bash
python benchmarks/v1-validation/preflight_real.py \
  benchmarks/v1-validation/dogfood.real.template.json
```

Expected: 20 tasks, 3 pinned repositories, 5 required families, `model_calls=0`.

Official execution is one randomized/interleaved 40-run plan:

```bash
python benchmarks/v1-validation/run_real.py \
  benchmarks/v1-validation/dogfood.real.template.json --output <new-dir>
```

Resume without overwriting history:

```bash
python benchmarks/v1-validation/run_real.py \
  benchmarks/v1-validation/dogfood.real.template.json --output <same-dir> --resume
```

## Repeated Jev A/B

`jev.real.template.json` defines 4 families × 2 arms × 5 repetitions = 40 runs. Both arms keep model, worker availability, scope and validators fixed; only Jev recommendation differs.

Preflight uses the normal resumable runner and must report zero model calls. TypeSafe usage/latency stays separate from principal+worker accounting.

Threshold calibration lives under `benchmarks/typesafe-router/calibrate.py`; calibration and holdout cases are distinct.

## Semantic-worker isolation

`worker_ab.real.template.json` contains 5 real worker-eligible tasks. The two arms receive the same preselected bundle:

- `direct-selected`
- `semantic-worker`

Jev is disabled. The principal runs in an empty read-only workspace with shell, tools, multi-agent and network disabled.

Portable launcher/preflight:

```bash
python benchmarks/v1-validation/worker_ab_real.py \
  benchmarks/v1-validation/worker_ab.real.template.json --preflight
```

Expected: 5 tasks × 2 arms × 5 repetitions = 50 planned runs, zero model calls during preflight.

## Validate and summarize

```bash
python benchmarks/v1-validation/record.py validate <run-dir>/runs.jsonl
python benchmarks/v1-validation/record.py summary <run-dir>/runs.jsonl \
  --output <run-dir>/summary.json
python benchmarks/v1-validation/paired.py <run-dir>/runs.jsonl \
  --left control --right gateway-smart --output <run-dir>/paired.json
```

Failed/incomplete pairs are never used for efficiency deltas.

## Cheap-first compute orchestration A/B

The new T0/T1/T2 path is evaluated separately from both the Jev route-selector experiment and the unconditional semantic-worker experiment. Fill `orchestration_ab.template.json` with paired real-host measurements from the same task/commit and evaluate:

```bash
python benchmarks/v1-validation/orchestration_ab.py --input <private-paired-evidence.json>
```

The measured system-token total is principal + worker + Jev control-plane tokens. Missing usage is never zero. A conservative gate requires at least 20 comparable quality-passing pairs, no quality regressions, complete accounting, at least one T1 case and >=10% median system-token savings. This evaluator does not execute providers.

## Authenticated host parity

The deterministic four-file fixture covers the same four route classes on Codex, Claude Code and Cursor:

```text
deterministic search
targeted query
principal security query
bulk factual query
```

`host_validate.py` isolates each host from unrelated tools/config while recording actual MCP audit behavior. `host_real.py` executes the full lifecycle:

```text
fixture
→ setup
→ 4 authenticated cases
→ move project
→ refresh setup
→ doctor
→ security scan
→ remove
```

Once all three CLIs are authenticated, run them together:

```bash
python benchmarks/v1-validation/host_parity_real.py \
  --worker-config <approved-worker-config> --output <new-dir>
```

The output includes `parity.json`, `parity.md`, combined `security.json`, per-host run records and lifecycle evidence. Release parity requires all three hosts and all lifecycle phases to pass.

## Release evidence

`release_evidence.py` fails closed unless all of these exist and pass:

- 20-task dogfood quality/context/safety gates;
- separate T11 held-out activation evidence;
- repeated Jev causal evidence where Jev was actually called;
- isolated worker evidence where the worker was actually called;
- Codex/Claude/Cursor parity with complete lifecycle;
- clean security audit.

No single token-savings percentage is treated as a release criterion.

## Security

Worker/router configs live outside target repositories. Scopes are explicit. Sensitive paths are rejected. Secret-looking environment values are redacted from run records. Real-task templates contain environment placeholders rather than machine-local paths. Host validation stores bounded metadata/results and validates final JSON in memory rather than granting write access just to create benchmark files.
