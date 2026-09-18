# Jev compute orchestration

I/O Delegation can use Jev as a metadata-only compute scorer after local routing has
already identified a task as a possible semantic delegation. The goal is lower total
cost per validated task, not maximum worker activation.

## Tiers

- **T0 — local/deterministic:** search, extract, bounded selected fragments, cache.
  No worker model call.
- **T1 — cheap worker:** one approved low-cost/read-only worker attempt. Its evidence
  must pass the existing literal-evidence validator and return no unknowns.
- **T2 — strong principal:** the current Claude/Codex/Cursor principal reasons from
  bounded evidence. I/O Delegation does not pretend it can switch the model of an
  already-open host session.

The execution path is:

    local routing
        |
        +-- obvious local/targeted ----------> T0
        +-- security/debug/edit/architecture -> T2
        |
        +-- bulk factual candidate
                |
                Jev compute scores
                |
          deterministic thresholds
             /             \
           T1               T2
           |                |
      cheap worker       principal
           |
      evidence validator
        /       \
      pass      fail/unknown
       |            |
      done      escalate T2

Jev does not directly choose an arbitrary provider or model. It returns bounded
probabilities. Local code applies fixed thresholds.

## Setup

When an approved worker is configured, normal setup uses orchestration mode `auto`.
This creates a private generated scorer config under `~/.io-delegation/configs/`.
The project stores only the config path and environment-variable name.

    io-delegation setup --project . --worker-config /private/worker.json

Use a persistent project override when required:

    io-delegation setup --project . --orchestration off
    io-delegation setup --project . --orchestration on

`--orchestration on` requires an approved worker. `auto` is inert when there is no
worker. A missing TypeSafe key fails closed to T2/principal; it never causes provider
fallback or an unapproved worker call.

## What Jev sees

Compute scoring reuses the router's metadata boundary:

- task/question text;
- operation hint;
- selected file count;
- aggregate bytes and extension counts;
- optional aggregate search-result / known-symbol counts.

Source contents and file names are not sent to Jev.

Jev returns these scores:

- `cheap_model_sufficient`;
- `risk_high`;
- `uncertainty_high`;
- `reasoning_required`;
- `parallelism_useful` (recorded for future policy work; it does not spawn extra
  workers in this version).

The generated default policy selects T1 only when all of these are true:

- `cheap_model_sufficient >= 0.78`
- `risk_high <= 0.30`
- `uncertainty_high <= 0.35`
- `reasoning_required <= 0.40`

Debugging, architecture, security, editing and generation are hard local T2 routes
regardless of Jev scores.

## Approved cheap model profile

The worker configuration remains the authorization boundary. An optional
`compute_profiles.cheap` block can select a cheaper model that uses the same approved
adapter/provider.

Codex CLI example:

    {
      "approved": true,
      "adapter": "codex-cli",
      "model": "strong-default-model",
      "reasoning_effort": "medium",
      "compute_profiles": {
        "cheap": {
          "model": "approved-cheap-model",
          "reasoning_effort": "low"
        }
      }
    }

Compatible Chat Completions example:

    {
      "approved": true,
      "adapter": "chat-completions",
      "url": "https://approved.example/v1/chat/completions",
      "allow_remote": true,
      "api_key_env": "WORKER_API_KEY",
      "model": "strong-default-model",
      "compute_profiles": {
        "cheap": {
          "model": "approved-cheap-model",
          "max_output_tokens": 800
        }
      }
    }

No profile means T1 uses the already approved base worker. Opaque `command` adapters
cannot dynamically override models; they can still serve as the T1 worker when the
operator already considers that command the cheap worker.

For Codex CLI, I/O Delegation can lower the approved model/reasoning effort but does
not claim a hard per-call output-token cap because the CLI does not expose a portable
one. Compatible HTTP workers receive the configured output cap.

## Validation and escalation

A T1 response is accepted only when the existing semantic validator confirms:

- status is `ok`;
- at least one finding exists;
- every quoted evidence string occurs in the selected local fragment;
- original files are unchanged;
- `unknowns` is empty.

Anything else escalates to T2. The failed T1 usage is still counted.

Transport errors, missing usage, missing TypeSafe credentials, invalid scorer output,
or scorer configuration changes never trigger a different provider automatically.
The safe fallback is the principal with bounded local evidence.

## Cost accounting

Context-operation telemetry records:

- router Jev calls and reported tokens;
- compute-orchestrator Jev calls and reported tokens;
- T0/T1/T2 tier;
- selected bytes;
- worker calls and their reported usage;
- escalations;
- cache hits.

System accounting adds principal + worker + Jev control-plane tokens. If a dispatched
component does not report usage, total accounting is marked incomplete rather than
treating the missing usage as zero.

The metric to optimize is **total tokens/cost per validated task**, not principal
tokens alone and not worker activation rate.

## Current boundary

This version intentionally does not:

- change the model of an already-open Claude/Cursor/Codex principal session;
- let Jev invent provider/model identifiers;
- spawn multiple workers from the `parallelism_useful` score;
- retry a failed cheap worker with another provider;
- accept unverified worker summaries.

Those are separate experiments and should be enabled only after measured evidence.
