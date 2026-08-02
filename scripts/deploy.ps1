[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)][string[]]$Profiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-IsWindowsHost {
    return [System.IO.Path]::DirectorySeparatorChar -eq '\'
}

function Initialize-NativeDirectoryLeaseType {
    if ($null -ne ([System.Management.Automation.PSTypeName]'RemoteInfraStack.NativeDirectoryLease').Type) {
        return
    }

    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace RemoteInfraStack
{
    public static class NativeDirectoryLease
    {
        private const uint GenericRead = 0x80000000;
        private const uint FileFlagBackupSemantics = 0x02000000;
        private const uint FileFlagOpenReparsePoint = 0x00200000;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(
            string fileName,
            uint desiredAccess,
            FileShare shareMode,
            IntPtr securityAttributes,
            FileMode creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile
        );

        [StructLayout(LayoutKind.Sequential)]
        private struct ByHandleFileInformation
        {
            public uint FileAttributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
            public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
            public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
            public uint VolumeSerialNumber;
            public uint FileSizeHigh;
            public uint FileSizeLow;
            public uint NumberOfLinks;
            public uint FileIndexHigh;
            public uint FileIndexLow;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle file,
            out ByHandleFileInformation information
        );

        public static SafeFileHandle Open(string path)
        {
            SafeFileHandle handle = CreateFile(
                path,
                GenericRead,
                FileShare.Read | FileShare.Write,
                IntPtr.Zero,
                FileMode.Open,
                FileFlagBackupSemantics | FileFlagOpenReparsePoint,
                IntPtr.Zero
            );
            if (handle.IsInvalid)
            {
                int error = Marshal.GetLastWin32Error();
                handle.Dispose();
                throw new Win32Exception(error, "Could not acquire a non-share-delete directory handle");
            }
            return handle;
        }

        public static string GetIdentity(SafeFileHandle handle)
        {
            ByHandleFileInformation information;
            if (!GetFileInformationByHandle(handle, out information))
            {
                throw new Win32Exception(
                    Marshal.GetLastWin32Error(),
                    "Could not read directory identity from its durable handle"
                );
            }
            ulong fileIndex = ((ulong)information.FileIndexHigh << 32) | information.FileIndexLow;
            return information.VolumeSerialNumber.ToString("X8") + ":" + fileIndex.ToString("X16");
        }
    }
}
'@
}

function Open-PrivateDirectoryLease {
    param([string]$Path, [string]$Label)

    if (-not (Test-IsWindowsHost)) {
        Throw-CommonError 'deploy.ps1 cannot prove a durable private staging boundary on this platform; use scripts/deploy.sh'
    }
    try {
        Initialize-NativeDirectoryLeaseType
        $lease = [RemoteInfraStack.NativeDirectoryLease]::Open($Path)
        if ($null -eq $lease -or $lease.IsInvalid -or $lease.IsClosed) {
            Throw-CommonError "$Label directory lease is invalid"
        }
        return $lease
    } catch {
        Throw-CommonError "could not acquire a durable private $Label directory lease: $($_.Exception.Message)"
    }
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

function Assert-PrivateWindowsAcl {
    param(
        [string]$Path,
        [bool]$RequireProtected,
        [string]$Label
    )

    try {
        $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
        $currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        $ownerSid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier])
        if ($ownerSid -cne $currentSid) {
            Throw-CommonError "$Label ACL owner is not the current user"
        }
        if ($RequireProtected -and -not $acl.AreAccessRulesProtected) {
            Throw-CommonError "$Label ACL still inherits permissions"
        }
        $rules = $acl.GetAccessRules(
            $true,
            $true,
            [System.Security.Principal.SecurityIdentifier]
        )
        $hasFullControl = $false
        foreach ($rule in $rules) {
            if (
                $rule.IdentityReference -cne $currentSid -or
                $rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow
            ) {
                Throw-CommonError "$Label ACL grants a non-current or non-allow principal"
            }
            if (
                ($rule.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -eq
                [System.Security.AccessControl.FileSystemRights]::FullControl
            ) {
                $hasFullControl = $true
            }
        }
        if (-not $hasFullControl) {
            Throw-CommonError "$Label ACL does not grant current-user FullControl"
        }
    } catch {
        Throw-CommonError "private $Label ACL verification failed: $($_.Exception.Message)"
    }
}

