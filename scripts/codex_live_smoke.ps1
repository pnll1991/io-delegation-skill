param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"

function Required-Command([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "Required command not found: $Name"
    }
    return $cmd
}

$report = [ordered]@{
    schema = "io-codex-live-smoke/v1"
    timestamp_utc = [DateTime]::UtcNow.ToString("o")
    runner_name = $env:RUNNER_NAME
    runner_os = $env:RUNNER_OS
    runner_arch = $env:RUNNER_ARCH
    repository = $env:GITHUB_REPOSITORY
    git_sha = $null
    git_version = $null
    python_version = $null
    codex_version = $null
    codex_authenticated = $false
    ok = $false
}

try {
    $git = Required-Command "git"
    $codex = Required-Command "codex"

    $report.git_version = ((& $git.Source --version 2>&1) | Out-String).Trim()
    $report.git_sha = ((& $git.Source rev-parse HEAD 2>&1) | Out-String).Trim()

    $python = Get-Command "python" -ErrorAction SilentlyContinue
    if ($python) {
        $report.python_version = ((& $python.Source --version 2>&1) | Out-String).Trim()
    } else {
        $py = Get-Command "py" -ErrorAction SilentlyContinue
        if (-not $py) { throw "Required command not found: python/py" }
        $report.python_version = ((& $py.Source -3 --version 2>&1) | Out-String).Trim()
    }

    $report.codex_version = ((& $codex.Source --version 2>&1) | Out-String).Trim()

    # Deliberately do not persist or echo the login-status body. PowerShell 5 can
    # surface native stderr as an ErrorRecord even when Codex exits 0, so temporarily
    # avoid Stop semantics and trust the native process exit code only.
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $codex.Source login status *> $null
        $codexLoginExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    $report.codex_authenticated = ($codexLoginExit -eq 0)
    if (-not $report.codex_authenticated) {
        throw "Codex CLI is present but not authenticated for the runner account."
    }

    $report.ok = $true
}
catch {
    $report.error = $_.Exception.Message
}
finally {
    $folder = Split-Path -Parent $OutputPath
    if ($folder) { New-Item -ItemType Directory -Force -Path $folder | Out-Null }
    $report | ConvertTo-Json -Depth 4 | Set-Content -Path $OutputPath -Encoding UTF8
}

if (-not $report.ok) {
    Write-Error "Codex live runner smoke failed. Review the sanitized artifact."
    exit 2
}

Write-Host "Codex live runner smoke passed."
