[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Action,
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Arguments
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
    Assert-CommandAvailable -Name 'ssh'

    if ([string]::IsNullOrEmpty($Action)) {
        Throw-CommonError 'usage: stack.ps1 up|stop profiles... | down | status | logs target | destroy'
    }
    [string[]]$forwardArguments = @()
    if ($null -ne $Arguments) {
        $forwardArguments = @($Arguments)
    }
    switch -CaseSensitive ($Action) {
        'up' {
            Assert-Profiles -Profiles $forwardArguments
            break
        }
        'stop' {
            Assert-Profiles -Profiles $forwardArguments
            break
        }
        'down' {
            if ($forwardArguments.Count -ne 0) {
                Throw-CommonError "$Action does not accept arguments"
            }
            break
        }
        'status' {
            if ($forwardArguments.Count -ne 0) {
                Throw-CommonError "$Action does not accept arguments"
            }
            break
        }
        'logs' {
            if ($forwardArguments.Count -ne 1) {
                Throw-CommonError 'logs requires one profile or service target'
            }
            $allowedLogTargets = @(
                'core', 'vector', 'search', 'observability', 'tools',
                'app-postgres', 'app-redis', 'chroma', 'opensearch',
                'opensearch-dashboards', 'langfuse-postgres', 'langfuse-redis',
                'clickhouse', 'minio', 'langfuse-worker', 'langfuse-web',
                'pgadmin', 'redisinsight'
            )
            if ($allowedLogTargets -cnotcontains $forwardArguments[0]) {
                Throw-CommonError "unknown log target: $($forwardArguments[0])"
            }
            break
        }
        'destroy' {
            if ($forwardArguments.Count -ne 0) {
                Throw-CommonError 'destroy does not accept command-line confirmation tokens'
            }
            [Console]::Error.Write(
                'Type the configured remote target {0} to continue: ',
                $configuration['REMOTE_HOST']
            )
            $confirmedHost = [Console]::In.ReadLine()
            if ($null -eq $confirmedHost) {
                Throw-CommonError 'destroy confirmation was cancelled'
            }
            if ($confirmedHost.TrimEnd("`r") -cne [string]$configuration['REMOTE_HOST']) {
                Throw-CommonError 'remote target confirmation did not match'
            }
            [Console]::Error.Write(
                'Permanent data loss: type DESTROY-remote-infra-stack to continue: '
            )
            $destroyToken = [Console]::In.ReadLine()
            if ($null -eq $destroyToken) {
                Throw-CommonError 'destroy confirmation was cancelled'
            }
            $destroyToken = $destroyToken.TrimEnd("`r")
            if ($destroyToken -cne 'DESTROY-remote-infra-stack') {
                Throw-CommonError 'destroy token did not match'
            }
            $forwardArguments = @('remote-infra-stack', $destroyToken)
            break
        }
        default {
            Throw-CommonError "unsupported stack action: $Action"
        }
    }

    $remoteCommand = @(
        'bash',
        ('{0}/current/scripts/remote/stack.sh' -f $configuration['REMOTE_ROOT']),
        $Action
    ) + $forwardArguments
    Invoke-SshCommand -Configuration $configuration -CommandArguments $remoteCommand
} catch {
    [Console]::Error.WriteLine('ERROR: {0}', $_.Exception.Message)
    exit 1
}
