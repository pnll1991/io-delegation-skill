param(
    [Parameter(Mandatory = $true)]
    [string]$PublicOutputPath
)

$ErrorActionPreference = "Stop"

$stateDir = Join-Path $env:USERPROFILE ".io-delegation"
$keyPath = Join-Path $stateDir "codex-live-rsa.dpapi"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

$rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider 3072
try {
    $privateXml = $rsa.ToXmlString($true)
    $privateBytes = [Text.Encoding]::UTF8.GetBytes($privateXml)
    try {
        $protected = [Security.Cryptography.ProtectedData]::Protect(
            $privateBytes,
            $null,
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        [IO.File]::WriteAllBytes($keyPath, $protected)
    }
    finally {
        [Array]::Clear($privateBytes, 0, $privateBytes.Length)
    }

    $public = $rsa.ExportParameters($false)
    $report = [ordered]@{
        schema = "io-codex-live-public-key/v1"
        algorithm = "RSA-OAEP-SHA1"
        runner_name = $env:RUNNER_NAME
        modulus_b64 = [Convert]::ToBase64String($public.Modulus)
        exponent_b64 = [Convert]::ToBase64String($public.Exponent)
    }
    $folder = Split-Path -Parent $PublicOutputPath
    if ($folder) { New-Item -ItemType Directory -Force -Path $folder | Out-Null }
    $report | ConvertTo-Json -Depth 3 | Set-Content -Path $PublicOutputPath -Encoding UTF8
}
finally {
    $rsa.Dispose()
}

Write-Host "Ephemeral live-runner encryption key is ready."
