# Token benchmark

## What this measures

This is a **controlled prompt-replay pilot on real repository source**, not an autonomous-agent evaluation. The harness preselects relevant Python symbols with `ast`, assembles the model inputs, and checks the generated answers against fixed JSON expectations. It does not ask Claude Code, Codex or Cursor to discover the files or decide when to use this skill.

The measured route is **targeted reading**, a first-class part of I/O Delegation. A separate forced-worker control exercises the real `bulk-read` runner. For these simple symbol lookups the skill would prefer a deterministic tool, so forcing delegation is deliberately a negative control, not a recommended strategy.

## Experimental arms

| Arm | Input presented to the main model |
| --- | --- |
| `whole_file` | Question and the complete relevant source file; no skill |
| `focused_no_skill` | Same question and predeclared, exact source ranges; no skill |
| `skill_cold` | Same focused ranges plus the **entire SKILL.md**, loaded in that request |
| `forced_worker` | Real worker request through `io_delegate.py`, then validated summary, source re-read for verification, and the entire skill in the main request; fallback is counted if needed |

The already-focused control makes the baseline identical to `focused_no_skill`. The small-installer control uses a real small file, with no irrelevant padding. All arms use the same source revision, questions and acceptance criteria. Answer keys are used only by the scorer, never included in prompts.

The strong baseline matters: if direct focused reading already works, adding a skill does not make those same excerpts shorter. A reduction against whole-file reading demonstrates the benefit of the reading strategy, not a unique compression property of this Markdown file.

## Two separate measurements

**Token census.** `tiktoken` 0.11.0 tokenizes the exact serialized message JSON using `o200k_base` and `cl100k_base`. These are actual BPE counts for that serialization, not byte estimates. They are **not** a provider invoice, ChatML overhead measurement, Claude tokenizer, or evidence of model accuracy. The evidence-only number deliberately excludes the skill and shared instructions; the cold number includes them.

**Local-model pilot.** The harness runs the same model weights in all main and worker calls, locally on a GitHub-hosted CPU runner. It records actual input tensor length **after the model's chat template** and generated token IDs, including the end-of-sequence token. Inputs are never silently truncated. No cross-request KV/prefix cache is reused. The model stays loaded; one recorded warm-up is excluded from paired totals. There is no hosted inference API or monetary-cost claim.

The primary result counts the main request's **input plus output tokens**. The forced-worker total additionally counts its input and output, even when validation fails. Model-call times exclude dependency installation, weight downloads, model loading, deterministic selection and orchestration; they are descriptive timings, not end-to-end workflow benchmarks.

## Cases and quality gate

Five census cases cover constants, configuration defaults, candidate-file status, the small installer, and already-focused reading. The first, second and fourth also run inference. The corpus is this repository's original runner and installer, not a synthetic file padded to inflate savings.

The live quality gate requires exactly the expected JSON keys, values and types. Extra prose, extra keys, wrong values and incomplete answers fail. There are no repair retries or best-of-N selection. The pilot uses one observation per case/arm and a seeded execution order: it provides no confidence intervals or claim of generalization.

The forced-worker control uses `runner.main`, its HTTP adapter, current-source hash checks and the existing literal-evidence validator. It also checks that all requested constants are covered before accepting the summary. The main receives original source excerpts as well, because literal quote validation is not semantic verification. Generated code is never executed in this benchmark.

## Published run · 2026-09-07

