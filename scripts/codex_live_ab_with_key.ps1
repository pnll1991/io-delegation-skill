param(
    [Parameter(Mandatory = $true)]
    [string]$CommentBody,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$prefix = "/codex-live-ab-key "
if (-not $CommentBody.StartsWith($prefix)) {
    throw "Invalid encrypted live-test command."
}
$cipherText = $CommentBody.Substring($prefix.Length).Trim()
if ($cipherText -notmatch '^[A-Za-z0-9+/=]+$' -or $cipherText.Length -gt 8192) {
    throw "Invalid encrypted payload format."
}

$keyPath = Join-Path $env:USERPROFILE ".io-delegation\codex-live-rsa.dpapi"
if (-not (Test-Path -LiteralPath $keyPath)) {
    throw "Live-runner private key is missing. Run /codex-live-keygen first."
}

$protected = [IO.File]::ReadAllBytes($keyPath)
$privateBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
    $protected,
    $null,
    [System.Security.Cryptography.DataProtectionScope]::CurrentUser
)
$rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider
$plainBytes = $null
try {
    $privateXml = [Text.Encoding]::UTF8.GetString($privateBytes)
    $rsa.FromXmlString($privateXml)
    $cipherBytes = [Convert]::FromBase64String($cipherText)
    $plainBytes = $rsa.Decrypt($cipherBytes, $true)
    $env:TYPESAFE_API_KEY = [Text.Encoding]::UTF8.GetString($plainBytes)

    python .\scripts\codex_live_ab.py --output $OutputPath
    if ($LASTEXITCODE -ne 0) {
        throw "Live Codex A/B script failed with exit code $LASTEXITCODE."
    }
}
finally {
    Remove-Item Env:TYPESAFE_API_KEY -ErrorAction SilentlyContinue
    if ($plainBytes) { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
    if ($privateBytes) { [Array]::Clear($privateBytes, 0, $privateBytes.Length) }
    $rsa.Dispose()
}
