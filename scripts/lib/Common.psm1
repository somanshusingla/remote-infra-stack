Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Throw-CommonError {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Message)

    throw $Message
}

function Test-RemoteKeyAllowed {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Key)

    return $Key -in @(
        'REMOTE_HOST', 'REMOTE_USER', 'REMOTE_PORT', 'REMOTE_IDENTITY_FILE', 'REMOTE_ROOT',
        'LOCAL_POSTGRES_PORT', 'LOCAL_REDIS_PORT', 'LOCAL_CHROMA_PORT',
        'LOCAL_OPENSEARCH_PORT', 'LOCAL_OPENSEARCH_DASHBOARDS_PORT',
        'LOCAL_LANGFUSE_PORT', 'LOCAL_PGADMIN_PORT', 'LOCAL_REDISINSIGHT_PORT',
        'LOCAL_MINIO_API_PORT', 'LOCAL_MINIO_CONSOLE_PORT'
    )
}

function Import-RemoteEnv {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not [System.IO.File]::Exists($Path)) {
        Throw-CommonError "remote configuration file is missing: $Path"
    }

    $values = @{}
    $lineNumber = 0
    foreach ($rawLine in [System.IO.File]::ReadAllLines($Path)) {
        $lineNumber += 1
        $line = $rawLine.TrimEnd("`r")
        if ($line.Length -eq 0 -or $line.StartsWith('#')) {
            continue
        }
        if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            Throw-CommonError "invalid remote.env line ${lineNumber}: expected KEY=VALUE"
        }
        $key = $Matches[1]
        $value = $Matches[2]
        if (-not (Test-RemoteKeyAllowed -Key $key)) {
            Throw-CommonError "unknown remote.env key: $key"
        }
        if ($values.ContainsKey($key)) {
            Throw-CommonError "duplicate remote.env key: $key"
        }
        $values[$key] = $value
    }

    $requiredKeys = @(
        'REMOTE_HOST', 'REMOTE_USER', 'REMOTE_PORT', 'REMOTE_IDENTITY_FILE', 'REMOTE_ROOT',
        'LOCAL_POSTGRES_PORT', 'LOCAL_REDIS_PORT', 'LOCAL_CHROMA_PORT',
        'LOCAL_OPENSEARCH_PORT', 'LOCAL_OPENSEARCH_DASHBOARDS_PORT',
        'LOCAL_LANGFUSE_PORT', 'LOCAL_PGADMIN_PORT', 'LOCAL_REDISINSIGHT_PORT',
        'LOCAL_MINIO_API_PORT', 'LOCAL_MINIO_CONSOLE_PORT'
    )
    foreach ($requiredKey in $requiredKeys) {
        if (-not $values.ContainsKey($requiredKey)) {
            Throw-CommonError "missing remote.env key: $requiredKey"
        }
    }

    $hostName = [string]$values['REMOTE_HOST']
    $userName = [string]$values['REMOTE_USER']
    $port = [string]$values['REMOTE_PORT']
    $root = [string]$values['REMOTE_ROOT']
    if ([string]::IsNullOrEmpty($hostName)) {
        Throw-CommonError 'REMOTE_HOST is required'
    }
    if ($hostName.StartsWith('-')) {
        Throw-CommonError 'REMOTE_HOST must not begin with an option prefix'
    }
    if ($hostName.Contains(':')) {
        Throw-CommonError 'REMOTE_HOST must not contain a colon; IPv6 targets are unsupported'
    }
    if ($hostName -notmatch '^[A-Za-z0-9_.-]+$') {
        Throw-CommonError 'REMOTE_HOST contains unsupported characters'
    }
    if ($userName.StartsWith('-')) {
        Throw-CommonError 'REMOTE_USER must not begin with an option prefix'
    }
    if ($userName.Length -gt 0 -and $userName -notmatch '^[A-Za-z0-9_.-]+$') {
        Throw-CommonError 'REMOTE_USER contains unsupported characters'
    }
    if ($port.Length -gt 0) {
        $parsedPort = 0
        if (-not [int]::TryParse($port, [ref]$parsedPort)) {
            Throw-CommonError 'REMOTE_PORT must be an integer'
        }
        if ($parsedPort -lt 1 -or $parsedPort -gt 65535) {
            Throw-CommonError 'REMOTE_PORT must be between 1 and 65535'
        }
    }
    if (
        [string]::IsNullOrEmpty($root) -or
        [System.IO.Path]::IsPathRooted($root) -or
        $root.StartsWith('~') -or
        $root -match '^[A-Za-z]:'
    ) {
        Throw-CommonError 'REMOTE_ROOT must be a relative REMOTE_ROOT path'
    }
    if ($root -match '(^|/)\.\.(/|$)') {
        Throw-CommonError 'REMOTE_ROOT must not contain .. path components'
    }
    if ($root -notmatch '^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$') {
        Throw-CommonError 'REMOTE_ROOT contains unsupported REMOTE_ROOT characters'
    }

    return $values
}

