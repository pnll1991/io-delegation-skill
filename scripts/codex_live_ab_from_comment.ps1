param(
    [Parameter(Mandatory = $true)]
    [string]$ReferenceCommand,
    [Parameter(Mandatory = $true)]
    [string]$Repository,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$prefix = "/codex-live-ab-key "

function Test-EncryptedCommandBody {
    param([object]$Comment)

    if ($null -eq $Comment -or $Comment.user.login -ne "pnll1991") {
        return $false
    }
    if (-not ($Comment.body -is [string]) -or -not $Comment.body.StartsWith($prefix)) {
        return $false
    }

    $payload = $Comment.body.Substring($prefix.Length).Trim()
    return (
        $payload.Length -gt 0 -and
        ($payload.Length % 4) -eq 0 -and
        $payload -match '^[A-Za-z0-9+/=]+$'
    )
}

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
$expectedIssueUrl = "https://api.github.com/repos/$Repository/issues/31"
$uri = "https://api.github.com/repos/$Repository/issues/comments/$commentId"
$sourceComment = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers

if ($sourceComment.user.login -ne "pnll1991") {
    throw "Referenced comment author is not trusted."
}
if ($sourceComment.issue_url -ne $expectedIssueUrl) {
    throw "Referenced comment does not belong to the control issue."
}
if (-not ($sourceComment.body -is [string]) -or -not $sourceComment.body.StartsWith($prefix)) {
    throw "Referenced comment is not an encrypted live A/B command."
}

if (-not (Test-EncryptedCommandBody -Comment $sourceComment)) {
    $commentsUri = "$expectedIssueUrl/comments?per_page=100"
    $olderComments = Invoke-RestMethod -Method Get -Uri $commentsUri -Headers $headers
    $sourceComment = $olderComments |
        Where-Object {
            [UInt64]$_.id -lt $commentId -and
            $_.issue_url -eq $expectedIssueUrl -and
            (Test-EncryptedCommandBody -Comment $_)
        } |
        Sort-Object { [UInt64]$_.id } -Descending |
        Select-Object -First 1

    if ($null -eq $sourceComment) {
        throw "No earlier valid encrypted live A/B command was found."
    }
}

& .\scripts\codex_live_ab_with_key.ps1 `
    -CommentBody $sourceComment.body `
    -OutputPath $OutputPath
