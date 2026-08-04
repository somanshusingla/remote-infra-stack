[CmdletBinding()]
param([switch]$Gpu)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-NvidiaCudaImage {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not [System.IO.File]::Exists($Path)) {
        Throw-CommonError "NVIDIA_CUDA_IMAGE catalog is missing: $Path"
    }

    $image = ''
    $count = 0
    foreach ($rawLine in [System.IO.File]::ReadAllLines($Path)) {
        $line = $rawLine.TrimEnd("`r")
        if ($line -clike 'NVIDIA_CUDA_IMAGE=*') {
            $count += 1
            $image = $line.Substring('NVIDIA_CUDA_IMAGE='.Length)
        } elseif ($line -clike 'NVIDIA_CUDA_IMAGE*') {
            Throw-CommonError 'invalid NVIDIA_CUDA_IMAGE assignment in versions.env'
        }
    }
    if ($count -ne 1) {
        Throw-CommonError 'versions.env must contain exactly one NVIDIA_CUDA_IMAGE assignment'
    }
    if ($image -cnotmatch '^[^\s@]+@sha256:[0-9a-f]{64}$' -or $image.Contains(':latest')) {
        Throw-CommonError 'NVIDIA_CUDA_IMAGE must be a single non-latest digest-pinned reference'
    }
    return $image
}

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
    $cudaImage = if ($Gpu) {
        Get-NvidiaCudaImage -Path ([System.IO.Path]::Combine($repositoryRoot, 'versions.env'))
    } else {
        $null
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

        $bootstrapArguments = @('sudo', 'bash', $remoteBootstrap, '--install')
        if ($Gpu) {
            $bootstrapArguments += @('--gpu', '--cuda-image', $cudaImage)
        }
        Invoke-SshCommand -Configuration $configuration -CommandArguments $bootstrapArguments
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
