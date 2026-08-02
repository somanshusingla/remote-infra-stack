[CmdletBinding()]
param()

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
    foreach ($commandName in @('ssh', 'scp')) {
        Assert-CommandAvailable -Name $commandName
    }

    $bootstrapSource = [System.IO.Path]::Combine(
        $scriptDirectory, 'remote', 'bootstrap-host.sh'
    )
    if (-not [System.IO.File]::Exists($bootstrapSource)) {
        Throw-CommonError "remote bootstrap script is missing: $bootstrapSource"
    }
    $operationId = [System.Guid]::NewGuid().ToString('N')
    $remoteBootstrap = '{0}/incoming/bootstrap-{1}.sh' -f @(
        $configuration['REMOTE_ROOT'], $operationId
    )
    $remoteCleanup = $false
    try {
        Invoke-SshCommand -Configuration $configuration -CommandArguments @(
            'mkdir', '-p', '--',
            ('{0}/incoming' -f $configuration['REMOTE_ROOT']),
            ('{0}/releases' -f $configuration['REMOTE_ROOT']),
            ('{0}/runtime' -f $configuration['REMOTE_ROOT'])
        )
        $remoteCleanup = $true

        $scpArguments = @()
        $scpArguments += @(Get-ScpArguments -Configuration $configuration)
        $scpArguments += @(
            $bootstrapSource,
            ('{0}:{1}' -f (Get-SshTarget -Configuration $configuration), $remoteBootstrap)
        )
        & scp @scpArguments
        if ($LASTEXITCODE -ne 0) {
            Throw-CommonError "scp failed with exit code $LASTEXITCODE"
        }

        Invoke-SshCommand -Configuration $configuration -CommandArguments @(
            'sudo', 'bash', $remoteBootstrap, '--install'
        )
        Invoke-SshCommand -Configuration $configuration -CommandArguments @(
            'rm', '-f', '--', $remoteBootstrap
        )
        $remoteCleanup = $false
    } finally {
        if ($remoteCleanup) {
            try {
                Invoke-SshCommand -Configuration $configuration -CommandArguments @(
                    'rm', '-f', '--', $remoteBootstrap
                )
            } catch {
                # Preserve the original bootstrap failure.
            }
        }
    }

    [Console]::Out.WriteLine(
        'Remote host bootstrap completed for {0}.',
        $configuration['REMOTE_HOST']
    )
} catch {
    [Console]::Error.WriteLine('ERROR: {0}', $_.Exception.Message)
    exit 1
}
