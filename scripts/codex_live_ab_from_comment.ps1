param(
    [Parameter(Mandatory = $true)]
    [string]$ReferenceCommand,
    [Parameter(Mandatory = $true)]
    [string]$Repository,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"

$match = [regex]::Match($ReferenceCommand, '^/codex-live-ab-key-ref ([0-9]+)$')
if (-not $match.Success) {
    throw "Invalid live-test reference. Expected a decimal comment id only."
}
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "Invalid repository name."
}
if ([string]::IsNullOrWhiteSpace($env:GH_TOKEN)) {
    throw "GitHub token is unavailable."
}

$commentId = [UInt64]::Parse($match.Groups[1].Value)
$headers = @{
    Authorization = "Bearer $env:GH_TOKEN"
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}
$uri = "https://api.github.com/repos/$Repository/issues/comments/$commentId"
$sourceComment = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers

if ($sourceComment.user.login -ne "pnll1991") {
    throw "Referenced comment author is not trusted."
}
$expectedIssueUrl = "https://api.github.com/repos/$Repository/issues/31"
if ($sourceComment.issue_url -ne $expectedIssueUrl) {
    throw "Referenced comment does not belong to the control issue."
}
if (-not ($sourceComment.body -is [string])) {
    throw "Referenced comment body is invalid."
}
if (-not $sourceComment.body.StartsWith("/codex-live-ab-key ")) {
    throw "Referenced comment is not an encrypted live A/B command."
}

& .\scripts\codex_live_ab_with_key.ps1 `
    -CommentBody $sourceComment.body `
    -OutputPath $OutputPath
