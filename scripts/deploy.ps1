[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)][string[]]$Profiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-IsWindowsHost {
    return [System.IO.Path]::DirectorySeparatorChar -eq '\'
}

function Assert-SafeDirectory {
    param([string]$Path, [string]$Label)

    if (-not [System.IO.Directory]::Exists($Path)) {
        Throw-CommonError "$Label must be a real non-symlink directory owned by the current user"
    }
    $item = Get-Item -LiteralPath $Path -Force
    if (
        -not $item.PSIsContainer -or
        (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
    ) {
        Throw-CommonError "$Label must be a real non-symlink directory owned by the current user"
    }
    if (Test-IsWindowsHost) {
        $currentOwner = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $owner = (Get-Acl -LiteralPath $Path).Owner
        if ($owner -cne $currentOwner) {
            Throw-CommonError "$Label must be a real non-symlink directory owned by the current user"
        }
    }
}

function Set-PrivatePermissions {
    param([string]$Path, [bool]$Directory)

    if (Test-IsWindowsHost) {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        if ($Directory) {
            $security = New-Object System.Security.AccessControl.DirectorySecurity
            $security.SetAccessRuleProtection($true, $false)
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                $identity,
                [System.Security.AccessControl.FileSystemRights]::FullControl,
                [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
                [System.Security.AccessControl.PropagationFlags]::None,
                [System.Security.AccessControl.AccessControlType]::Allow
            )
            [void]$security.AddAccessRule($rule)
            try {
                [System.IO.Directory]::SetAccessControl($Path, $security)
            } catch {
                # Restricted Windows tokens can deny ACL replacement. The owned,
                # non-reparse-point boundary remains the mandatory safety gate.
            }
        } else {
            $security = New-Object System.Security.AccessControl.FileSecurity
            $security.SetAccessRuleProtection($true, $false)
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                $identity,
                [System.Security.AccessControl.FileSystemRights]::FullControl,
                [System.Security.AccessControl.AccessControlType]::Allow
            )
            [void]$security.AddAccessRule($rule)
            try {
                [System.IO.File]::SetAccessControl($Path, $security)
            } catch {
                # Match the Bash/MINGW fallback when Windows denies chmod/ACL changes.
            }
        }
        return
    }

    $mode = if ($Directory) { '0700' } else { '0600' }
    & chmod $mode -- $Path
    if ($LASTEXITCODE -ne 0) {
        Throw-CommonError "could not apply private mode $mode to $Path"
    }
}

function Read-StreamExactly {
    param(
        [System.IO.Stream]$Stream,
        [byte[]]$Buffer,
        [int]$Offset,
        [int]$Count
    )

    $total = 0
    while ($total -lt $Count) {
        $read = $Stream.Read($Buffer, $Offset + $total, $Count - $total)
        if ($read -eq 0) {
            break
        }
        $total += $read
    }
    return $total
}

function Export-GzipTarEntry {
    param(
        [string]$Archive,
        [string]$EntryName,
        [string]$Destination
    )

    $archiveStream = [System.IO.File]::OpenRead($Archive)
    $gzipStream = New-Object System.IO.Compression.GZipStream(
        $archiveStream,
        [System.IO.Compression.CompressionMode]::Decompress
    )
    try {
        $header = New-Object byte[] 512
        while ((Read-StreamExactly -Stream $gzipStream -Buffer $header -Offset 0 -Count 512) -eq 512) {
            $allZero = $true
            foreach ($value in $header) {
                if ($value -ne 0) { $allZero = $false; break }
            }
            if ($allZero) { break }

            $name = [System.Text.Encoding]::ASCII.GetString($header, 0, 100).Trim([char]0)
            $prefix = [System.Text.Encoding]::ASCII.GetString($header, 345, 155).Trim([char]0)
            if ($prefix.Length -gt 0) { $name = "$prefix/$name" }
            $sizeText = [System.Text.Encoding]::ASCII.GetString($header, 124, 12).Trim(
                [char]0, [char]32
            )
            $size = if ($sizeText.Length -eq 0) { 0 } else {
                [System.Convert]::ToInt64($sizeText, 8)
            }
            $typeFlag = [char]$header[156]
            $isRequested = $name -ceq $EntryName -and ($typeFlag -eq [char]0 -or $typeFlag -eq '0')
            $remaining = $size
            $buffer = New-Object byte[] 65536
            $destinationStream = $null
            if ($isRequested) {
                $destinationStream = [System.IO.File]::Open(
                    $Destination,
                    [System.IO.FileMode]::CreateNew,
                    [System.IO.FileAccess]::Write,
                    [System.IO.FileShare]::None
                )
            }
            try {
                while ($remaining -gt 0) {
                    $chunk = [int][System.Math]::Min([long]$buffer.Length, $remaining)
                    $read = Read-StreamExactly -Stream $gzipStream -Buffer $buffer -Offset 0 -Count $chunk
                    if ($read -ne $chunk) {
                        Throw-CommonError 'release archive ended while reading the receiver'
                    }
                    if ($null -ne $destinationStream) {
                        $destinationStream.Write($buffer, 0, $read)
                    }
                    $remaining -= $read
                }
            } finally {
                if ($null -ne $destinationStream) { $destinationStream.Dispose() }
            }

            $padding = [int]((512 - ($size % 512)) % 512)
            if ($padding -gt 0) {
                $discard = New-Object byte[] $padding
                if ((Read-StreamExactly -Stream $gzipStream -Buffer $discard -Offset 0 -Count $padding) -ne $padding) {
                    Throw-CommonError 'release archive ended while reading entry padding'
                }
            }
            if ($isRequested) { return }
            $header = New-Object byte[] 512
        }
    } finally {
        $gzipStream.Dispose()
        $archiveStream.Dispose()
    }
    Throw-CommonError "release archive is missing regular file: $EntryName"
}

