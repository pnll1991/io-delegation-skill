# Jev host-aware model orchestration

I/O Delegation uses Jev to estimate task requirements, then applies a local,
user-editable model policy. Jev never receives a list of approved model IDs and never
chooses a provider directly.

The optimization target is **total cost per validated task**, not principal-context
reduction and not the number of worker calls.

## Execution model

```text
local deterministic work ------------------------------> T0
security / architecture / debugging / editing --------> principal
bulk factual candidate
        |
        v
Jev metadata-only scores
        |
        v
local host-aware model policy
        |
        +--> minimum efficient approved profile
        |        |
        |        v
        |   evidence validator
        |      |       |
        |     pass   valid-but-incomplete
        |      |       |
        |     done   next approved profile
        |              |
        |            bounded by max_escalations
        |
        +--> no profile under ceiling can satisfy demand -> principal
```

Transport/configuration failures, missing credentials, timeouts and unknown usage do
**not** walk an expensive escalation ladder. They fall back to the principal.

## What Jev scores

The scorer sees task text plus aggregate metadata, never source bodies or file names.
It returns probabilities for:

- `cheap_model_sufficient`
- `risk_high`
- `uncertainty_high`
- `reasoning_required`
- `parallelism_useful`

Local code converts those signals into a normalized demand. Sensitive operations are
hard-routed to the principal before model selection.

## Default presets

`setup --model-preset` accepts:

- `cost`: lower demand threshold and strongest cost weighting.
- `balanced`: default; combines normalized cost/capability and latency evidence.
- `quality`: raises task demand and weights capability more heavily.

The selector considers only profiles at or below the user's effective ceiling. Among
profiles whose declared capability covers task demand it minimizes a host-local
objective derived from `cost_index / capability`, optional benchmark steps and the
selected preset. A task may jump directly to a stronger model; it does not burn cheap
attempts first when the scorer already says they are insufficient.

## Codex defaults

The default Codex registry is based on current OpenAI model positioning and published
API pricing as of September 2026. The numbers stored in `cost_index` are normalized
selection weights, not a billing promise.

| Profile | Model | Effort | Default role |
| --- | --- | --- | --- |
| `luna-medium` | GPT-5.6 Luna | medium | cheap bounded factual work |
| `luna-high` | GPT-5.6 Luna | high | cheap but reasoning-heavier factual work |
| `terra-medium` | GPT-5.6 Terra | medium | everyday multi-file synthesis |
| `sol-medium` | GPT-5.6 Sol | medium | difficult bounded synthesis |
| `astra-low` | GPT-6 Astra | **low** | hardest worker task / default hard ceiling |

**Astra low is the highest default Codex profile.** There is no Astra medium/high in
the default registry. A user can replace the registry, but the product default never
goes above Astra low.

Research references:

- OpenAI model guide: https://developers.openai.com/api/docs/models
- OpenAI API pricing: https://developers.openai.com/api/docs/pricing
- GPT-5.6 Luna: https://developers.openai.com/api/docs/models/gpt-5.6-luna
- GPT-5.6 Terra: https://developers.openai.com/api/docs/models/gpt-5.6-terra
- GPT-5.6 Sol: https://developers.openai.com/api/docs/models/gpt-5.6-sol
- GPT-6 Astra: https://developers.openai.com/api/docs/models/gpt-6-astra

## Cursor defaults

Cursor uses a separate registry because model/effort efficiency differs inside the
Cursor harness. CursorBench 4.0 (September 10, 2026) reported, among other points:

| Profile | CursorBench score | Reported cost/task | Steps |
| --- | ---: | ---: | ---: |
| Luna medium | 22.2% | $0.08 | 32 |
| Luna high | 29.4% | $0.25 | 64 |
| Terra medium | 27.6% | $0.64 | 25 |
| Sol medium | 31.1% | $1.77 | 32 |
| Sol high | 35.7% | $2.85 | 41 |

The balanced default therefore uses:

```text
luna-medium -> luna-high -> sol-medium -> sol-high
```

Terra medium remains available in the registry for user policies where its lower
reported step count matters, but it is skipped by the default balanced/cost order
because Luna high had higher benchmark score at much lower reported task cost.

GPT-6 Astra is **not** in the default Cursor registry because the current Cursor model
documentation/benchmark used for this policy does not list an Astra result.

Research references:

- CursorBench 4.0: https://cursor.com/cursorbench
- Cursor models/pricing: https://cursor.com/docs/models-and-pricing
- Cursor Router announcement: https://cursor.com/blog/router
- Cursor model-routing guide: https://cursor.com/guides/model-routing

## Cursor native Router

