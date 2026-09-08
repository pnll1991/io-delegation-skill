# Optional read enforcement · 0.2.0

This layer implements the missing pre-tool gate. The shared decision engine is
`scripts/read_guard.py`; host adapters only translate events and decisions.
The portable skill and the existing worker runner remain usable without hooks.
No model, API key, Portal instance, MCP server or third-party Python package is
required by the gate. It never launches a worker or executes the inspected command.

## Three distinct modes

| Mode | What happens |
| --- | --- |
| Instructions only | The agent decides whether to follow SKILL.md; no interception. |
| Observe | A connected hook evaluates requests and writes metadata to stderr, but does not deny budget violations. |
| Enforce | A connected hook denies covered reads exceeding the policy and supplies alternatives. |

An installed hook is **not** evidence that a particular client version invoked it.
`host_verified: false` in the installer output is deliberate. Only actual host
traces and blocked/allowed tool outcomes can establish that fact.

## Installation from the repository

First install the skill normally. Then opt in explicitly from the repository root:

```bash
python install_hooks.py --agent claude-code --project "/path/to/project" --dry-run
python install_hooks.py --agent claude-code --project "/path/to/project"
```

Replace `claude-code` with `codex` or `cursor`. Each host needs its own registration;
Codex and Cursor still share the skill itself. Use `--python python3` when that is
the executable visible to your client. Python 3.10+ is required for this layer.
Do not use a shell alias or paste extra flags into `--python`.

The installer writes a machine-local runtime under `.io-delegation-hooks/` and
merges only its entry into the existing host file. Other hooks and permission
settings are preserved. Before replacing a changed JSON configuration it keeps
an exact backup inside the ignored runtime directory. Installation is idempotent;
custom policies are preserved and a changed runtime is not overwritten silently.
Malformed JSON, symlinked installation paths and concurrent settings changes fail
rather than being replaced. The copied runtime/interpreter receives a preflight
check before the host configuration changes. This preflight is not a host test.

| Host | Project configuration | Event / matcher |
| --- | --- | --- |
| Claude Code | `.claude/settings.json` | `PreToolUse`: `Read`, `read_file`, `Bash` |
| Codex | `.codex/hooks.json` | `PreToolUse`: `Read`, `read_file`, `Bash` |
| Cursor | `.cursor/hooks.json` | `preToolUse`: `Read`, `Shell`; `failClosed: true` |

Commands contain absolute local paths so a nested session working directory does
not break resolution. They are machine-local: reinstall after moving/cloning the
project or changing machines. Do not commit installation backups or credentials.
Review host settings before sharing them. Trust/review the project and hook through
the client's normal interface; this installer does not grant trust, disable
sandboxes, bypass approvals, or start/restart the client. Restart or reload hooks
as required by that client. The standard skill installer remains unchanged and
does not enable hooks. Existing 0.1 skill copies need a reviewed manual update;
`install.py` intentionally refuses to overwrite them.

Remove only the exact registered entry, leaving unrelated edits intact:

```bash
python install_hooks.py --agent claude-code --project "/path/to/project" --remove
```

The shared runtime, policy and backups remain for other agents and recovery.
Changing the launcher requires removing the old entry before reinstalling.

## Common policy

Edit the reviewed project's `.io-delegation-hooks/policy.json`:

```json
{"version":1,"mode":"enforce","max_lines":350,"max_bytes":64000}
```

These are configurable source-text budgets, **not token counts or universal
optimal thresholds**. Exactly 350 lines is allowed; 351 is not. A minified file
can exceed the byte budget despite having only one line. Native `Read`/`read_file`
requests accept `file_path` or `path`, one-based `offset`, and positive `limit`.
A range is measured; the mere presence of an offset does not exempt it.

The shell adapter recognizes literal `cat`, `head`, `tail`, `less`, `more`, `type`,
`Get-Content`/`gc`, and `sed -n 'START,ENDp'` forms. It supports bounded head/tail,
`Get-Content -TotalCount`/`-Tail`, quoted paths, simple `cd ... &&` prefixes and
aggregate budgets across recognized files in one command. It never executes or
expands shell syntax. Unsupported options/expressions on recognized readers are
rejected. Shell parsing is deliberately limited, not an authorization parser.
Pipelines are conservative: a large upstream `cat` may be denied even when a later
filter would return little. Prefer a direct search or bounded native read instead.

