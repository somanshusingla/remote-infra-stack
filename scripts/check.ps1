[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)][string[]]$Profiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

try {
    $scriptDirectory = [System.IO.Path]::GetFullPath($PSScriptRoot)
    $repositoryRoot = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::Combine($scriptDirectory, '..')
    )
    Import-Module ([System.IO.Path]::Combine($scriptDirectory, 'lib', 'Common.psm1')) -Force -DisableNameChecking

    $remoteEnv = if ([string]::IsNullOrEmpty($env:STACK_REMOTE_ENV)) {
        [System.IO.Path]::Combine($repositoryRoot, 'remote.env')
    } else {
        $env:STACK_REMOTE_ENV
    }
    $configuration = Import-RemoteEnv -Path $remoteEnv
    [string[]]$selectedProfiles = @()
    if ($null -ne $Profiles) { $selectedProfiles = @($Profiles) }
    Assert-Profiles -Profiles $selectedProfiles

    foreach ($commandName in @('git', 'ssh', 'scp')) {
        Assert-CommandAvailable -Name $commandName
    }
    $identityFile = [string]$configuration['REMOTE_IDENTITY_FILE']
    if ($identityFile.Length -gt 0) {
        if (-not [System.IO.File]::Exists($identityFile)) {
            Throw-CommonError "configured SSH identity file must be a readable regular file: $identityFile"
        }
        try {
            $identityStream = [System.IO.File]::Open(
                $identityFile,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::Read
            )
            $identityStream.Dispose()
        } catch {
            Throw-CommonError "configured SSH identity file must be a readable regular file: $identityFile"
        }
    }

    Assert-StackEnv -Path ([System.IO.Path]::Combine($repositoryRoot, '.env')) -ExamplePath ([System.IO.Path]::Combine($repositoryRoot, '.env.example'))
    Assert-CleanGitHead -Repository $repositoryRoot

    foreach ($powerShellFile in [System.IO.Directory]::GetFiles(
        $scriptDirectory,
        '*',
        [System.IO.SearchOption]::AllDirectories
    )) {
        if ([System.IO.Path]::GetExtension($powerShellFile) -notin @('.ps1', '.psm1')) {
            continue
        }
        $tokens = $null
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile(
            $powerShellFile,
            [ref]$tokens,
            [ref]$parseErrors
        )
        if ($null -ne $parseErrors -and $parseErrors.Count -gt 0) {
            Throw-CommonError "PowerShell syntax validation failed for $powerShellFile`: $($parseErrors -join '; ')"
        }
    }

    $openSearchConfig = [System.IO.Path]::Combine(
        $repositoryRoot, 'config', 'opensearch', 'opensearch.yml'
    )
    $openSearchEntrypoint = [System.IO.Path]::Combine(
        $repositoryRoot, 'config', 'opensearch', 'docker-entrypoint.sh'
    )
    foreach ($transportFile in @($openSearchConfig, $openSearchEntrypoint)) {
        if (-not [System.IO.File]::Exists($transportFile)) {
            Throw-CommonError "verified OpenSearch input is unavailable: $transportFile"
        }
        $length = (Get-Item -LiteralPath $transportFile -Force).Length
        if ($length -le 0 -or $length -gt 65536) {
            Throw-CommonError 'verified OpenSearch input exceeds the 64 KiB transport boundary'
        }
    }
    $env:STACK_OPENSEARCH_CONFIG_B64 = [System.Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($openSearchConfig)
    )
    $env:STACK_OPENSEARCH_ENTRYPOINT_B64 = [System.Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($openSearchEntrypoint)
    )

    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    $composeAvailable = $false
    if ($null -ne $dockerCommand) {
        & docker compose version *> $null
        $composeAvailable = $LASTEXITCODE -eq 0
    }
    if ($composeAvailable) {
        $composeArguments = @(
            'compose',
            '--env-file', [System.IO.Path]::Combine($repositoryRoot, 'versions.env'),
            '--env-file', [System.IO.Path]::Combine($repositoryRoot, '.env'),
            '--project-directory', $repositoryRoot,
            '--file', [System.IO.Path]::Combine($repositoryRoot, 'compose.yaml')
        )
        foreach ($profile in $selectedProfiles) {
            $composeArguments += @('--profile', $profile)
        }
        $composeArguments += @('config', '--quiet')
        & docker @composeArguments
        if ($LASTEXITCODE -ne 0) {
            Throw-CommonError 'local Docker Compose configuration rendering failed'
        }
        & docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine(
                'WARNING: local Docker daemon is unavailable; remote validation remains authoritative.'
            )
        }
    } else {
        [Console]::Error.WriteLine(
            'WARNING: local Docker Compose is unavailable; remote validation remains authoritative.'
        )
    }

    [Console]::Out.WriteLine(
        'Local checks passed for profiles: {0}',
        ($selectedProfiles -join ' ')
    )
} catch {
    [Console]::Error.WriteLine('ERROR: {0}', $_.Exception.Message)
    exit 1
}
