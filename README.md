# I/O Delegation

**English** | [Español](README.es.md)

### Give coding agents the right context, not the whole repository.

[![Offline tests](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](docs/INSTALLATION.md)

**Claude Code · Codex · Cursor**

I/O Delegation is a context gateway for coding agents. It exposes three primary MCP tools: local `search`, exact `extract`, and smart `query`. The main agent keeps debugging, architecture, security and final edits. TypeSafe Jev routing remains optional; with an approved CLI worker, Jev can score model requirements for Codex and Cursor. Model control is user-first: `suggest` is the default, `manual` preserves the user's model, and dynamic switching requires explicit `auto`. Jev-guided context compaction is automatic by default.

```text
agent -> io_context -> search / extract / query
                                  |
                           local rules / Jev
                            /       |       \
                      targeted  principal  worker
```

The gateway still works without an external model. Jev routing and compute scoring receive task text plus aggregate metadata, not source bodies or file names. In the default `suggest` mode, local policy reports the minimum sufficient approved profile without changing the model. `manual` keeps an explicitly configured model. Only `auto` may select a profile dynamically, with hard ceilings, an Astra guard and bounded cost-aware escalation; transport failures fall back to the principal. Automatic compaction uses Jev when its configured API key is available and has the broader conversation/tool-input data boundary documented below; if Jev is unavailable, native host compaction remains the fallback.

## Quick start

Clone the repository, then run one setup command from this repository:

```bash
git clone https://github.com/pnll1991/io-delegation-skill.git
cd io-delegation-skill

# Windows
io-delegation.cmd setup --project "D:\\path\\to\\project"

# macOS / Linux
./io-delegation setup --project "/path/to/project"
```

Setup detects supported agents, installs the skill plus a stable project marker, installs a machine-local runtime under `~/.io-delegation/`, registers one global `io_context` MCP server per host, selects a safe project scope, keeps experimental Jev **routing off by default**, prepares **host-aware compute scoring when an approved worker is configured**, uses **suggest / balanced** as the model-control default, enables **automatic context compaction by default**, keeps the read guard **off by default**, and runs `doctor`.

Preview with zero writes, then inspect the installation at any time:

```bash
io-delegation setup --project . --dry-run
io-delegation status --project /path/to/project
io-delegation doctor --project /path/to/project
```

Project folders can be moved without changing their gateway identity. Rerun `setup` after a move or Python change to refresh metadata and host registration.

To configure specific hosts or behavior:

```bash
io-delegation setup --project . --agent codex
io-delegation setup --project . --agent all --jev on
io-delegation setup --project . --worker-config /private/worker.json
io-delegation setup --project . --model-mode suggest --model-preset balanced
io-delegation setup --project . --model-mode manual
io-delegation setup --project . --model-mode auto --model-preset balanced  # explicit dynamic switching
io-delegation setup --project . --orchestration off  # persistent project escape hatch
io-delegation setup --project . --guard enforce
io-delegation setup --project . --compaction off  # persistent escape hatch
```

`TYPESAFE_API_KEY` being present does not auto-enable the experimental Jev route selector. Use `--jev on` (or an explicitly reviewed `--router-config`) when you want that router. Compute orchestration is separate: with an approved `--worker-config`, setup defaults to `--orchestration auto`; a missing TypeSafe key simply falls back to T2/principal without dispatching the worker.

The older unconditional semantic-worker path still requires `"context_auto_dispatch": true`. Host-aware compute scoring is separate: `manual` never changes the configured model, `suggest` only returns a recommendation, and `auto` is the explicit opt-in that may select an approved profile. In `auto`, valid-but-incomplete evidence can retry only within the configured escalation-count and cost-ratio limits.

Real provider configs and credentials stay outside the project. Setup stores only environment-variable names for credentials. Codex uses a managed user config, Cursor uses a global MCP that resolves the active project from the current workspace, and Claude Code uses user-scope MCP registration when its CLI is available. See [V1 product design](docs/PRODUCT_V1.md) and [gateway usage](docs/CONTEXT_GATEWAY_USAGE.md). The older `install.py`, direct runner and compatibility tools remain available for manual/legacy setups.



Lifecycle commands are surgical and preserve unrelated settings:

```bash
io-delegation remove --project . --dry-run
io-delegation remove --project .
io-delegation backups
io-delegation restore BACKUP_ID --dry-run
```

Restore refuses to overwrite a config changed after I/O Delegation touched it unless `--force` is explicitly reviewed.
## Optional read enforcement

Version **0.2.0** adds the third layer that instructions alone cannot provide: a pre-tool gate. The common Python engine checks source-text budgets locally; small host adapters translate decisions into the documented Claude Code, Codex and Cursor hook formats.

| Mode | Behavior |
| --- | --- |
| Instructions only | Portable guidance; no tool interception. |
| Observe | Evaluate requests and record decision metadata without blocking budget violations. |
| Enforce | Deny covered reads over budget and suggest search, a bounded range or an approved worker. |

The V1.1 `io-delegation setup` command leaves this integration **off by default**. Enable `observe` or `enforce` explicitly. For the manual/legacy path, register it directly:

```bash
# Choose your agent: claude-code, codex, or cursor
python install_hooks.py --agent claude-code --project "/path/to/your/project" --mode observe --dry-run
python install_hooks.py --agent claude-code --project "/path/to/your/project" --mode observe
```

Use `--python python3` when that is the executable available to the host. Unlike the shared skill folder, **each host needs its own hook registration**. The default skill installer does not enable hooks.

| Host | Hook configuration | Covered event names |
| --- | --- | --- |
| Claude Code | `.claude/settings.json` | `PreToolUse`: `Read`, `read_file`, `Bash` |
| Codex | `.codex/hooks.json` | `PreToolUse`: `Read`, `read_file`, `Bash` |
| Cursor | `.cursor/hooks.json` | `preToolUse`: `Read`, `Shell` |

The initial policy allows up to **350 source lines and 64,000 bytes** per inspected read/recognized shell batch. It checks minified files by bytes, verifies requested ranges, and does not treat an offset alone as a size exemption. Recognized literal shell readers include `cat`, `head`, `tail`, `Get-Content` and bounded `sed`. A denial never starts a model or changes providers: targeted reading works without a worker.

The installer preserves other settings and hooks, backs up changed configuration, performs a local runtime preflight, supports `--dry-run` and `--remove`, and refuses silent replacement of a modified runtime. Policy lives in `.io-delegation-hooks/policy.json`; set `mode` to `observe` to audit instead of blocking budget violations. The hook runtime uses machine-local paths, while the Context Gateway project identity survives project moves. Rerun `setup` after moving a project only to refresh stored metadata or after changing Python. Normal host trust and approvals remain required.

**Coverage is deliberately bounded.** Arbitrary scripts, unrecognized/MCP tools, search output, editor attachments and context paths that do not fire the registered hook are not controlled. Pipelines are conservative; this is not a general shell interpreter, security sandbox or session-wide token cap. It does not enforce code generation. An installed hook is not proof that a specific client invoked it: the installer reports `host_verified: false` until a real-host test is performed.

[Policy, supported forms, removal and real-host verification](skills/io-delegation/references/ENFORCEMENT.md) · [Hook tests](tests/test_read_guard.py)


## Host-aware Jev model orchestration

With an approved CLI worker, Jev scores task requirements while a **local, editable policy** chooses the model. The default is `balanced`; users can switch to `cost` or `quality` without editing JSON.

```text
bulk factual candidate
        ↓
Jev requirement scores
        ↓
local model policy
   ┌────┴──────────────────────────────┐
Codex                                 Cursor
Luna medium                           Luna medium
Luna high                             Luna high
Terra medium                          Sol medium
Sol medium                            Sol high
Astra low  ← default hard ceiling
```

The selector does not blindly start at the cheapest model. It estimates normalized task demand, filters profiles above that demand and below the user's ceiling, then minimizes a preset-specific cost/capability objective. A difficult task can therefore start directly at Sol or Astra. A valid-but-incomplete answer may escalate to the next approved profile; timeouts, transport errors and unknown usage go directly back to the principal.

The defaults are host-specific. Codex uses current OpenAI positioning/pricing and caps at **Astra low**. Cursor uses CursorBench 4.0 efficiency data; its balanced ladder skips Terra because Luna-high had better benchmark score at much lower reported task cost. Astra is not in the default Cursor registry because the current Cursor model/benchmark data used for this policy does not list it.

```bash
io-delegation setup --project . \
  --worker-config /private/worker.host-cli.json \
  --model-preset balanced

# Other project-scoped presets
io-delegation setup --project . --model-preset cost
io-delegation setup --project . --model-preset quality
```

Advanced users can replace the registry/order, lower or raise ceilings, block profiles, bound escalations, and explicitly opt into Cursor's native Router with an exact reviewed model string. Copy `skills/io-delegation/assets/worker.host-cli.example.json` as a starting point. Jev never invents model IDs.

Telemetry records host, demand, selected profile/model/effort, attempts, escalations and reported worker/Jev usage. Unknown usage remains unknown. See [host-aware orchestration](skills/io-delegation/references/ORCHESTRATION.md) for defaults, research references and the full schema.

## Automatic Jev-guided context compaction

Claude Code, Codex and Cursor use the same project-scoped compaction policy. `setup`
enables the compaction adapters automatically for the installed hosts; there is no
manual compaction command to remember. Claude can proactively compact at the configured
context threshold (60% by default), while Codex and Cursor attach to their host-native
automatic compaction lifecycle.

```bash
# Automatic compaction is the default
io-delegation setup --project . --agent all

# Persistent per-project escape hatch
io-delegation setup --project . --compaction off

# Restore automatic compaction
io-delegation setup --project . --compaction on
```

An explicit `--compaction off` preference persists across later `setup` runs. Legacy
project states that never stored a compaction preference migrate to automatic compaction
on their next setup/update.

The decision model is shared, while the host adapter matches the lifecycle each
client actually exposes:

| Host | Integration |
| --- | --- |
| Claude Code | Native `session.compact` replacement using the vendored `fast-jev-compaction` core. User/assistant text stays verbatim; Jev prunes or truncates old tool evidence. |
| Codex | Command hooks journal prompts/tool I/O locally. `PreCompact` runs Jev, native compaction proceeds, then `SessionStart(source=compact)` injects the retained verbatim tool evidence before the next model request. |
| Cursor | Command hooks journal prompts/tool I/O locally. `preCompact` runs Jev; because Cursor's hook is observational, retained evidence is injected by the first `postToolUse` after compaction or by one bounded `stop` follow-up if no tool call occurs. |

This is intentionally separate from `--jev on`. The normal router sends task
text plus aggregate corpus metadata; compaction has a broader data boundary and
sends conversation text plus tool inputs to TypeSafe. **Full tool-result bodies
are not sent to Jev** in the Codex/Cursor bridge: Jev sees only result
size/error metadata, while the exact result stays machine-local and is
re-injected only when Jev says it should survive compaction.

The ignored `.io-delegation/compaction.json` policy records the configured hosts
and data scope. The API key remains in `TYPESAFE_API_KEY` (or the
`--typesafe-env` variable). Outside configured projects the adapters are inert;
`--compaction off` disables them for that project. A missing key or any Jev/adapter
failure falls back to the host's native compaction rather than blocking the session.

## What it does

| Situation | Preferred route |
| --- | --- |
| A search, parser or test answers the question | Deterministic tool |
| Known function or small relevant section | Targeted direct read |
| Focused factual question across substantial text | `bulk-read`, when worthwhile |
| New repetitive file with a real reference and clear contract | `code-write`, as a reviewable candidate |
| Debugging, architecture, payments or critical logic | Main-agent reasoning with direct evidence |
| Editing existing code | Re-read the current source and edit precisely |

`bulk-read` returns bounded findings, literal evidence, source hashes and declared coverage. The runner checks quotes and computes their locations; the main agent still verifies their meaning.

`code-write` creates `.io-delegation/candidates/<target>`, not a live project file. It never executes, applies or overwrites the candidate automatically.

## Try the runner without a model

From this repository, these commands use files that already exist and make no model calls:

```bash
python skills/io-delegation/scripts/io_delegate.py inspect --root . --paths install.py
python skills/io-delegation/scripts/io_delegate.py bulk-read --root . --paths install.py --question "Which directories can the installer write to?" --dry-run
```

For real delegation, follow [ADAPTERS.md](skills/io-delegation/references/ADAPTERS.md). Supported transports include reviewed Codex/Cursor host CLIs, dedicated Codex/Cursor CLI workers, a compatible Chat Completions endpoint, or a reviewed command using JSON over stdin/stdout. Worker inference still requires an approved external configuration. Never publish real worker configuration or credentials.

## Repository map

```text
skills/io-delegation/
  SKILL.md                 portable decision workflow
  scripts/io_delegate.py   optional worker runner
  scripts/read_guard.py    common read policy and host protocol adapters
  references/              playbook, enforcement, adapters, validation and sources
  assets/                  unapproved config examples and benchmark template
  LICENSE                  travels with the installed skill
install.py                 project/global skill installer; no hooks enabled
install_hooks.py           optional project hook registration and removal
tests/                     infrastructure, benchmark and hook contract tests
benchmarks/                reproducible census, model pilot and raw results
docs/                      installation, testing and maintainer guides
.github/                   CI, issue templates and pull-request checklist
```

## Tests and evidence

```bash
python -m unittest discover -s tests -v
# Only the new read gate and installer tests:
python -m unittest discover -s tests -p test_read_guard.py -v
```

Version 0.2 adds **51 tests** for budgets, ranges, shell forms, negative controls, malformed inputs, subprocess hook protocols, installation, backups and removal. The existing core and benchmark tests remain. GitHub Actions runs the suite on Linux, Windows and macOS with Python 3.10 and 3.13; the badge links to actual run status.

These tests exercise our real scripts with synthetic host events. **They do not open authenticated Claude Code, Codex or Cursor sessions or prove token savings.** The [real-host checklist](skills/io-delegation/references/ENFORCEMENT.md#test-a-real-host-before-claiming-enforcement) explains the separate validation step. [Earlier testing scope](docs/TESTING.md) documents the original infrastructure and model pilot.

## Benchmark

### Historical 0.1 local-model pilot · 2026-09-07

**These measurements predate the 0.2 read hooks. They are not a benchmark of enforcement or autonomous agents.** No new end-to-end token savings are claimed for 0.2.

**Measured fewer tokens on large-file lookups, but not a quality-passing end-to-end win.** We ran `Qwen/Qwen2.5-Coder-1.5B-Instruct` on CPU against real repository files, comparing whole-file reading, focused reading without the skill, and focused reading with the **entire 0.1 skill loaded**. This is a controlled prompt replay, not an autonomous Claude Code, Codex or Cursor benchmark.

Observed **input + output tokens**, including responses rejected by the quality gate:

| Case | Whole file | Focused, no skill | Focused + complete 0.1 skill | Reduction vs whole file |
| --- | ---: | ---: | ---: | ---: |
| Runner constants | 6,902 | 240 | 2,132 | 69.11% |
| Configuration defaults | 6,908 | 791 | 2,697 | 60.96% |
| Small installer — negative control | 770 | 653 | 2,545 | -230.52% |

**Quality:** the predeclared gate required bare JSON. Only **1 of 9** main-arm responses passed: the focused/no-skill configuration lookup. Eight responses added Markdown fences, including all three skill responses. A separate **post-hoc** check found the correct field values in all nine after removing those fences; this does **not** turn the original failures into passes. No repair calls or their costs were measured.

**Delegation control:** the real `bulk-read` runner called the same model, which returned `insufficient_context` with no findings. The harness fell back to targeted reading. Worker + fallback consumed **9,356 tokens**, versus **6,902** for whole-file reading: **35.55% more**. The main request alone was smaller; total consumption was not. The fallback also failed the strict output-format gate.

**Interpretation:** targeted reading can reduce context substantially. A cold-loaded skill can be counterproductive on small tasks, and a strong focused/no-skill baseline is cheaper still. This pilot does not demonstrate automatic skill selection, successful code changes, provider billing savings, or a fixed percentage of savings for supported agents.

[Methodology, quality failures and reproduction](benchmarks/README.md) · [Raw model results](benchmarks/results/2026-09-07-live.json) · [Completed model run](https://github.com/pnll1991/io-delegation-skill/actions/runs/34154914478)

### Independent token census

A separate five-case census used `tiktoken` 0.11.0 on serialized message JSON. The three large-file cases showed **64.03–72.00%** fewer `o200k_base` tokens with the full 0.1 skill included; small/already-focused negative controls increased token counts. These are BPE serialization counts, **not** the Qwen inference counts above, Claude tokenization, or provider invoices.

[All census results, including both tokenizers and negative controls](benchmarks/results/2026-09-07-census.json) · [Completed census run](https://github.com/pnll1991/io-delegation-skill/actions/runs/34155367336)

```bash
# Census only; no model download or inference
python -m pip install tiktoken==0.11.0
python benchmarks/run.py --output benchmark-output
```

A run on current main counts the current skill. To reproduce the historical numbers, check out the report's `executed_commit`, as explained in the benchmark guide. The model pilot is an optional, manually triggered workflow; the lightweight census runs on relevant changes. Benchmark dependencies are separate from the installed skill. Count principal, worker, validation and rework using the [validation guide](skills/io-delegation/references/VALIDATION.md).

## Origin and contribution

Inspired by the Spotify Engineering article and the `shunt` design, adapted into an independent, provider-neutral workflow. [Sources and design differences](skills/io-delegation/references/SOURCES.md) document the mapping. Not affiliated with or endorsed by Spotify, Anthropic, OpenAI or Cursor.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for changes and [SECURITY.md](SECURITY.md) for trust boundaries. The runner and read gate are not sandboxes; file-name filtering is not comprehensive secret detection.

[MIT License](LICENSE) · Version 1.0.0-beta · Maintained in [pnll1991/io-delegation-skill](https://github.com/pnll1991/io-delegation-skill)