function Set-PrivateDirectoryPermissions {
    param([string]$Path, [string]$Label)

    if (-not (Test-IsWindowsHost)) {
        Throw-CommonError 'deploy.ps1 cannot prove a durable private staging boundary on this platform; use scripts/deploy.sh'
    }

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $security = Get-Acl -LiteralPath $Path -ErrorAction Stop
    $security.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $identity,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.SetAccessRule($rule)
    try {
        Set-Acl -LiteralPath $Path -AclObject $security -ErrorAction Stop
    } catch {
        Throw-CommonError "could not establish a private $Label ACL: $($_.Exception.Message)"
    }
}

function Assert-PrivateDirectoryBoundary {
    param(
        [string]$Path,
        [Microsoft.Win32.SafeHandles.SafeFileHandle]$Lease,
        [string]$Label
    )

    if ($null -eq $Lease -or $Lease.IsInvalid -or $Lease.IsClosed) {
        Throw-CommonError "$Label directory lease is not durable"
    }
    $pathLease = $null
    try {
        $leasedIdentity = [RemoteInfraStack.NativeDirectoryLease]::GetIdentity($Lease)
        $pathLease = [RemoteInfraStack.NativeDirectoryLease]::Open($Path)
        $pathIdentity = [RemoteInfraStack.NativeDirectoryLease]::GetIdentity($pathLease)
        if ($pathIdentity -cne $leasedIdentity) {
            Throw-CommonError "$Label pathname no longer identifies its leased directory"
        }
    } catch {
        Throw-CommonError "private $Label directory identity verification failed: $($_.Exception.Message)"
    } finally {
        if ($null -ne $pathLease) {
            $pathLease.Dispose()
        }
    }
    Assert-SafeDirectory -Path $Path -Label $Label
    Assert-PrivateWindowsAcl -Path $Path -RequireProtected $true -Label $Label
}

function Assert-PrivateDeploymentBoundary {
    param(
        [string]$ArtifactParent,
        [Microsoft.Win32.SafeHandles.SafeFileHandle]$ArtifactParentLease,
        [string]$Staging,
        [Microsoft.Win32.SafeHandles.SafeFileHandle]$StagingLease
    )

    Assert-PrivateDirectoryBoundary -Path $ArtifactParent -Lease $ArtifactParentLease -Label 'artifact parent'
    Assert-PrivateDirectoryBoundary -Path $Staging -Lease $StagingLease -Label 'staging'
}

