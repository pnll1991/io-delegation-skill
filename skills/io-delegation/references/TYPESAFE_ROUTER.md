# TypeSafe Jev decision router

Experimental, opt-in routing for the I/O Delegation skill.

## Goal

Use TypeSafe Jev only for the ambiguous routing decision that sits between localization and execution:

```text
locate files -> deterministic rule if obvious -> Jev only for the gray zone
                                           -> deterministic
                                           -> targeted_read
                                           -> bulk_read
                                           -> principal
```

The router does **not** replace `read_guard.py`, `bulk-read`, the worker, or main-agent reasoning. It only recommends which existing path is appropriate.

## Rollout tasks

- [x] Add a stdlib-only `decision_router.py`.
- [x] Send task text plus aggregate corpus metadata, never source contents or file names.
- [x] Keep the integration opt-in and require explicit `approved: true` configuration.
- [x] Read the API key from an environment variable; never store it in repository config.
- [x] Validate Jev Choice/Noul outputs and probability ranges.
- [x] Fall back to the existing skill rules when Choice confidence is below the configured threshold.
- [x] Add offline tests with a mocked TypeSafe transport.
- [x] Run a live calibration set with a real early-access API key.
- [x] Compare route accuracy against the current hand-written rules.
- [ ] Measure worker activation rate, main-agent context, total tokens, latency, and retrabajo before considering default activation.

## Live calibration · 16 September 2026

Eight labeled cases cover deterministic tools, targeted reads, bulk factual extraction, debugging and security. After clarifying the route boundaries, the compact criteria were run three consecutive times against jev-latest: **24/24 route decisions matched the labels**, with zero low-confidence fallbacks, mean confidence **0.953**, mean latency **835 ms**, and **6,495 input + 750 output tokens per 8-case run**.

The initial criteria scored 4/8 and never selected ulk_read; the failure was traced to overlapping route definitions. A more explicit draft reached 8/8 but used 7,807 input tokens per run. The final compact criteria preserved 8/8 across all three repeats while reducing Jev input by about 17% versus that draft. These are routing calibration results, not an end-to-end claim about worker quality or total session savings.

## Configure

Copy the example config somewhere outside the repository or into a project-local ignored file:

```bash
cp RUTA_SKILL/assets/router.typesafe.example.json .io-delegation-router.json
```

Edit it and set:

```json
{
  "version": 1,
  "approved": true,
  "provider": "typesafe",
  "model": "jev-latest",
  "api_key_env": "TYPESAFE_API_KEY",
  "timeout_seconds": 10,
  "min_confidence": 0.75
}
```

Set the key in the environment. Do not paste it into config files or prompts:

```bash
# PowerShell
$env:TYPESAFE_API_KEY="..."

# bash/zsh
export TYPESAFE_API_KEY="..."
```

## Inspect before sending

`--dry-run` builds the exact state without making a network request:

```bash
python RUTA_SKILL/scripts/decision_router.py \
  --root . \
  --config .io-delegation-router.json \
  --task "Find where workers are initialized and which paths activate them" \
  --operation exploration \
  --search-results 18 \
  --known-symbols 2 \
  --paths src/router.py src/worker.py \
  --dry-run
```

The state contains the task text, file count, aggregate bytes, extension counts and optional localization signals. It does not include source contents or file names.

## Call Jev

Remove `--dry-run`:

```bash
python RUTA_SKILL/scripts/decision_router.py \
  --root . \
  --config .io-delegation-router.json \
  --task "Find where workers are initialized and which paths activate them" \
  --operation exploration \
  --search-results 18 \
  --known-symbols 2 \
  --paths src/router.py src/worker.py
```

Example output shape:

```json
{
  "status": "ok",
  "model": "jev-latest",
  "route": "bulk_read",
  "model_route": "bulk_read",
  "confidence": 0.91,
  "probabilities": {
    "deterministic": 0.02,
    "targeted_read": 0.05,
    "bulk_read": 0.9,
    "principal": 0.03
  },
  "delegation_useful": 0.94,
  "reasoning_required": 0.08,
  "min_confidence": 0.75,
  "usage": {
    "input_tokens": 123,
    "output_tokens": 7
  }
}
```

If Choice confidence is below `min_confidence`, `route` becomes `current_rules` while `model_route` preserves Jev's raw choice for measurement.

## Separate compute orchestration

The route selector above remains experimental and opt-in. I/O Delegation now also has a distinct Jev **compute scorer** for cheap-first execution after local routing already identifies a bulk factual candidate. It returns bounded sufficiency/risk/uncertainty/reasoning probabilities; deterministic local thresholds choose T1 cheap worker or T2 principal. It does not enable the route selector, invent a provider/model, or weaken worker approval. See [ORCHESTRATION.md](ORCHESTRATION.md).

## Intended skill behavior

Use the router only after localization and only when ordinary rules do not make the route obvious. Obvious deterministic searches, small bounded reads, security-sensitive work, exact edits and known debugging paths should not pay for an extra model call.

A routing recommendation does not authorize a worker, provider, write, command, or external data transfer beyond the TypeSafe request itself. Existing approval and validation rules still apply.

## Test

Offline tests never call TypeSafe:

```bash
python -m unittest tests.test_decision_router -v
python -m unittest discover -s tests -v
```

Live testing should start with a small labeled calibration set rather than enabling the router globally. Record both `model_route` and effective `route` so low-confidence fallbacks remain measurable.