function Assert-Profiles {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Profiles)

    if ($Profiles.Count -eq 0) {
        Throw-CommonError 'at least one profile is required'
    }
    $seen = @{}
    foreach ($profile in $Profiles) {
        if ($profile -notin @('core', 'vector', 'search', 'observability', 'tools')) {
            Throw-CommonError "unknown profile: $profile"
        }
        if ($seen.ContainsKey($profile)) {
            Throw-CommonError "duplicate profile: $profile"
        }
        $seen[$profile] = $true
    }
    if ($seen.ContainsKey('tools') -and -not $seen.ContainsKey('core')) {
        Throw-CommonError 'tools requires core'
    }
}

function Get-SshTarget {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Configuration)

    if ([string]::IsNullOrEmpty([string]$Configuration['REMOTE_USER'])) {
        return [string]$Configuration['REMOTE_HOST']
    }
    return '{0}@{1}' -f $Configuration['REMOTE_USER'], $Configuration['REMOTE_HOST']
}

function Get-SshArguments {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Configuration)

    $result = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrEmpty([string]$Configuration['REMOTE_PORT'])) {
        $result.Add('-p')
        $result.Add([string]$Configuration['REMOTE_PORT'])
    }
    if (-not [string]::IsNullOrEmpty([string]$Configuration['REMOTE_IDENTITY_FILE'])) {
        $result.Add('-i')
        $result.Add([string]$Configuration['REMOTE_IDENTITY_FILE'])
    }
    return $result.ToArray()
}

function Get-ScpArguments {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Configuration)

    $result = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrEmpty([string]$Configuration['REMOTE_PORT'])) {
        $result.Add('-P')
        $result.Add([string]$Configuration['REMOTE_PORT'])
    }
    if (-not [string]::IsNullOrEmpty([string]$Configuration['REMOTE_IDENTITY_FILE'])) {
        $result.Add('-i')
        $result.Add([string]$Configuration['REMOTE_IDENTITY_FILE'])
    }
    return $result.ToArray()
}

function ConvertTo-PosixCommand {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments)

    if ($Arguments.Count -eq 0) {
        Throw-CommonError 'remote command must not be empty'
    }
    $apostropheEscape = "'" + "\" + "'" + "'"
    $quoted = foreach ($argument in $Arguments) {
        "'" + ([string]$argument).Replace("'", $apostropheEscape) + "'"
    }
    return $quoted -join ' '
}

function Invoke-SshCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Configuration,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$CommandArguments
    )

    $nativeArguments = @()
    $nativeArguments += @(Get-SshArguments -Configuration $Configuration)
    $nativeArguments += @(
        '--',
        (Get-SshTarget -Configuration $Configuration),
        (ConvertTo-PosixCommand -Arguments $CommandArguments)
    )
    & ssh @nativeArguments
    if ($LASTEXITCODE -ne 0) {
        Throw-CommonError "ssh failed with exit code $LASTEXITCODE"
    }
}

function Assert-CommandAvailable {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Name)

    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Throw-CommonError "required command is unavailable: $Name"
    }
}

function Invoke-GitText {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Arguments
    )

    $output = @(& git -C $Repository @Arguments)
    if ($LASTEXITCODE -ne 0) {
        Throw-CommonError "Git command failed: git -C $Repository $($Arguments -join ' ')"
    }
    return $output
}

