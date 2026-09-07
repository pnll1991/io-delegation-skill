# I/O Delegation

### Keep the reasoning. Delegate the noise.

[![Offline tests](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml/badge.svg)](https://github.com/pnll1991/io-delegation-skill/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: optional](https://img.shields.io/badge/Python-3.10%2B%20optional-3776AB.svg)](docs/INSTALLATION.md)

**Claude Code · Codex · Cursor · any agent that can read Markdown**

[Español](README.es.md) · [The skill](skills/io-delegation/SKILL.md) · [Installation](docs/INSTALLATION.md) · [Adapters](skills/io-delegation/references/ADAPTERS.md)

A portable Agent Skill that keeps large factual reads and predictable file generation out of the main agent's context when delegation is useful. The main agent retains architecture, debugging, sensitive decisions, verification and integration.

**No required provider, subscription, MCP server, proprietary hook or agent-specific API.** Use the instructions alone, or add the optional standard-library Python runner and an explicitly approved worker.

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

Use `py -3` on Windows or `python3` where appropriate. The installer requires Python 3.10+, copies only the self-contained skill folder and refuses to overwrite an existing installation. Add `--dry-run` to preview the destination. Replace `--project "..."` with `--global` for a user-level installation.

| Agent | Project destination | Global destination |
| --- | --- | --- |
| Claude Code | `.claude/skills/io-delegation/` | `~/.claude/skills/io-delegation/` |
| Codex | `.agents/skills/io-delegation/` | `~/.agents/skills/io-delegation/` |
| Cursor | `.agents/skills/io-delegation/` | `~/.agents/skills/io-delegation/` |

Codex and Cursor share one installation. Do not run both commands against the same destination. Directory support is documented by [Claude Code](https://code.claude.com/docs/en/skills), [Codex](https://developers.openai.com/codex/skills) and [Cursor](https://cursor.com/docs/skills). Global installations apply to the local machine; remote/cloud agents need the skill available in their own environment.

### No Python? Copy the folder

Copy the complete `skills/io-delegation/` directory to the appropriate destination above. The instructions work without Python; only the installer and optional runner need it.

### Ask your agent to use it

> Apply the io-delegation skill to this task. Locate relevant files first. Use deterministic tools or targeted reads whenever they are sufficient. Delegate only to an available, approved worker with separate context. Verify evidence before making decisions. Without a worker, continue with targeted reading.

For an agent without skill discovery, give it the actual path to `SKILL.md` and ask it to read that file. Detailed instructions and reference guides are currently in Spanish. See [activation, updates and coexistence](docs/INSTALLATION.md).

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
  scripts/io_delegate.py   optional runner, no third-party dependencies
  references/              playbook, adapters, validation and sources
  assets/                  unapproved config examples and benchmark template
  LICENSE                  travels with the installed skill
install.py                 project/global installer
tests/                     84 offline infrastructure and contract tests
benchmarks/                reproducible census, model pilot and raw results
docs/                      installation, testing and maintainer guides
.github/                   CI, issue templates and pull-request checklist
```

## Tests and evidence

```bash
python -m unittest discover -s tests -v
```

The suite contains **84 offline tests**: 75 core tests and 9 benchmark-contract tests. GitHub Actions runs the suite on Linux, Windows and macOS with Python 3.10 and 3.13; the badge links to actual run status. [Testing scope](docs/TESTING.md) separates infrastructure validation from real-agent behavior and benchmarks.

## Benchmark

### Real local-model pilot · 2026-09-07

**Measured fewer tokens on large-file lookups, but not a quality-passing end-to-end win.** We ran `Qwen/Qwen2.5-Coder-1.5B-Instruct` on CPU against real repository files, comparing whole-file reading, focused reading without the skill, and focused reading with the **entire skill loaded**. This is a controlled prompt replay, not an autonomous Claude Code, Codex or Cursor benchmark.

Observed **input + output tokens**, including responses rejected by the quality gate:

| Case | Whole file | Focused, no skill | Focused + complete skill | Reduction vs whole file |
| --- | ---: | ---: | ---: | ---: |
| Runner constants | 6,902 | 240 | 2,132 | 69.11% |
| Configuration defaults | 6,908 | 791 | 2,697 | 60.96% |
| Small installer — negative control | 770 | 653 | 2,545 | -230.52% |

**Quality:** the predeclared gate required bare JSON. Only **1 of 9** main-arm responses passed: the focused/no-skill configuration lookup. Eight responses added Markdown fences, including all three skill responses. A separate **post-hoc** check found the correct field values in all nine after removing those fences; this does **not** turn the original failures into passes. No repair calls or their costs were measured.

**Delegation control:** the real `bulk-read` runner called the same model, which returned `insufficient_context` with no findings. The harness fell back to targeted reading. Worker + fallback consumed **9,356 tokens**, versus **6,902** for whole-file reading: **35.55% more**. The main request alone was smaller; total consumption was not. The fallback also failed the strict output-format gate.

**Interpretation:** targeted reading can reduce context substantially. A cold-loaded skill can be counterproductive on small tasks, and a strong focused/no-skill baseline is cheaper still. This pilot does not demonstrate automatic skill selection, successful code changes, provider billing savings, or a fixed percentage of savings for supported agents.

[Methodology, quality failures and reproduction](benchmarks/README.md) · [Raw model results](benchmarks/results/2026-09-07-live.json) · [Completed model run](https://github.com/pnll1991/io-delegation-skill/actions/runs/34154914478)

### Independent token census

A separate five-case census used `tiktoken` 0.11.0 on serialized message JSON. The three large-file cases showed **64.03–72.00%** fewer `o200k_base` tokens with the full skill included; small/already-focused negative controls increased token counts. These are BPE serialization counts, **not** the Qwen inference counts above, Claude tokenization, or provider invoices.

[All census results, including both tokenizers and negative controls](benchmarks/results/2026-09-07-census.json) · [Completed census run](https://github.com/pnll1991/io-delegation-skill/actions/runs/34155367336)

```bash
# Census only; no model download or inference
python -m pip install tiktoken==0.11.0
python benchmarks/run.py --output benchmark-output
```

The model pilot is an optional, manually triggered workflow; the lightweight census runs on relevant changes. Benchmark dependencies are separate from the installed skill. Count principal, worker, validation and rework using the [validation guide](skills/io-delegation/references/VALIDATION.md).

## Origin and contribution

Inspired by the Spotify Engineering article and the `shunt` design, adapted into an independent, provider-neutral workflow. [Sources and design differences](skills/io-delegation/references/SOURCES.md) document the mapping. Not affiliated with or endorsed by Spotify, Anthropic, OpenAI or Cursor.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for changes and [SECURITY.md](SECURITY.md) for trust boundaries. The runner is not a sandbox and file-name filtering is not comprehensive secret detection.

[MIT License](LICENSE) · Version 0.1.0 · Maintained in [pnll1991/io-delegation-skill](https://github.com/pnll1991/io-delegation-skill)
