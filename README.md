# I/O Delegation

**English** | [Español](README.es.md)

### Keep the reasoning. Delegate the noise.

[![Offline tests](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: optional](https://img.shields.io/badge/Python-3.10%2B%20optional-3776AB.svg)](docs/INSTALLATION.md)

**Claude Code · Codex · Cursor · any agent that can read Markdown**

[The skill](skills/io-delegation/SKILL.md) · [Installation](docs/INSTALLATION.md) · [Read enforcement](#optional-read-enforcement) · [Benchmark](#benchmark) · [Adapters](skills/io-delegation/references/ADAPTERS.md)

A portable Agent Skill that keeps large factual reads and predictable file generation out of the main agent's context when delegation is useful. The main agent retains architecture, debugging, sensitive decisions, verification and integration.

**No required provider, subscription, MCP server, proprietary hook or agent-specific API.** Use the instructions alone, or add the optional standard-library Python runner and an explicitly approved worker. Version 0.2 adds optional pre-tool read gates without making host-specific hooks a requirement of the skill.

```text
                    task
                     |
              locate + classify
                     |
       +-------------+---------------+
       |             |               |
 deterministic   factual read    repetitive file
 tool / range      worker            worker
       |             |               |
       |       facts + evidence   isolated candidate
       +-------------+---------------+
                     |
            main agent verifies,
          reasons, edits and integrates
```

## Quick start

Clone the repository, or download it using GitHub's **Code > Download ZIP**:

```bash
git clone https://github.com/pnll1991/io-delegation-skill.git
cd io-delegation-skill
```

Choose **one** installation command for an existing project:

```bash
# Claude Code
python install.py --agent claude-code --project "/path/to/your/project"

# Codex
python install.py --agent codex --project "/path/to/your/project"

# Cursor
python install.py --agent cursor --project "/path/to/your/project"
```

Use `py -3` on Windows or `python3` where appropriate. The installer requires Python 3.10+, copies only the self-contained skill folder and refuses to overwrite an existing installation. Add `--dry-run` to preview the destination. Replace `--project "..."` with `--global` for a user-level installation. Existing 0.1 copies require a reviewed manual update; no user customization is silently overwritten.

| Agent | Project destination | Global destination |
| --- | --- | --- |
| Claude Code | `.claude/skills/io-delegation/` | `~/.claude/skills/io-delegation/` |
| Codex | `.agents/skills/io-delegation/` | `~/.agents/skills/io-delegation/` |
| Cursor | `.agents/skills/io-delegation/` | `~/.agents/skills/io-delegation/` |

Codex and Cursor share one installation. Do not run both commands against the same destination. Directory support is documented by [Claude Code](https://code.claude.com/docs/en/skills), [Codex](https://developers.openai.com/codex/skills) and [Cursor](https://cursor.com/docs/skills). Global installations apply to the local machine; remote/cloud agents need the skill available in their own environment.

### No Python? Copy the folder

Copy the complete `skills/io-delegation/` directory to the appropriate destination above. The instructions work without Python; the installers, optional runner and optional read gate need Python.

### Ask your agent to use it

> Apply the io-delegation skill to this task. Locate relevant files first. Use deterministic tools or targeted reads whenever they are sufficient. Delegate only to an available, approved worker with separate context. Verify evidence before making decisions. Without a worker, continue with targeted reading.

For an agent without skill discovery, give it the actual path to `SKILL.md` and ask it to read that file. The core instructions and most reference guides are in Spanish. See [activation, updates and coexistence](docs/INSTALLATION.md).

## Optional read enforcement

Version **0.2.0** adds the third layer that instructions alone cannot provide: a pre-tool gate. The common Python engine checks source-text budgets locally; small host adapters translate decisions into the documented Claude Code, Codex and Cursor hook formats.

| Mode | Behavior |
| --- | --- |
| Instructions only | Portable guidance; no tool interception. |
| Observe | Evaluate requests and record decision metadata without blocking budget violations. |
| Enforce | Deny covered reads over budget and suggest search, a bounded range or an approved worker. |

After installing the skill, explicitly enable the optional project-local integration:

```bash
# Choose your agent: claude-code, codex, or cursor
python install_hooks.py --agent claude-code --project "/path/to/your/project" --dry-run
python install_hooks.py --agent claude-code --project "/path/to/your/project"
```

Use `--python python3` when that is the executable available to the host. Unlike the shared skill folder, **each host needs its own hook registration**. The default skill installer does not enable hooks.

| Host | Hook configuration | Covered event names |
| --- | --- | --- |
| Claude Code | `.claude/settings.json` | `PreToolUse`: `Read`, `read_file`, `Bash` |
| Codex | `.codex/hooks.json` | `PreToolUse`: `Read`, `read_file`, `Bash` |
| Cursor | `.cursor/hooks.json` | `preToolUse`: `Read`, `Shell` |

The initial policy allows up to **350 source lines and 64,000 bytes** per inspected read/recognized shell batch. It checks minified files by bytes, verifies requested ranges, and does not treat an offset alone as a size exemption. Recognized literal shell readers include `cat`, `head`, `tail`, `Get-Content` and bounded `sed`. A denial never starts a model or changes providers: targeted reading works without a worker.

The installer preserves other settings and hooks, backs up changed configuration, performs a local runtime preflight, supports `--dry-run` and `--remove`, and refuses silent replacement of a modified runtime. Policy lives in `.io-delegation-hooks/policy.json`; set `mode` to `observe` to audit instead of blocking budget violations. Installation uses machine-local paths: reinstall after moving the project. Normal host trust and approvals remain required.

**Coverage is deliberately bounded.** Arbitrary scripts, unrecognized/MCP tools, search output, editor attachments and context paths that do not fire the registered hook are not controlled. Pipelines are conservative; this is not a general shell interpreter, security sandbox or session-wide token cap. It does not enforce code generation. An installed hook is not proof that a specific client invoked it: the installer reports `host_verified: false` until a real-host test is performed.

[Policy, supported forms, removal and real-host verification](skills/io-delegation/references/ENFORCEMENT.md) · [Hook tests](tests/test_read_guard.py)

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

For real delegation, follow [ADAPTERS.md](skills/io-delegation/references/ADAPTERS.md). Supported transports are a compatible Chat Completions endpoint or a reviewed command using JSON over stdin/stdout. Nothing is enabled by default. Never publish real worker configuration or credentials.

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

[MIT License](LICENSE) · Version 0.2.0 · Maintained in [pnll1991/io-delegation-skill](https://github.com/pnll1991/io-delegation-skill)
