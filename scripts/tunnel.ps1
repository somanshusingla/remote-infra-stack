[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Profiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-LocalPort {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value
    )

    if ($Value -cnotmatch '^[0-9]+$') {
        Throw-CommonError "$Key must contain ASCII digits only"
    }
    $parsed = 0
    if (-not [int]::TryParse($Value, [ref]$parsed)) {
        Throw-CommonError "$Key must be between 1 and 65535"
    }
    if ($parsed -lt 1 -or $parsed -gt 65535) {
        Throw-CommonError "$Key must be between 1 and 65535"
    }
    return $parsed
}

function Assert-LoopbackPortAvailable {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][int]$Port)

    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener(
            [System.Net.IPAddress]::Loopback,
            $Port
        )
        $listener.Start()
    } catch [System.Net.Sockets.SocketException] {
        $message = Get-TunnelSocketProbeFailureMessage `
            -Port $Port `
            -SocketError $_.Exception.SocketErrorCode
        Throw-CommonError $message
    } finally {
        if ($null -ne $listener) {
            $listener.Stop()
        }
    }
}

try {
    $scriptDirectory = [System.IO.Path]::GetFullPath($PSScriptRoot)
    $repositoryRoot = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::Combine($scriptDirectory, '..')
    )
    Import-Module (
        [System.IO.Path]::Combine($scriptDirectory, 'lib', 'Common.psm1')
    ) -Force -DisableNameChecking
    $remoteEnv = if ([string]::IsNullOrEmpty($env:STACK_REMOTE_ENV)) {
        [System.IO.Path]::Combine($repositoryRoot, 'remote.env')
    } else {
        $env:STACK_REMOTE_ENV
    }
    $configuration = Import-RemoteEnv -Path $remoteEnv

    [string[]]$selectedProfiles = @()
    if ($null -ne $Profiles) {
        $selectedProfiles = @($Profiles)
    }
    Assert-Profiles -Profiles $selectedProfiles

    $localPorts = New-Object 'System.Collections.Generic.Dictionary[string,int]' -ArgumentList (
        [System.StringComparer]::Ordinal
    )
    $localPortKeys = @(
        'LOCAL_POSTGRES_PORT',
        'LOCAL_REDIS_PORT',
        'LOCAL_CHROMA_PORT',
        'LOCAL_CHROMA_ADMIN_PORT',
        'LOCAL_DYNAMODB_PORT',
        'LOCAL_DYNAMODB_ADMIN_PORT',
        'LOCAL_OLLAMA_LLM_PORT',
        'LOCAL_OLLAMA_EMBEDDING_PORT',
        'LOCAL_OPENSEARCH_PORT',
        'LOCAL_OPENSEARCH_DASHBOARDS_PORT',
        'LOCAL_LANGFUSE_PORT',
        'LOCAL_MINIO_API_PORT',
        'LOCAL_MINIO_CONSOLE_PORT',
        'LOCAL_PGADMIN_PORT',
        'LOCAL_REDISINSIGHT_PORT'
    )
    foreach ($key in $localPortKeys) {
        $localPorts[$key] = ConvertTo-LocalPort -Key $key -Value ([string]$configuration[$key])
    }

    $selected = New-Object 'System.Collections.Generic.HashSet[string]' -ArgumentList (
        [System.StringComparer]::Ordinal
    )
    foreach ($profile in $selectedProfiles) {
        [void]$selected.Add($profile)
    }

    $mapping = @(
        [pscustomobject]@{ Profile = 'core'; Key = 'LOCAL_POSTGRES_PORT'; Remote = 15432 },
        [pscustomobject]@{ Profile = 'core'; Key = 'LOCAL_REDIS_PORT'; Remote = 16379 },
        [pscustomobject]@{ Profile = 'vector'; Key = 'LOCAL_CHROMA_PORT'; Remote = 18000 },
        [pscustomobject]@{ Profile = 'vector'; Key = 'LOCAL_CHROMA_ADMIN_PORT'; Remote = 18001 },
        [pscustomobject]@{ Profile = 'search'; Key = 'LOCAL_OPENSEARCH_PORT'; Remote = 9200 },
        [pscustomobject]@{
            Profile = 'search'
            Key = 'LOCAL_OPENSEARCH_DASHBOARDS_PORT'
            Remote = 5601
        },
        [pscustomobject]@{
            Profile = 'observability'
            Key = 'LOCAL_LANGFUSE_PORT'
            Remote = 3000
        },
        [pscustomobject]@{
            Profile = 'observability'
            Key = 'LOCAL_MINIO_API_PORT'
            Remote = 9090
        },
        [pscustomobject]@{
            Profile = 'observability'
            Key = 'LOCAL_MINIO_CONSOLE_PORT'
            Remote = 9091
        },
        [pscustomobject]@{ Profile = 'tools'; Key = 'LOCAL_PGADMIN_PORT'; Remote = 5050 },
        [pscustomobject]@{ Profile = 'tools'; Key = 'LOCAL_REDISINSIGHT_PORT'; Remote = 5540 },
        [pscustomobject]@{ Profile = 'dynamodb'; Key = 'LOCAL_DYNAMODB_PORT'; Remote = 18002 },
        [pscustomobject]@{ Profile = 'dynamodb'; Key = 'LOCAL_DYNAMODB_ADMIN_PORT'; Remote = 18003 },
        [pscustomobject]@{ Profile = 'inference'; Key = 'LOCAL_OLLAMA_LLM_PORT'; Remote = 11440 },
        [pscustomobject]@{ Profile = 'inference'; Key = 'LOCAL_OLLAMA_EMBEDDING_PORT'; Remote = 11441 }
    )

    $seenLocalPorts = New-Object 'System.Collections.Generic.HashSet[int]'
    $selectedLocalPorts = New-Object 'System.Collections.Generic.List[int]'
    $forwardArguments = New-Object 'System.Collections.Generic.List[string]'
    foreach ($entry in $mapping) {
        if (-not $selected.Contains([string]$entry.Profile)) {
            continue
        }
        $localPort = [int]$localPorts[[string]$entry.Key]
        if (-not $seenLocalPorts.Add($localPort)) {
            Throw-CommonError "duplicate local port: $localPort"
        }
        $selectedLocalPorts.Add($localPort)
        $forwardArguments.Add('-L')
        $forwardArguments.Add(
            ('127.0.0.1:{0}:127.0.0.1:{1}' -f $localPort, [int]$entry.Remote)
        )
    }

    foreach ($localPort in $selectedLocalPorts) {
        Assert-LoopbackPortAvailable -Port $localPort
    }
    Assert-CommandAvailable -Name 'ssh.exe'

    $nativeArguments = @()
    $nativeArguments += @(Get-SshArguments -Configuration $configuration)
    $nativeArguments += @(
        '-NT',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'GatewayPorts=no',
        '-o', 'ServerAliveInterval=30',
        '-o', 'ServerAliveCountMax=3'
    )
    $nativeArguments += @($forwardArguments.ToArray())
    $nativeArguments += @(Get-SshTarget -Configuration $configuration)

    & ssh.exe @nativeArguments
    if ($LASTEXITCODE -ne 0) {
        Throw-CommonError "ssh failed with exit code $LASTEXITCODE"
    }
} catch {
    [Console]::Error.WriteLine('ERROR: {0}', $_.Exception.Message)
    exit 1
}