function Assert-PrivateStagingFile {
    param(
        [string]$Path,
        [string]$Staging,
        [string]$Label
    )

    $stagingRoot = [System.IO.Path]::GetFullPath($Staging).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $canonical = [System.IO.Path]::GetFullPath($Path)
    $comparison = if (Test-IsWindowsHost) {
        [System.StringComparison]::OrdinalIgnoreCase
    } else {
        [System.StringComparison]::Ordinal
    }
    $prefix = $stagingRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $canonical.StartsWith($prefix, $comparison)) {
        Throw-CommonError "$Label escaped private deployment staging"
    }
    if (-not [System.IO.File]::Exists($canonical)) {
        Throw-CommonError "$Label is not a regular file in private deployment staging"
    }
    $item = Get-Item -LiteralPath $canonical -Force
    if (
        $item.PSIsContainer -or
        (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)
    ) {
        Throw-CommonError "$Label is not a non-reparse regular staging file"
    }
    if (Test-IsWindowsHost) {
        Assert-PrivateWindowsAcl -Path $canonical -RequireProtected $false -Label $Label
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

try {
    $scriptDirectory = [System.IO.Path]::GetFullPath($PSScriptRoot)
    Import-Module ([System.IO.Path]::Combine($scriptDirectory, 'lib', 'Common.psm1')) -Force -DisableNameChecking
    if (-not (Test-IsWindowsHost)) {
        Throw-CommonError 'deploy.ps1 cannot prove a durable private staging boundary on this platform; use scripts/deploy.sh'
    }
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
    $artifactParentLease = $null
    $stagingLease = $null
    $staging = $null
    [string[]]$remoteCleanupPaths = @()
    try {
        if (-not [System.IO.Directory]::Exists($artifactParent)) {
            [void][System.IO.Directory]::CreateDirectory($artifactParent)
            $createdArtifactParent = $true
        }
        $artifactParentLease = Open-PrivateDirectoryLease -Path $artifactParent -Label 'artifact parent'
        Assert-SafeDirectory -Path $artifactParent -Label 'artifact parent'
        Set-PrivateDirectoryPermissions -Path $artifactParent -Label 'artifact parent'
        Assert-PrivateDirectoryBoundary -Path $artifactParent -Lease $artifactParentLease -Label 'artifact parent'

        $operationId = [System.Guid]::NewGuid().ToString('N')
        $staging = [System.IO.Path]::Combine($artifactParent, "deploy.$operationId")
        [void][System.IO.Directory]::CreateDirectory($staging)
        $stagingLease = Open-PrivateDirectoryLease -Path $staging -Label 'staging'
        Assert-SafeDirectory -Path $staging -Label 'staging'
        Set-PrivateDirectoryPermissions -Path $staging -Label 'staging'
        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease

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

        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease
        [System.IO.File]::Copy(
            [System.IO.Path]::Combine($repositoryRoot, '.env'),
            $runtimeEnvSnapshot,
            $false
        )
        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease
        Assert-PrivateStagingFile -Path $runtimeEnvSnapshot -Staging $staging -Label 'runtime environment snapshot'

        $archiveArguments = @(
            '-C', $repositoryRoot,
            'archive', '--format=tar.gz', "--output=$archive", $headOid
        )
        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease
        & git @archiveArguments
        if ($LASTEXITCODE -ne 0) {
            Throw-CommonError "git archive failed with exit code $LASTEXITCODE"
        }
        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease
        Assert-PrivateStagingFile -Path $archive -Staging $staging -Label 'release archive'
        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease
        Assert-PrivateStagingFile -Path $archive -Staging $staging -Label 'release archive'
        Export-GzipTarEntry -Archive $archive -EntryName 'scripts/remote/deploy-release.sh' -Destination $receiverSnapshot
        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease
        Assert-PrivateStagingFile -Path $receiverSnapshot -Staging $staging -Label 'release receiver snapshot'

        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease
        Assert-PrivateStagingFile -Path $archive -Staging $staging -Label 'release archive'
        $hash = Get-FileHash -LiteralPath $archive -Algorithm SHA256
        $digest = ([string]$hash.Hash).ToLowerInvariant()
        if ($digest -cnotmatch '^[0-9a-f]{64}$') {
            Throw-CommonError 'Get-FileHash returned an invalid SHA256 digest'
        }
        $ascii = New-Object System.Text.ASCIIEncoding
        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease
        [System.IO.File]::WriteAllText(
            $checksum,
            ('{0}  {1}' -f $digest, [System.IO.Path]::GetFileName($archive)) + "`n",
            $ascii
        )
        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease
        Assert-PrivateStagingFile -Path $checksum -Staging $staging -Label 'release checksum'

        $currentHead = @(Invoke-GitText -Repository $repositoryRoot -Arguments @('rev-parse', '--verify', 'HEAD^{commit}'))
        if ($currentHead.Count -ne 1 -or $currentHead[0] -cne $headOid) {
            Throw-CommonError 'HEAD changed during deployment preparation'
        }
        Assert-CleanGitHead -Repository $repositoryRoot
        Assert-FileNotTracked -Repository $repositoryRoot -Path ([System.IO.Path]::Combine($repositoryRoot, '.env')) -Label '.env'
        Assert-FileNotTracked -Repository $repositoryRoot -Path ([System.IO.Path]::Combine($repositoryRoot, 'remote.env')) -Label 'repository remote.env'
        Assert-FileNotTracked -Repository $repositoryRoot -Path $remoteEnv -Label 'selected remote.env'

        Assert-PrivateDeploymentBoundary `
            -ArtifactParent $artifactParent `
            -ArtifactParentLease $artifactParentLease `
            -Staging $staging `
            -StagingLease $stagingLease
        Assert-PrivateStagingFile -Path $archive -Staging $staging -Label 'release archive'
        Assert-PrivateStagingFile -Path $checksum -Staging $staging -Label 'release checksum'
        Assert-PrivateStagingFile -Path $runtimeEnvSnapshot -Staging $staging -Label 'runtime environment snapshot'
        Assert-PrivateStagingFile -Path $receiverSnapshot -Staging $staging -Label 'release receiver snapshot'

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
        if ($null -ne $stagingLease) {
            $stagingLease.Dispose()
        }
        if ($null -ne $artifactParentLease) {
            $artifactParentLease.Dispose()
        }
        if ($null -ne $staging -and [System.IO.Directory]::Exists($staging)) {
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
