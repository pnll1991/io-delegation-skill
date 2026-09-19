# Codex live Windows runner

This repository uses a dedicated self-hosted Windows runner only for owner-triggered
live Codex tests.

Security boundary:

- the workflow is triggered only by the exact owner command on issue #31, or by a
  manual workflow dispatch from a repository writer;
- public pull-request/fork events never target the self-hosted runner;
- the job checks out trusted `main` only;
- checkout credentials are not persisted;
- the runner needs the custom label `codex-live`;
- the smoke artifact contains only versions/status booleans, never Codex login output.

## One-time Windows registration

In GitHub, open:

`Settings -> Actions -> Runners -> New self-hosted runner`

Choose **Windows / x64**. GitHub shows commands containing a time-limited registration
token. Open **PowerShell as Administrator** on the Windows machine and run the
download/extract commands GitHub shows. GitHub recommends `C:\actions-runner`.

When you run the generated `config.cmd --url ... --token ...` command, append:

```powershell
--name DESKTOP-SJUA224-codex --labels codex-live --work _work
```

During Windows configuration, choose to run the runner **as a service**.

For this repository, the service account must be the same Windows user whose profile
already contains the authenticated Codex CLI state. If a different service identity is
used, `codex login status` can appear logged out even though interactive Codex works.

After setup, verify:

```powershell
Get-Service "actions.runner.*"
Get-Command codex
codex --version
codex login status
```

The runner should appear **Idle** under GitHub's Runners page and have labels:

`self-hosted`, `Windows`, `X64`, `codex-live`.

## First remote smoke

Comment exactly this on issue #31:

```text
/codex-live-smoke
```

The workflow produces a seven-day `codex-live-smoke-<run-id>` artifact. It verifies
the runner OS/arch, Git, Python, Codex CLI and authentication without persisting the
login-status output.