function Get-Sha256Hex {
    param([string]$Path)

    $stream = [System.IO.File]::OpenRead($Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

try {
    $scriptDirectory = [System.IO.Path]::GetFullPath($PSScriptRoot)
    Import-Module ([System.IO.Path]::Combine($scriptDirectory, 'lib', 'Common.psm1')) -Force -DisableNameChecking
    foreach ($commandName in @('git', 'ssh', 'scp')) {
        Assert-CommandAvailable -Name $commandName
    }

    $gitRootOutput = @(& git -C ([System.IO.Path]::Combine($scriptDirectory, '..')) rev-parse --show-toplevel)
    if ($LASTEXITCODE -ne 0 -or $gitRootOutput.Count -ne 1) {
        Throw-CommonError 'deploy.ps1 must run from a Git checkout'
    }
    $repositoryRoot = [System.IO.Path]::GetFullPath($gitRootOutput[0])
    $remoteEnv = if ([string]::IsNullOrEmpty($env:STACK_REMOTE_ENV)) {
        [System.IO.Path]::Combine($repositoryRoot, 'remote.env')
    } else {
        $env:STACK_REMOTE_ENV
    }
    $configuration = Import-RemoteEnv -Path $remoteEnv
    [string[]]$selectedProfiles = @()
    if ($null -ne $Profiles) { $selectedProfiles = @($Profiles) }
    Assert-Profiles -Profiles $selectedProfiles

    $artifactParent = [System.IO.Path]::Combine($repositoryRoot, '.artifacts')
    if ([System.IO.Directory]::Exists($artifactParent) -or [System.IO.File]::Exists($artifactParent)) {
        Assert-SafeDirectory -Path $artifactParent -Label '.artifacts'
    }

    Assert-CleanGitHead -Repository $repositoryRoot
    Assert-FileNotTracked -Repository $repositoryRoot -Path ([System.IO.Path]::Combine($repositoryRoot, '.env')) -Label '.env'
    Assert-FileNotTracked -Repository $repositoryRoot -Path ([System.IO.Path]::Combine($repositoryRoot, 'remote.env')) -Label 'repository remote.env'
    Assert-FileNotTracked -Repository $repositoryRoot -Path $remoteEnv -Label 'selected remote.env'
    Assert-StackEnv -Path ([System.IO.Path]::Combine($repositoryRoot, '.env')) -ExamplePath ([System.IO.Path]::Combine($repositoryRoot, '.env.example'))

    $headOutput = @(Invoke-GitText -Repository $repositoryRoot -Arguments @('rev-parse', '--verify', 'HEAD^{commit}'))
    if ($headOutput.Count -ne 1 -or $headOutput[0] -notmatch '^[0-9a-fA-F]{40,64}$') {
        Throw-CommonError 'could not capture the full Git HEAD object ID'
    }
    $headOid = [string]$headOutput[0]

    $createdArtifactParent = $false
    if (-not [System.IO.Directory]::Exists($artifactParent)) {
        [void][System.IO.Directory]::CreateDirectory($artifactParent)
        $createdArtifactParent = $true
    }
    Assert-SafeDirectory -Path $artifactParent -Label '.artifacts'
    Set-PrivatePermissions -Path $artifactParent -Directory $true

    $operationId = [System.Guid]::NewGuid().ToString('N')
    $staging = [System.IO.Path]::Combine($artifactParent, "deploy.$operationId")
    [void][System.IO.Directory]::CreateDirectory($staging)
    Assert-SafeDirectory -Path $staging -Label 'deployment staging'
    Set-PrivatePermissions -Path $staging -Directory $true

    [string[]]$remoteCleanupPaths = @()
    try {
        $shortSha = $headOid.Substring(0, 12).ToLowerInvariant()
        $releaseName = '{0}-{1}-{2}' -f @(
            [System.DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'),
            $shortSha,
            $operationId
        )
        $archive = [System.IO.Path]::Combine($staging, "$releaseName.tar.gz")
        $checksum = "$archive.sha256"
        $runtimeEnvSnapshot = [System.IO.Path]::Combine($staging, "runtime-env-$operationId.env")
        $receiverSnapshot = [System.IO.Path]::Combine($staging, "deploy-release-$operationId.sh")

        [System.IO.File]::Copy(
            [System.IO.Path]::Combine($repositoryRoot, '.env'),
            $runtimeEnvSnapshot,
            $false
        )
        Set-PrivatePermissions -Path $runtimeEnvSnapshot -Directory $false

        $archiveArguments = @(
            '-C', $repositoryRoot,
            'archive', '--format=tar.gz', "--output=$archive", $headOid
        )
        & git @archiveArguments
        if ($LASTEXITCODE -ne 0) {
            Throw-CommonError "git archive failed with exit code $LASTEXITCODE"
        }
        Export-GzipTarEntry -Archive $archive -EntryName 'scripts/remote/deploy-release.sh' -Destination $receiverSnapshot
        Set-PrivatePermissions -Path $receiverSnapshot -Directory $false
        Set-PrivatePermissions -Path $archive -Directory $false

        $digest = Get-Sha256Hex -Path $archive
        $ascii = New-Object System.Text.ASCIIEncoding
        [System.IO.File]::WriteAllText(
            $checksum,
            ('{0}  {1}' -f $digest, [System.IO.Path]::GetFileName($archive)) + "`n",
            $ascii
        )
        Set-PrivatePermissions -Path $checksum -Directory $false

        $currentHead = @(Invoke-GitText -Repository $repositoryRoot -Arguments @('rev-parse', '--verify', 'HEAD^{commit}'))
        if ($currentHead.Count -ne 1 -or $currentHead[0] -cne $headOid) {
            Throw-CommonError 'HEAD changed during deployment preparation'
        }
        Assert-CleanGitHead -Repository $repositoryRoot
        Assert-FileNotTracked -Repository $repositoryRoot -Path ([System.IO.Path]::Combine($repositoryRoot, '.env')) -Label '.env'
        Assert-FileNotTracked -Repository $repositoryRoot -Path ([System.IO.Path]::Combine($repositoryRoot, 'remote.env')) -Label 'repository remote.env'
        Assert-FileNotTracked -Repository $repositoryRoot -Path $remoteEnv -Label 'selected remote.env'

        $remoteIncoming = '{0}/incoming' -f $configuration['REMOTE_ROOT']
        $remoteArchive = "$remoteIncoming/$([System.IO.Path]::GetFileName($archive))"
        $remoteChecksum = "$remoteIncoming/$([System.IO.Path]::GetFileName($checksum))"
        $remoteRuntimeEnv = "$remoteIncoming/$([System.IO.Path]::GetFileName($runtimeEnvSnapshot))"
        $remoteReceiver = "$remoteIncoming/$([System.IO.Path]::GetFileName($receiverSnapshot))"
        $remoteCleanupPaths = @(
            $remoteArchive, $remoteChecksum, $remoteRuntimeEnv, $remoteReceiver
        )

        $scpArguments = @()
        $scpArguments += @(Get-ScpArguments -Configuration $configuration)
        $scpArguments += @(
            $archive,
            $checksum,
            $runtimeEnvSnapshot,
            $receiverSnapshot,
            ('{0}:{1}/' -f (Get-SshTarget -Configuration $configuration), $remoteIncoming)
        )
        & scp @scpArguments
        if ($LASTEXITCODE -ne 0) {
            Throw-CommonError "scp failed with exit code $LASTEXITCODE"
        }

        Invoke-SshCommand -Configuration $configuration -CommandArguments @(
            'bash', $remoteReceiver,
            '--root', [string]$configuration['REMOTE_ROOT'],
            '--archive', $remoteArchive,
            '--checksum', $remoteChecksum,
            '--env', $remoteRuntimeEnv,
            '--profiles', ($selectedProfiles -join ',')
        )
        [Console]::Out.WriteLine('Deployment completed for release {0}.', $releaseName)
    } finally {
        if ($remoteCleanupPaths.Count -gt 0) {
            try {
                $cleanupCommand = @('rm', '-f', '--') + $remoteCleanupPaths
                Invoke-SshCommand -Configuration $configuration -CommandArguments $cleanupCommand
            } catch {
                # Cleanup is idempotent and must not hide the deployment result.
            }
        }
        if ([System.IO.Directory]::Exists($staging)) {
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
        if ($createdArtifactParent -and [System.IO.Directory]::Exists($artifactParent)) {
            try { [System.IO.Directory]::Delete($artifactParent, $false) } catch { }
        }
    }
} catch {
    [Console]::Error.WriteLine('ERROR: {0}', $_.Exception.Message)
    exit 1
}
