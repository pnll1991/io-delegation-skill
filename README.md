# I/O Delegation

**English** | [Español](README.es.md)

### Give coding agents the right context, not the whole repository.

[![Offline tests](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](docs/INSTALLATION_V1.md)

**Claude Code · Codex · Cursor**

I/O Delegation is a local-first context gateway for coding agents. It reduces unnecessary repository exposure by finding, extracting and routing bounded evidence before the main model reasons over it.

The product is deliberately control-first:

- deterministic search/extraction before model calls;
- debugging, security, architecture, editing and generation stay with the principal agent;
- Jev routing is optional;
- model switching is not the default;
- suggest / balanced is the default model-control mode;
- manual preserves an explicitly selected model;
- auto is explicit opt-in and is the only mode allowed to switch approved worker profiles;
- context compaction is automatic by default, with native-host fallback;
- read enforcement is optional and off by default.

The gateway works without TypeSafe, without a worker and without changing the model you already selected.

## Current status

| Area | Status |
| --- | --- |
| MCP context gateway | Implemented for Claude Code, Codex and Cursor |
| Public MCP surface | search, extract, query |
| Local deterministic routing | Default |
| TypeSafe Jev route selector | Optional, explicit opt-in |
| Host-aware model policy | Codex + Cursor |
| Model-control default | suggest / balanced |
| Dynamic profile switching | auto only |
| Automatic context compaction | Enabled by default |
| Read guard | Optional, off by default |
| Live Codex validation | Completed on real authenticated Codex |
| Cursor policy | Implemented/tested; not yet live-calibrated on a real user Cursor session |
| Production savings claim | None — current evidence is calibration/controlled validation, not a billing-savings proof |

## Architecture

~~~text
coding agent
    |
    v
 io_context MCP
    |
    +-- search  -> deterministic discovery
    +-- extract -> exact bounded projection
    +-- query   -> local policy / optional Jev
                    |
                    +-- targeted evidence
                    +-- principal
                    +-- approved worker
                          |
                          +-- manual  -> fixed user model
                          +-- suggest -> recommendation only
                          +-- auto    -> bounded dynamic profile
~~~

I/O Delegation does not try to replace the main agent. Its job is to prepare the smallest useful evidence and avoid paying for model work when a parser, literal search, compiler, test or bounded read can answer the question directly.

## Host support

| Capability | Claude Code | Codex | Cursor |
| --- | --- | --- | --- |
| Global/user MCP gateway | Yes | Yes | Yes |
| search / extract / query | Yes | Yes | Yes |
| Optional read guard | Yes | Yes | Yes |
| Automatic compaction integration | Yes | Yes | Yes |
| Host-aware model policy | Principal-only boundary | Yes | Yes |
| CLI worker support | Existing adapter paths | Yes | Yes |
| Real authenticated live calibration | Not claimed | Yes | Not yet claimed |

Host-aware profile orchestration is intentionally focused on Codex and Cursor. Claude Code still receives the common gateway, read-control and compaction layers.

## Quick start

~~~bash
git clone https://github.com/pnll1991/io-delegation-skill.git
cd io-delegation-skill
~~~

Windows:

~~~powershell
.\io-delegation.cmd setup --project "D:\path\to\project"
~~~

macOS / Linux:

~~~bash
./io-delegation setup --project "/path/to/project"
~~~

Preview with zero writes:

~~~bash
io-delegation setup --project . --dry-run
~~~

Inspect the effective installation:

~~~bash
io-delegation status --project .
io-delegation doctor --project .
~~~

Setup installs a stable machine-local runtime under ~/.io-delegation/, gives the project a persistent identity, registers one host-level io_context MCP per supported client and keeps private state/configuration outside the repository being worked on.

Projects can move. Re-run setup after moving a project or changing the Python executable to refresh machine metadata without changing the project identity.

## Default behavior

A normal setup intentionally starts conservative:

| Setting | Default |
| --- | --- |
| Deterministic search/extract | On |
| query local routing | On |
| Experimental Jev route selector | Off |
| Model control | suggest |
| Model preset | balanced |
| Dynamic model switching | Off unless auto is selected |
| Automatic compaction | On |
| Read guard | Off |
| Legacy unconditional semantic auto-dispatch | Off |
| Sensitive operations | Principal |

Typical configuration:

~~~bash
io-delegation setup --project . --agent all
io-delegation setup --project . --model-mode suggest --model-preset balanced
~~~

Explicit alternatives:

~~~bash
# Never change an explicitly configured model
io-delegation setup --project . --model-mode manual

# Allow dynamic approved-profile selection
io-delegation setup --project . --model-mode auto --model-preset balanced

# Experimental Jev route selector
io-delegation setup --project . --jev on

# Persistent orchestration escape hatch
io-delegation setup --project . --orchestration off

# Optional read enforcement
io-delegation setup --project . --guard observe
io-delegation setup --project . --guard enforce

# Disable/restore automatic compaction
io-delegation setup --project . --compaction off
io-delegation setup --project . --compaction on
~~~

The presence of TYPESAFE_API_KEY does not silently enable the experimental Jev route selector.

## MCP surface

The public interface is intentionally small.

### search

Deterministic discovery within the approved project scope. Use it for literal search, path discovery and bounded excerpts.

### extract

Exact projections without inference. Supported projections include:

- HTML metadata such as title, h1, canonical and description;
- RFC 6901 JSON pointers;
- one-based line ranges;
- normalized character spans.

Oversized results are rejected instead of being silently truncated and treated as complete.

### query

The semantic context interface. It receives explicit selected fragments plus a question/operation hint and decides among:

- targeted evidence;
- principal reasoning;
- approved semantic worker.

query works without external services. Jev and worker execution are optional layers on top of the local gateway.

See [Context MCP reference](skills/io-delegation/references/CONTEXT_MCP.md).

## Model control: manual, suggest, auto

With an approved Codex/Cursor CLI worker, Jev can score task requirements while the actual model decision remains local and user-editable.

### manual

Never changes the model selected by the user.

For an explicit codex-cli/cursor-cli configuration, the configured model is preserved. A generic host-cli path without a fixed model falls back to the principal rather than inventing a selection.

### suggest — default

Computes a recommended approved profile but does not dynamically switch models.

This is the default because model choice remains visible and under user control while the policy can still surface a cheaper/minimum-sufficient recommendation.

### auto — explicit opt-in

May choose and retry approved worker profiles under local ceilings and escalation guards.

Auto is used by the live calibration harness because that benchmark specifically validates dynamic routing. It is not the product default.

## Calibrated model policy

Jev returns five scores:

- cheap_model_sufficient
- risk_high
- uncertainty_high
- reasoning_required
- parallelism_useful

Model strength uses cheap-model sufficiency, reasoning and risk.

Two important corrections from live calibration:

- uncertainty_high is diagnostic for missing/incomplete context; it does not independently buy a stronger model;
- parallelism_useful describes decomposition, not intelligence, so it also does not increase model strength.

If uncertainty indicates a context-gap problem, policy fails back to the principal instead of trying to solve missing evidence with a more expensive model.

Sensitive operations — debugging, architecture, security, editing and generation — remain principal operations.

### Codex default ladders

| Preset | Order |
| --- | --- |
| cost | Luna medium → Luna high → Terra medium → Sol medium → Astra low |
| balanced | Luna high → Terra medium → Sol medium → Astra low |
| quality | Luna high → Terra medium → Sol medium → Astra low |

Balanced deliberately starts at Luna high. Luna medium remains available in cost/custom configurations.

### Cursor default ladders

| Preset | Order |
| --- | --- |
| cost | Luna medium → Luna high → Sol medium |
| balanced | Luna medium → Luna high → Sol medium → Sol high |
| quality | Luna high → Sol medium → Sol high |

The Cursor registry is informed by CursorBench 4.0 metadata and remains user-editable. These Cursor defaults are not presented as live empirical validation from the Codex evidence.

### Escalation and Astra guards

Defaults:

- maximum model-profile escalations: 1;
- cost preset max one-step cost ratio: 2x;
- balanced: 10x;
- quality: 25x;
- transport/configuration/budget failures do not walk the ladder;
- principal fallback is not counted as a model escalation.

Balanced uses 10x because the live hard-factual calibration required the useful Luna-high → Terra-medium retry; a previous 4x guard incorrectly blocked that recovery.

Astra is exceptional, not a normal uncertainty fallback. The default Codex policy requires:

- demand >= 0.90;
- reasoning_required >= 0.85;
- and either risk_high >= 0.55 or cheap_model_sufficient <= 0.12.

The policy chooses the first approved profile whose declared capability covers demand. It no longer uses the old cost/capability objective that could over-reward expensive profiles.

See [ORCHESTRATION.md](skills/io-delegation/references/ORCHESTRATION.md).

## Automatic Jev-guided context compaction

Compaction is a separate subsystem from the optional Jev route selector.

~~~bash
# Default
io-delegation setup --project . --compaction on

# Persistent project escape hatch
io-delegation setup --project . --compaction off
~~~

| Host | Integration |
| --- | --- |
| Claude Code | Native session.compact replacement using the vendored fast-jev-compaction core; user/assistant text remains verbatim while old tool evidence can be pruned/truncated |
| Codex | Journals prompts/tool I/O locally; PreCompact runs the compaction decision and retained evidence is injected after native compaction |
| Cursor | Uses preCompact plus the first postToolUse/one bounded stop follow-up to re-inject retained evidence because Cursor's compact hook is observational |

If Jev or the API key is unavailable, compaction falls back to the host's native compaction path rather than blocking the session.

For the Codex/Cursor bridge, full tool-result bodies are kept machine-local. Jev receives result size/error metadata while exact retained evidence is re-injected locally when required.

## Optional read guard

The read guard is not a security sandbox and is off by default.

| Mode | Behavior |
| --- | --- |
| off / instructions only | No interception |
| observe | Evaluate covered reads and record decision metadata without blocking budget violations |
| enforce | Deny covered reads above policy budget and suggest bounded alternatives |

Default policy budget:

- 350 source lines;
- 64,000 bytes per inspected read/recognized shell batch.

Covered host integrations:

| Host | Hook |
| --- | --- |
| Claude Code | PreToolUse: Read, read_file, Bash |
| Codex | PreToolUse: Read, read_file, Bash |
| Cursor | preToolUse: Read, Shell |

The guard recognizes a bounded set of literal readers such as cat, head, tail, Get-Content and sed ranges. It does not interpret arbitrary shell programs or impose a session-wide token cap.

See [ENFORCEMENT.md](skills/io-delegation/references/ENFORCEMENT.md).

## Routing philosophy

| Situation | Preferred path |
| --- | --- |
| Search/parser/test/compiler can answer | Deterministic tool |
| Known function or small relevant region | Targeted read |
| Large factual lookup across selected text | query / bulk factual path |
| New repetitive file with clear contract | Reviewable code-write candidate |
| Debugging, architecture, security, payments, critical logic | Principal with direct evidence |
| Editing existing code | Re-read current source and edit precisely |
| Missing context | Acquire evidence or return to principal, not a stronger model by default |

The legacy bulk-read/code-write runner still exists for compatibility and explicit worker use. code-write produces a candidate under .io-delegation/candidates/; it does not silently overwrite live project files.

## Data and security boundaries

I/O Delegation separates three Jev-related paths because they have different data boundaries.

| Feature | Data sent to TypeSafe/Jev | Important boundary |
| --- | --- | --- |
| Route selection | Task text + aggregate metadata | No source bodies or file names |
| Model requirement scoring | Task text + aggregate metadata | Model IDs/providers are chosen locally |
| Compaction | Broader conversation/tool-input context | Codex/Cursor tool-result bodies remain local; result size/error metadata can be sent |

Other security properties:

- credentials remain environment variables;
- setup stores credential variable names, not plaintext values;
- private worker configuration belongs outside the project;
- project state/audits/runtime live under ~/.io-delegation/;
- generated host configuration is backed up before managed changes;
- restore refuses to overwrite later user edits unless explicitly forced;
- self-hosted Codex live validation is owner-only and checks out trusted main;
- live TypeSafe validation uses an encrypted handoff and does not intentionally persist the plaintext key.

The gateway is not a sandbox, DLP system or permission bypass. Host trust, approvals and sandboxes still apply.

## Benchmarks and evidence

There is no single "X% savings" claim. The repository keeps favorable and unfavorable results because each experiment measures a different layer.

### Evidence summary

| Evidence | Sample | Main observation | Product decision |
| --- | ---: | --- | --- |
| 0.1 independent token census | 5 cases | Large-file cold-skill arms used 64.03–72.00% fewer o200k tokens than whole-file input; small/already-focused controls became much worse | Targeted context is useful; bypass small/already-focused work |
| 0.1 local Qwen pilot | 9 main responses + forced-worker control | Large-file token consumption fell, but strict quality gate passed only 1/9; forced worker used 35.55% more total tokens than whole-file baseline | Do not claim quality-passing savings; deterministic/targeted paths first |
| Activation A/B 2026-09-17 | 15 pairs | Historical apparent improvement | Invalidated because runner inherited user Codex config; do not use as current evidence |
| Jev A/B 2026-09-18 | 20 pairs / 40 runs | Both arms 11/20 successes; Jev called in 9 pairs; no causal quality regressions in called subset | Keep Jev route selector explicit opt-in |
| Semantic worker validation | 25 pairs | ~97% median principal+worker token overhead, ~16.3s median wall overhead, 2/25 accepted worker responses | Unconditional semantic-worker auto-dispatch stays off |
| Codex live model calibration 2026-09-20 | 3 orchestration cases + direct profile probes | Easy/medium stayed Luna-high; hard factual recovered with one Luna-high → Terra-medium retry; no calibration case required Astra | suggest remains default; auto bounded to calibrated routing |

### 0.1 token census

Using tiktoken 0.11.0 on serialized message JSON:

| Case | o200k whole | o200k focused | o200k cold skill | Cold reduction vs whole |
| --- | ---: | ---: | ---: | ---: |
| Constants | 6,854 | 203 | 1,919 | 72.00% |
| Defaults | 6,862 | 752 | 2,468 | 64.03% |
| Candidate fields | 6,861 | 576 | 2,292 | 66.59% |
| Small installer | 772 | 638 | 2,354 | -204.92% |
| Already focused | 203 | 203 | 1,919 | -845.32% |

These are BPE counts for a controlled serialization, not provider billing and not quality measurements.

Evidence: [2026-09-07-census.json](benchmarks/results/2026-09-07-census.json).

### 0.1 local-model pilot

The controlled Qwen/Qwen2.5-Coder-1.5B-Instruct replay measured lower token consumption on large-file lookups, but the predeclared strict bare-JSON quality gate passed only 1 of 9 main-arm responses.

The forced-worker control used:

- 7,224 worker tokens;
- 2,132 fallback/main tokens;
- 9,356 combined tokens;
- versus 6,902 for the whole-file baseline;
- 35.55% more total token consumption.

That negative result is why worker calls are not treated as automatically cheaper.

Evidence: [2026-09-07-live.json](benchmarks/results/2026-09-07-live.json) and [benchmark methodology](benchmarks/README.md).

### Jev A/B — 2026-09-18

Completed isolated sample:

- 40 runs;
- 20 paired tasks;
- gateway-local: 11/20 successes;
- gateway-jev: 11/20 successes;
- Jev actually called in 9/20 pairs;
- five valid called pairs had a median principal-token delta of -23.29% and median wall-time delta of -27.61%;
- those five pairs were all from the targeted family;
- one causal quality gain;
- zero causal quality regressions among pairs where Jev was actually called;
- seven effective-route mismatches in the Jev arm overall;
- multi-file family never reached Jev.

The called subset is promising but too narrow to justify automatic activation. Therefore the route selector stays explicit opt-in.

Evidence: [jev-ab-20260918.json](benchmarks/v1-validation/evidence/jev-ab-20260918.json).

### Invalidated activation result

The 2026-09-17 activation artifact is intentionally retained but marked invalidated: the runner inherited user Codex configuration and did not pin the supported Windows sandbox fallback. Do not use its headline percentages as current product evidence.

Evidence: [activation-20260917.json](benchmarks/v1-validation/evidence/activation-20260917.json).

### Live Codex model-policy calibration — 2026-09-20

This is a real authenticated Codex CLI validation on the user's Windows self-hosted runner with TypeSafe/Jev available. The corpus is synthetic and sanitized; it contains no private project source.

Committed calibration fixture:

[model-policy-live-calibration-20260920.json](benchmarks/v1-validation/evidence/model-policy-live-calibration-20260920.json)

Latest secure post-merge rerun:

[GitHub Actions run 35515936634](https://github.com/pnll1991/io-delegation-skill/actions/runs/35515936634) on commit 7a911d8df89e857777b236d0749d1bb533d096c2.

Observed routing:

| Case | Jev raw tokens | Initial | Final | Model calls | Escalations | Worker raw tokens | Wall time |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: |
| easy | 861 | Luna high | Luna high | 1 | 0 | 12,095 | 28.0s |
| medium | 876 | Luna high | Luna high | 1 | 0 | 12,270 | 21.9s |
| hard factual | 895 | Luna high | Terra medium | 2 | 1 | 26,997 | 72.0s |

Effective benchmark policy:

- mode: auto;
- preset: balanced;
- order: Luna high → Terra medium → Sol medium → Astra low;
- max model escalations: 1;
- max escalation cost ratio: 10x.

The hard-factual case is the important calibration result: Luna high failed local evidence validation, one retry to Terra medium passed, and the ladder stopped there.

Direct one-shot profile probes remain noisy. In the latest rerun Luna high, Terra medium and Astra low passed while Sol medium failed the literal-evidence validator. Earlier runs produced different individual acceptance outcomes. The routing policy is therefore calibrated on invariants and bounded recovery behavior, not on a claim that one model always passes.

This live Codex evidence does not prove production savings and does not empirically validate Cursor.

## Tests and validation

Run the offline suite:

~~~bash
python -m unittest discover -s tests -v
~~~

CI runs across:

- Ubuntu;
- Windows;
- macOS;
- Python 3.10;
- Python 3.13;
- compaction tests/typechecks;
- token census checks.

The model-policy calibration is also locked by regression tests so historical Jev score patterns continue to:

- start balanced Codex at Luna high;
- avoid Astra on ordinary calibration cases;
- allow the single useful Luna-high → Terra-medium recovery;
- prevent full-ladder walking.

Automated tests do not replace authenticated real-host validation. The repository distinguishes protocol/unit coverage from live host evidence.

## Worker configuration

Real worker configs and credentials should stay outside the project.

Start from:

skills/io-delegation/assets/worker.host-cli.example.json

Then install it explicitly:

~~~bash
io-delegation setup --project . --worker-config /private/worker.host-cli.json
~~~

Disable a configured worker:

~~~bash
io-delegation setup --project . --no-worker
~~~

Advanced users can replace profile registries/order, block profiles, lower ceilings, change escalation limits and — for Cursor only — explicitly configure a reviewed native-router-first model string. I/O Delegation never invents model IDs.

## Lifecycle

Remove only managed project integration:

~~~bash
io-delegation remove --project . --dry-run
io-delegation remove --project .
~~~

Inspect backups:

~~~bash
io-delegation backups
~~~

Restore safely:

~~~bash
io-delegation restore BACKUP_ID --dry-run
io-delegation restore BACKUP_ID
~~~

Restore refuses to overwrite host configuration changed after I/O Delegation touched it unless --force is explicitly reviewed.

## Repository map

~~~text
io_gateway.py
  setup / status / doctor / remove / backups / restore

skills/io-delegation/
  SKILL.md
  scripts/
    context_mcp.py
    context_bootstrap.py
    context_query.py
    context_orchestrator.py
    model_policy.py
    context_telemetry.py
    read_guard.py
    io_delegate.py
  references/
    CONTEXT_MCP.md
    ORCHESTRATION.md
    ENFORCEMENT.md
    ADAPTERS.md
    WORKERS.md
    VALIDATION.md
  assets/
    worker.host-cli.example.json

scripts/
  codex_live_smoke.ps1
  codex_live_keygen.ps1
  codex_live_ab.py
  codex_live_ab_with_key.ps1
  codex_live_ab_from_comment.ps1

benchmarks/
  README.md
  results/
  v1-validation/evidence/

tests/
docs/
.github/workflows/
~~~

## Current limits

I/O Delegation is still evidence-driven beta software.

Do not infer more than the experiments support:

- no fixed percentage token-savings claim;
- no provider-billing savings claim;
- no claim that automatic worker delegation is always cheaper;
- no claim that Cursor has been live-calibrated from Codex evidence;
- no claim that the read guard is a security sandbox;
- no claim that an installed hook was invoked without a real-host trace;
- no claim that TypeSafe/Jev must be enabled for the gateway to be useful.

Remaining high-value validation includes a larger paired real-host sample with complete principal + worker + Jev accounting and authenticated Cursor/Claude lifecycle parity.

## Documentation

- [Product design](docs/PRODUCT_V1.md)
- [Installation](docs/INSTALLATION_V1.md)
- [Context Gateway / MCP](skills/io-delegation/references/CONTEXT_MCP.md)
- [Host-aware orchestration](skills/io-delegation/references/ORCHESTRATION.md)
- [Workers](skills/io-delegation/references/WORKERS.md)
- [Adapters](skills/io-delegation/references/ADAPTERS.md)
- [Read enforcement](skills/io-delegation/references/ENFORCEMENT.md)
- [Validation](skills/io-delegation/references/VALIDATION.md)
- [Benchmark methodology](benchmarks/README.md)
- [Changelog](CHANGELOG.md)

## License

MIT. See [LICENSE](LICENSE).