function Assert-CleanGitHead {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Repository)

    & git -C $Repository rev-parse --verify HEAD *> $null
    if ($LASTEXITCODE -ne 0) {
        Throw-CommonError 'operation requires a clean committed Git HEAD'
    }
    & git -C $Repository diff --quiet --
    if ($LASTEXITCODE -ne 0) {
        Throw-CommonError 'operation requires a clean committed Git HEAD'
    }
    & git -C $Repository diff --cached --quiet --
    if ($LASTEXITCODE -ne 0) {
        Throw-CommonError 'operation requires a clean committed Git HEAD'
    }
    $status = @(& git -C $Repository status --porcelain --untracked-files=normal)
    if ($LASTEXITCODE -ne 0 -or $status.Count -gt 0) {
        Throw-CommonError 'operation requires a clean committed Git HEAD'
    }
}

function Assert-FileNotTracked {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $root = [System.IO.Path]::GetFullPath($Repository).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $canonical = [System.IO.Path]::GetFullPath($Path)
    $comparison = if ([System.IO.Path]::DirectorySeparatorChar -eq '\') {
        [System.StringComparison]::OrdinalIgnoreCase
    } else {
        [System.StringComparison]::Ordinal
    }
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    if (-not $canonical.StartsWith($prefix, $comparison)) {
        return
    }
    $relative = $canonical.Substring($prefix.Length).Replace('\', '/')
    $tracked = @(& git -C $root ls-files -- $relative)
    if ($LASTEXITCODE -ne 0) {
        Throw-CommonError "could not inspect tracked status for $Label"
    }
    if ($tracked.Count -gt 0) {
        Throw-CommonError "$Label must not be tracked by Git"
    }
}

function Read-StrictEnvFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not [System.IO.File]::Exists($Path)) {
        Throw-CommonError "$Label is missing: $Path"
    }
    $values = @{}
    $order = New-Object System.Collections.Generic.List[string]
    $lineNumber = 0
    foreach ($rawLine in [System.IO.File]::ReadAllLines($Path)) {
        $lineNumber += 1
        $line = $rawLine.TrimEnd("`r")
        if ($line.Length -eq 0 -or $line.StartsWith('#')) {
            continue
        }
        if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            Throw-CommonError "invalid $Label line ${lineNumber}: expected KEY=VALUE"
        }
        $key = $Matches[1]
        if ($values.ContainsKey($key)) {
            Throw-CommonError "duplicate $Label key: $key"
        }
        $values[$key] = $Matches[2]
        $order.Add($key)
    }
    return [pscustomobject]@{ Values = $values; Order = $order.ToArray() }
}

function Assert-StackEnv {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExamplePath
    )

    $actual = Read-StrictEnvFile -Path $Path -Label '.env'
    $expected = Read-StrictEnvFile -Path $ExamplePath -Label '.env.example contract'
    foreach ($key in $expected.Order) {
        if (-not $actual.Values.ContainsKey($key)) {
            Throw-CommonError "missing required .env key: $key"
        }
        $value = [string]$actual.Values[$key]
        if ($value.Length -eq 0) {
            Throw-CommonError "empty required .env value: $key"
        }
        if ($value.Contains('GENERATED_BY_INIT_ENV')) {
            Throw-CommonError "placeholder remains in .env key: $key"
        }
    }
    foreach ($key in $actual.Order) {
        if (-not $expected.Values.ContainsKey($key)) {
            Throw-CommonError "unknown .env key: $key"
        }
    }

    $password = [string]$actual.Values['OPENSEARCH_INITIAL_ADMIN_PASSWORD']
    if (
        $password.Length -lt 12 -or
        $password -notmatch '[a-z]' -or
        $password -notmatch '[A-Z]' -or
        $password -notmatch '[0-9]'
    ) {
        Throw-CommonError 'OPENSEARCH_INITIAL_ADMIN_PASSWORD does not meet the local strength contract'
    }
    $encryptionKey = [string]$actual.Values['LANGFUSE_ENCRYPTION_KEY']
    if ($encryptionKey -notmatch '^[0-9a-f]{64}$') {
        Throw-CommonError 'LANGFUSE_ENCRYPTION_KEY must be 64 lowercase hexadecimal characters'
    }
}

Export-ModuleMember -Function @(
    'Throw-CommonError',
    'Import-RemoteEnv',
    'Assert-Profiles',
    'Get-SshArguments',
    'Get-ScpArguments',
    'Get-SshTarget',
    'ConvertTo-PosixCommand',
    'Invoke-SshCommand',
    'Assert-CommandAvailable',
    'Invoke-GitText',
    'Assert-CleanGitHead',
    'Assert-FileNotTracked',
    'Assert-StackEnv'
)