The project root is the inspection scope. Out-of-project paths, non-regular files
and invalid covered requests are denied rather than inspected. Missing files are
left for the original tool to report. A local scan stops at 8 MB; requests needing
more inspection are rejected. Errors in our running handler deny even in observe
mode. Host handling of an unstarted, crashed or timed-out process is a separate
behavior, not something this script can guarantee.

On a budget denial, the agent is told to locate symbols, use an explicit range,
or invoke `bulk-read` with an already approved worker when useful. **It is never
forced to pay for a model call.** No-worker fallback remains targeted reading.
Code generation, edits and architectural decisions are not redirected by these
hooks. The worker runner's internal file reads do not create new host tool calls.

## Host protocol and coverage limits

Claude Code and Codex receive a `hookSpecificOutput` denial with
`hookEventName: PreToolUse`, `permissionDecision: deny` and a reason. Passing calls
produce no stdout, leaving normal permissions in place. Cursor receives its own
`permission` protocol with a denial reason. No adapter rewrites tool input or
approves an otherwise disallowed action.

**This is not a sandbox, DLP control, adversarial boundary or universal output cap.**
Unknown tools and arbitrary programs return `covered: false`; their execution is
left to the host. Search output, arbitrary scripts, terminal sessions already
running, hosted tools, editor attachments and context loaded without a covered
pre-tool event are not controlled. Repeated individually small reads are not a
session-wide budget. No MCP read schema is guessed; adding one requires explicit
normalization and tests. Do not interpret `pass` as proof that a tool was covered.

Cursor's generic `preToolUse` is used instead of `beforeReadFile`: the latter's
documented payload includes full content and lacks a requested range, which can
cause a size gate to block legitimate targeted reads. Only the registered names
are claimed covered. Some cloud exploration and version-specific paths may never
fire hooks. Claude's `@` attachments also do not trigger `PreToolUse`.

The handler writes only decision metadata (mode, code, coverage and counts) to
stderr: no source text, command, file path, environment, credentials or model
transcript. Hosts may expose/store stderr differently; no audit-retention promise
is made and success logging is not used as proof of host invocation.

## Test a real host before claiming enforcement

Use a disposable trusted project with no private code. Create one 1,000-line text
fixture and one 20-line file, install the skill and the matching hook, then start a
fresh session. Ask the agent to attempt **one full native read of the large file**,
stop on a denial (do not switch tools), and then request only the first 20 lines.
Separately test one literal full-file shell read and a small file. Inspect the
actual host tool events and denial reason, not just the agent's final statement.

Expected outcomes: full large read denied before tool output, bounded read allowed,
small read allowed. Save client version, OS, model, installed policy/code hashes,
hook configuration, event trace and outcomes. Repeat after client updates. A hook
script returning deny on stdin is only a protocol test, not this host test.

For an end-to-end token experiment, use identical tasks and clean snapshots with
(A) the normal agent, (B) the skill only, and (C) skill plus hooks. Add a same-tools,
no-skill control when evaluating a worker. Use a real separate worker context;
count all main/worker calls, cache, retries, review and corrections. Verify task
success with external tests. Include small tasks and already-focused baselines,
randomize paired runs, preserve failures, and use held-out tasks after tuning.
The current token pilot remains a **0.1 controlled replay**, not a measurement of
0.2 enforcement or autonomous clients. Do not transfer its percentages to hooks.

## Verification and sources

The new automated suite exercises the local engine, subprocess JSON protocols,
installation/rollback behavior, malformed input, ranges and negative controls.
It does not open an authenticated Claude Code, Codex or Cursor session. No new
end-to-end token savings have been measured for 0.2.

Reviewed 2026-09-08 against primary documentation:

- [Claude Code hook reference](https://code.claude.com/docs/en/hooks)
- [Codex hook reference](https://learn.chatgpt.com/docs/hooks)
- [Cursor hook reference](https://cursor.com/docs/hooks)
- [Original shunt implementation](https://github.com/sorantis/portal-ai-plugins/tree/add-shunt-claude/plugins/shunt)

Host-specific glue is optional. The common policy and worker contract stay
provider-neutral; the reference architecture's three layers are now represented,
without pretending every host or tool has the same enforcement coverage.
