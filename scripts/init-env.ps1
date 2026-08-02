[CmdletBinding()]
param(
    [string]$OutputPath = '.env',
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-HexSecret([int]$ByteCount) {
    $bytes = New-Object byte[] $ByteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

if ((Test-Path -LiteralPath $OutputPath) -and -not $Force) {
    [Console]::Error.WriteLine("Refusing to overwrite $OutputPath without -Force")
    exit 1
}

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$template = Join-Path $repositoryRoot '.env.example'
$resolvedOutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $resolvedOutputPath
if (-not (Test-Path -LiteralPath $outputDirectory -PathType Container)) {
    throw "Output directory does not exist: $outputDirectory"
}

$temporary = $null
try {
    $temporary = New-TemporaryFile -ErrorAction Stop
    $temporaryOutputPath = Join-Path $outputDirectory ".init-env-$($temporary.Name)"
    Move-Item -LiteralPath $temporary.FullName -Destination $temporaryOutputPath
    $temporary = Get-Item -LiteralPath $temporaryOutputPath

    foreach ($line in Get-Content -LiteralPath $template) {
        if ($line -match '^(?<Key>[^=]+)=GENERATED_BY_INIT_ENV$') {
            switch ($Matches.Key) {
                'LANGFUSE_ENCRYPTION_KEY' { $value = New-HexSecret 32 }
                'OPENSEARCH_INITIAL_ADMIN_PASSWORD' { $value = "aA0!$(New-HexSecret 14)" }
                default { $value = New-HexSecret 32 }
            }
            Add-Content -LiteralPath $temporary.FullName -Value "$($Matches.Key)=$value" -NoNewline
            Add-Content -LiteralPath $temporary.FullName -Value "`n" -NoNewline
        }
        else {
            Add-Content -LiteralPath $temporary.FullName -Value $line -NoNewline
            Add-Content -LiteralPath $temporary.FullName -Value "`n" -NoNewline
        }
    }

    if ($Force) {
        Move-Item -LiteralPath $temporary.FullName -Destination $resolvedOutputPath -Force
    }
    else {
        Move-Item -LiteralPath $temporary.FullName -Destination $resolvedOutputPath
    }
}
finally {
    if ($null -ne $temporary -and (Test-Path -LiteralPath $temporary.FullName)) {
        Remove-Item -LiteralPath $temporary.FullName -Force
    }
}