Cursor's native Router has its own real-traffic optimization system. I/O Delegation
does not guess whether an account has access or invent an optimization-mode CLI
identifier.

An operator can explicitly delegate model choice to a reviewed Cursor CLI model
string:

```json
{
  "model_policy": {
    "hosts": {
      "cursor": {
        "strategy": "native-router-first",
        "native_router_model": "auto"
      }
    }
  }
}
```

That exact string is passed to Cursor. No direct fallback ladder is attempted after a
Router transport failure.

## User-editable policy

The simplest control is project-scoped:

```bash
io-delegation setup --project . --model-preset cost
io-delegation setup --project . --model-preset balanced
io-delegation setup --project . --model-preset quality
```

For advanced control copy `assets/worker.host-cli.example.json` outside the project,
review it, set `approved: true`, and pass it with `--worker-config`.

Supported host-policy controls include:

- `profiles`: fully replace the host registry.
- `orders.cost|balanced|quality`: ordered escalation paths.
- `min_profile` / `max_profile`: hard lower/upper limits.
- `allowed_profiles` / `blocked_profiles`.
- `allow_escalation`.
- `max_escalations`.
- Cursor-only `strategy: native-router-first` + `native_router_model`.

Example lower Codex ceiling:

```json
{
  "approved": true,
  "adapter": "host-cli",
  "model_policy": {
    "preset": "balanced",
    "hosts": {
      "codex": {
        "max_profile": "sol-medium",
        "blocked_profiles": ["terra-medium"],
        "max_escalations": 1
      }
    }
  }
}
```

A complete custom registry is also allowed:

```json
{
  "model_policy": {
    "hosts": {
      "codex": {
        "profiles": [
          {
            "id": "small",
            "model": "approved-small-model",
            "effort": "low",
            "capability": 0.45,
            "cost_index": 0.1
          },
          {
            "id": "max",
            "model": "approved-max-model",
            "effort": "medium",
            "capability": 1.0,
            "cost_index": 1.0
          }
        ],
        "orders": {
          "cost": ["small", "max"],
          "balanced": ["small", "max"],
          "quality": ["max"]
        },
        "max_profile": "max"
      }
    }
  }
}
```

The user-supplied model IDs are authorization/configuration. Jev never creates them.

## Host CLI worker

`adapter: "host-cli"` reuses the authenticated CLI belonging to the host that called
the global MCP:

- Codex -> isolated ephemeral `codex exec`, read-only sandbox.
- Cursor -> isolated temporary workspace, Ask mode, sandbox enabled, with shell,
  writes, web and MCP denied.

The same config can therefore serve a project used from both Codex and Cursor. Optional
`codex_executable` and `cursor_executable` override executable discovery.

Existing `codex-cli`, `cursor-cli`, command and Chat Completions workers remain
supported. The pre-v1.3 `compute_profiles.cheap` format is retained for compatibility,
but it cannot be combined with `model_policy`.

## Cursor trust boundary

The Cursor CLI worker uses a temporary workspace, Ask mode, Cursor sandbox, and explicit deny rules for shell, writes, web, MCP, parent/absolute reads and `.cursor/**`. These controls reduce unintended access but are not described as an OS-level chroot: Cursor read/search capabilities remain subject to Cursor's own permission enforcement. Use the adapter only on machines/workspaces where Cursor itself is trusted.

## Validation and escalation

Every worker response still passes the existing local evidence validator:

- status must be `ok`;
- at least one finding must exist;
- evidence must occur literally inside the selected fragment;
- original source files must remain unchanged;
- `unknowns` must be empty for acceptance.

A valid response that contains evidence but still reports unknowns may escalate to the
next approved profile. The escalation count is bounded. Transport errors and budget
errors go directly to principal fallback.

## Accounting

Operation telemetry records:

- host and preset;
- normalized task demand;
- initial/final model profile;
- model/effort/adapter;
- model attempts and escalations;
- worker reported usage;
- Jev scorer usage;
- cache hits.

System accounting remains:

```text
principal + worker attempts + Jev routing/scoring
```

Unknown dispatched usage is never treated as zero.

`cost_index` and Cursor benchmark metadata are selection inputs, not invoices.
Release claims must still use paired measured usage/cost from the actual configured
provider/account.

## Current boundaries

This feature does not:

- switch the already-open principal model in Claude/Cursor/Codex;
- delegate security/debugging/architecture/editing judgment away from the principal;
- invent model IDs or providers;
- assume Cursor native Router access;
- treat benchmark scores as permanent truth;
- auto-edit policy thresholds from telemetry.

The registry is intentionally replaceable because model pricing and benchmark
efficiency change over time.