[Raw model report](results/2026-09-07-live.json) · [Raw census](results/2026-09-07-census.json) · [Model workflow and logs](https://github.com/pnll1991/io-delegation-skill/actions/runs/34154914478) · [Census workflow](https://github.com/pnll1991/io-delegation-skill/actions/runs/34155367336)

Model revision: `2e1fd397ee46e1388853d2af2c993145b0f1098a`; harness commit: `e879ebd5e05a0c5b1610db9e9a0547a64547574d`. Python 3.11.16, Transformers 4.56.2, Torch 2.8.0+cpu, float32, four CPU threads, greedy decoding, one observation per arm/case. The source-file hashes are in both reports.

| Case / arm | Input | Output | Total | Model-call seconds | Strict JSON pass |
| --- | ---: | ---: | ---: | ---: | --- |
| Constants / whole file | 6,860 | 42 | 6,902 | 193.072 | No |
| Constants / focused, no skill | 198 | 42 | 240 | 14.449 | No |
| Constants / focused + cold skill | 2,090 | 42 | 2,132 | 49.203 | No |
| Defaults / whole file | 6,868 | 40 | 6,908 | 181.068 | No |
| Defaults / focused, no skill | 765 | 26 | 791 | 18.992 | Yes |
| Defaults / focused + cold skill | 2,657 | 40 | 2,697 | 61.221 | No |
| Small installer / whole file | 738 | 32 | 770 | 20.534 | No |
| Small installer / focused, no skill | 621 | 32 | 653 | 18.369 | No |
| Small installer / focused + cold skill | 2,513 | 32 | 2,545 | 55.212 | No |

### Quality failures are retained

The original strict gate passed **1/9**, not 9/9. Eight responses wrapped correct-looking JSON in Markdown despite the instruction to emit bare JSON. Therefore the 60.96–69.11% token reductions on the large-file inputs are **observed consumption changes, not quality-passing task savings**. No repair work was run or counted; a cost-to-success comparison remains unknown.

As a separately labeled **post-hoc diagnostic**, removing a single outer ` ```json ` fence made all nine field-value objects match the answer keys. This diagnoses formatting rather than wrong lookup values; it does not retroactively relax the primary acceptance criterion. Reproduce that diagnostic without a model:

```python
import importlib.util
import json
import re
from pathlib import Path

spec = importlib.util.spec_from_file_location("bench", "benchmarks/run.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
report = json.loads(Path("benchmarks/results/2026-09-07-live.json").read_text())
gold = {case["id"]: case["expected"] for case in bench.cases()}
rows = report["live"]["rows"]
print("Original strict passes:", sum(row["passed"] for row in rows))
normalized = [re.sub(r"^```json\n(.*)\n```$", r"\1", row["output"], flags=re.S) for row in rows]
print("Post-hoc value matches:", sum(bench.score(text, gold[row["case"]]) for text, row in zip(normalized, rows)))
```

### The forced worker did not help

The real runner returned exit code 0 with a valid **`insufficient_context`** response, not usable findings. The model claimed the public source contained sensitive information; this was its generated explanation, not a verified finding about the file. The harness correctly selected a targeted-read fallback rather than treating the response as successful extraction.

The worker used **7,149 input + 75 output = 7,224 tokens**. The fallback used **2,090 + 42 = 2,132**. Combined consumption was **9,356**, or **35.55% more** than the 6,902-token whole-file baseline. The fallback retained the same Markdown-format failure. This control demonstrates why main-context reduction must not be presented as total savings; it does not demonstrate successful bulk extraction by this model.

### Census, including unfavorable cases

The independent census completed at harness commit `1b071cf87e2da326492af58127e42a6e5655bdc6`, with unchanged source/skill hashes. Counts below are serialized-message BPE tokens, not the inference tensor counts above.

| Case | o200k whole | o200k focused | o200k cold skill | Cold reduction | cl100k cold reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| Constants | 6,854 | 203 | 1,919 | 72.00% | 69.10% |
| Defaults | 6,862 | 752 | 2,468 | 64.03% | 61.12% |
| Candidate fields | 6,861 | 576 | 2,292 | 66.59% | 63.55% |
| Small installer | 772 | 638 | 2,354 | -204.92% | -235.35% |
| Already focused | 203 | 203 | 1,919 | -845.32% | -983.50% |

### Reproducibility and harness maintenance

The committed reports preserve the completed run's original outputs and flags. Full prompts are also in the workflow's `benchmark-results` artifact (ID `10030948975`, ZIP SHA-256 `5c89ddc6854a973b12d27c6547c5e20bc7264a0ffaca9068eabdc09a7a71f2b7`); unlike the committed reports, that artifact expires after 90 days. Prompts can be reconstructed from the recorded harness commit and checked against their `messages_sha256` values.

After the run, the extra benchmark-only assignment checker was fixed to recognize Python numeric separators (`6_000`) instead of matching decimal substrings; a regression test was added. That check was **not reached** in the recorded forced-worker case because its status was already `insufficient_context`, so the fix does not alter these results. The current harness also serializes local model calls if an HTTP request times out, and its execution-order comment now correctly says seeded rather than counterbalanced. The original `executed_commit` remains the exact reproduction target; there is no claim that the patched harness has another completed live run.

## Reproduce

To rerun the optional model pilot, use **Actions → Controlled token benchmark → Run workflow**. The workflow needs only `contents: read`, does not persist checkout credentials, and uses no inference secrets. Model weights and benchmark dependencies are not part of the installed skill.

For the census only:

```bash
python -m pip install tiktoken==0.11.0
python benchmarks/run.py --output benchmark-output/census
```

For the local-model pilot, use Python 3.11 in a separate environment:

```bash
python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r benchmarks/requirements.txt
python benchmarks/run.py --live --repeat 1 --output benchmark-output/live
```

Use `--revision` with the model commit in the published report to reproduce the exact weights. Check out the report's `executed_commit` to reproduce the harness and corpus. `--repeat 2` or `--repeat 3` repeats the main arms; the forced-worker control runs once. The default model is `Qwen/Qwen2.5-Coder-1.5B-Instruct`. It resolves `main` once and pins tokenizer and weights to that resolved revision for the run.

`report.json` contains all scored outputs and token counts. `traces/` contains exact messages and hashes; GitHub uploads these, plus partial progress on failure, as `benchmark-results` with a 90-day retention period. Published JSON results are kept in Git independently of that temporary artifact. Top-level dependencies are pinned; runner hardware and transitive dependencies can change, so do not expect bit-identical timings.

## Interpretation limits

This pilot does not establish automatic skill invocation, superior reasoning, correctness of code changes, prompt-injection resistance, the benefit of a cheaper/different worker model, lower monetary cost, or performance on large production repositories. It is not a measured reduction for Claude Code, Codex or Cursor. Their compatibility remains an installation/procedure claim, not a benchmark result.

For these lookup tasks an ordinary parser can sometimes answer without any model call. That is consistent with the skill's deterministic-first rule and is a stronger baseline than forcing an LLM to answer every question.

References: [tiktoken](https://github.com/openai/tiktoken), [Qwen model](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct), [validation policy](../skills/io-delegation/references/VALIDATION.md).
