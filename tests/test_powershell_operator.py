import contextlib
import base64
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import repo_path


def available_powershells() -> list[str]:
    executables: list[str] = []
    seen: set[str] = set()
    for name in ("powershell", "pwsh"):
        discovered = shutil.which(name)
        if not discovered:
            continue
        canonical = str(Path(discovered).resolve()).casefold()
        if canonical in seen:
            continue
        result = subprocess.run(
            [discovered, "-NoProfile", "-Command", "exit 0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            executables.append(discovered)
            seen.add(canonical)
    return executables


POWERSHELLS = available_powershells()
REAL_GIT = shutil.which("git")


def run_git(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def read_operations(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    operations: list[list[str]] = []
    for record in log.read_bytes().splitlines():
        fields = record.split(b"\0")
        if fields and fields[-1] == b"":
            fields.pop()
        operations.append([field.decode("utf-8") for field in fields])
    return operations


def scp_positionals(arguments: list[str]) -> list[str]:
    positionals: list[str] = []
    index = 0
    while index < len(arguments):
        if arguments[index] in ("-P", "-i"):
            index += 2
        elif arguments[index].startswith("-"):
            index += 1
        else:
            positionals.append(arguments[index])
            index += 1
    return positionals


FAKE_COMMON = r"""
function Add-FakeRecord {
    param([string]$Kind, [string[]]$Values, [string]$Path)
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $record = (@($Kind) + @($Values) -join [char]0) + [char]0 + "`n"
    [System.IO.File]::AppendAllText($Path, $record, $utf8)
}
"""


FAKE_GIT = FAKE_COMMON + r"""
$ErrorActionPreference = 'Stop'
$remaining = @($args)
if (-not [string]::IsNullOrEmpty($env:STACK_GIT_LOG)) {
    Add-FakeRecord -Kind 'git' -Values $remaining -Path $env:STACK_GIT_LOG
}
& $env:STACK_REAL_GIT @remaining
$status = $LASTEXITCODE
if ($status -ne 0) { exit $status }
if (($remaining -contains 'archive') -and
    -not [string]::IsNullOrEmpty($env:STACK_GIT_MUTATION) -and
    -not [System.IO.File]::Exists($env:STACK_GIT_MUTATION_MARKER)) {
    [System.IO.File]::WriteAllText($env:STACK_GIT_MUTATION_MARKER, 'mutated')
    switch ($env:STACK_GIT_MUTATION) {
        'move-head' {
            [System.IO.File]::AppendAllText(
                [System.IO.Path]::Combine($env:STACK_GIT_REPO, 'committed.txt'),
                "moved HEAD`n"
            )
            & $env:STACK_REAL_GIT -C $env:STACK_GIT_REPO add committed.txt
            & $env:STACK_REAL_GIT -C $env:STACK_GIT_REPO commit -m 'move head during deploy' | Out-Null
        }
        'edit-receiver' {
            [System.IO.File]::AppendAllText(
                [System.IO.Path]::Combine($env:STACK_GIT_REPO, 'scripts', 'remote', 'deploy-release.sh'),
                "`nprintf `"MUTATED RECEIVER\\n`" >&2`n"
            )
        }
        'edit-env' {
            [System.IO.File]::AppendAllText(
                [System.IO.Path]::Combine($env:STACK_GIT_REPO, '.env'),
                "`nMUTATED_DURING_ARCHIVE=yes`n"
            )
        }
        default { exit 97 }
    }
}
exit 0
"""


FAKE_DOCKER = FAKE_COMMON + r"""
$ErrorActionPreference = 'Stop'
$remaining = @($args)
if (-not [string]::IsNullOrEmpty($env:STACK_DOCKER_LOG)) {
    Add-FakeRecord -Kind 'docker' -Values $remaining -Path $env:STACK_DOCKER_LOG
}
if (($remaining -contains 'config') -and
    -not [string]::IsNullOrEmpty($env:STACK_FAKE_REQUIRE_OPENSEARCH_B64)) {
    if ([string]::IsNullOrEmpty($env:STACK_OPENSEARCH_CONFIG_B64) -or
        [string]::IsNullOrEmpty($env:STACK_OPENSEARCH_ENTRYPOINT_B64)) { exit 61 }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $configDigest = ([System.BitConverter]::ToString(
            $sha.ComputeHash([System.Convert]::FromBase64String($env:STACK_OPENSEARCH_CONFIG_B64))
        ) -replace '-', '').ToLowerInvariant()
        $entrypointDigest = ([System.BitConverter]::ToString(
            $sha.ComputeHash([System.Convert]::FromBase64String($env:STACK_OPENSEARCH_ENTRYPOINT_B64))
        ) -replace '-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
    [System.IO.File]::WriteAllText(
        $env:STACK_FAKE_CONTENT_LOG,
        "config/opensearch/opensearch.yml $configDigest`nconfig/opensearch/docker-entrypoint.sh $entrypointDigest`n",
        $utf8
    )
}
if ($remaining -contains 'info') { exit 1 }
exit 0
"""


NATIVE_COMMAND_FAKE = r"""
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

public static class NativeCommandFake
{
    private static void WriteRecord(string path, IEnumerable<string> fields)
    {
        using (FileStream stream = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
        {
            foreach (string field in fields)
            {
                byte[] bytes = new UTF8Encoding(false).GetBytes(field ?? string.Empty);
                stream.Write(bytes, 0, bytes.Length);
                stream.WriteByte(0);
            }
            stream.WriteByte((byte)'\n');
        }
    }

    public static int Main(string[] arguments)
    {
        string executable = Environment.GetCommandLineArgs()[0];
        string kind = Path.GetFileNameWithoutExtension(executable).ToLowerInvariant();
        string log = Environment.GetEnvironmentVariable("STACK_FAKE_LOG");
        List<string> record = new List<string>();
        record.Add(kind);
        record.AddRange(arguments);
        WriteRecord(log, record);

        if (kind == "ssh")
        {
            string remoteLog = Environment.GetEnvironmentVariable("STACK_FAKE_REMOTE_LOG");
            if (!string.IsNullOrEmpty(remoteLog))
            {
                int index = 0;
                while (index < arguments.Length)
                {
                    if (arguments[index] == "-p" || arguments[index] == "-i") { index += 2; continue; }
                    if (arguments[index] == "--") { index += 1; break; }
                    if (arguments[index].StartsWith("-", StringComparison.Ordinal)) { index += 1; continue; }
                    break;
                }
                List<string> remote = new List<string>();
                remote.Add("ssh-remote");
                remote.Add(index < arguments.Length ? arguments[index] : string.Empty);
                index += 1;
                while (index < arguments.Length) { remote.Add(arguments[index]); index += 1; }
                WriteRecord(remoteLog, remote);
            }
        }
        else if (kind == "scp")
        {
            string capture = Environment.GetEnvironmentVariable("STACK_FAKE_CAPTURE_DIR");
            if (!string.IsNullOrEmpty(capture))
            {
                List<string> positionals = new List<string>();
                for (int index = 0; index < arguments.Length; )
                {
                    if (arguments[index] == "-P" || arguments[index] == "-i") { index += 2; continue; }
                    if (arguments[index].StartsWith("-", StringComparison.Ordinal)) { index += 1; continue; }
                    positionals.Add(arguments[index]);
                    index += 1;
                }
                for (int index = 0; index + 1 < positionals.Count; index += 1)
                {
                    if (File.Exists(positionals[index]))
                    {
                        File.Copy(
                            positionals[index],
                            Path.Combine(capture, Path.GetFileName(positionals[index])),
                            true
                        );
                    }
                }
            }
        }
        return 0;
    }
}
"""


class PowerShellOperatorTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.native_fake_directory = None
        cls.native_fake_executable = None
        if not POWERSHELLS:
            return
        cls.native_fake_directory = tempfile.TemporaryDirectory()
        cls.native_fake_executable = Path(cls.native_fake_directory.name) / "native-command-fake.exe"
        environment = os.environ.copy()
        environment["NATIVE_FAKE_SOURCE_B64"] = base64.b64encode(
            NATIVE_COMMAND_FAKE.encode("utf-8")
        ).decode("ascii")
        environment["NATIVE_FAKE_OUTPUT"] = str(cls.native_fake_executable)
        command = (
            "$source=[Text.Encoding]::UTF8.GetString("
            "[Convert]::FromBase64String($env:NATIVE_FAKE_SOURCE_B64)); "
            "Add-Type -TypeDefinition $source -Language CSharp "
            "-OutputAssembly $env:NATIVE_FAKE_OUTPUT -OutputType ConsoleApplication"
        )
        result = subprocess.run(
            [
                POWERSHELLS[0],
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            env=environment,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not cls.native_fake_executable.is_file():
            raise RuntimeError(f"native command fake compilation failed: {result.stderr}")

    @classmethod
    def tearDownClass(cls):
        if cls.native_fake_directory is not None:
            cls.native_fake_directory.cleanup()

    def setUp(self):
        if not POWERSHELLS:
            self.skipTest("PowerShell is not installed")
        if REAL_GIT is None:
            self.skipTest("Git is not installed")

    @contextlib.contextmanager
    def operator_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "repository with spaces;quo'te$(literal)"
            fake_dir = base / "controlled fakes"
            root.mkdir()
            fake_dir.mkdir()
            shutil.copytree(repo_path("scripts"), root / "scripts")
            shutil.copytree(repo_path("config"), root / "config")
            for name in (
                "compose.yaml",
                "versions.env",
                ".env.example",
                "remote.env.example",
                ".gitattributes",
                ".gitignore",
            ):
                shutil.copy2(repo_path(name), root / name)
            with (root / ".gitignore").open("a", encoding="utf-8") as handle:
                handle.write("ignored-note.txt\n")
            (root / "committed.txt").write_text("committed HEAD\n", encoding="utf-8")
            run_git(root, "init", "-b", "main")
            run_git(root, "config", "user.name", "Operator Tests")
            run_git(root, "config", "user.email", "operator@example.invalid")
            run_git(root, "config", "core.autocrlf", "false")
            run_git(root, "add", "-A")
            run_git(root, "commit", "-m", "fixture")
            shutil.copy2(repo_path("tests/fixtures/stack.env"), root / ".env")
            (root / "ignored-note.txt").write_text("ignored local data\n", encoding="utf-8")
            shutil.copy2(self.native_fake_executable, fake_dir / "ssh.exe")
            shutil.copy2(self.native_fake_executable, fake_dir / "scp.exe")
            (fake_dir / "git.ps1").write_text(FAKE_GIT, encoding="utf-8", newline="\n")
            (fake_dir / "docker.ps1").write_text(FAKE_DOCKER, encoding="utf-8", newline="\n")
            yield base, root, fake_dir

    def environment(
        self,
        fake_dir: Path,
        log: Path,
        capture: Path | None = None,
        remote_env: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("STACK_FAKE_")
            and not key.startswith("STACK_DOCKER_")
            and not key.startswith("STACK_GIT_")
            and key not in ("STACK_OPENSEARCH_CONFIG_B64", "STACK_OPENSEARCH_ENTRYPOINT_B64")
        }
        environment.update(
            {
                "PATH": str(fake_dir),
                "PATHEXT": ".EXE;.PS1;.CMD;.BAT",
                "STACK_FAKE_LOG": str(log),
                "STACK_FAKE_REMOTE_LOG": str(log.with_suffix(".remote.log")),
                "STACK_REMOTE_ENV": str(remote_env or repo_path("tests/fixtures/remote.env")),
                "STACK_REAL_GIT": str(REAL_GIT),
            }
        )
        if capture is not None:
            environment["STACK_FAKE_CAPTURE_DIR"] = str(capture)
        if extra_env:
            environment.update(extra_env)
        return environment

    def run_script(
        self,
        shell: str,
        root: Path,
        fake_dir: Path,
        script: str,
        *arguments: str,
        log: Path,
        capture: Path | None = None,
        remote_env: Path | None = None,
        input_text: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "scripts" / script),
                *arguments,
            ],
            cwd=root,
            env=self.environment(fake_dir, log, capture, remote_env, extra_env),
            input=input_text,
            capture_output=True,
            text=True,
        )

    def run_script_with_prelude(
        self,
        shell: str,
        root: Path,
        fake_dir: Path,
        script: str,
        prelude: str,
        *arguments: str,
        log: Path,
        capture: Path | None = None,
        remote_env: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        quoted_script = str(root / "scripts" / script).replace("'", "''")
        quoted_arguments = ",".join(
            "'" + argument.replace("'", "''") + "'" for argument in arguments
        )
        command = f"{prelude}; & '{quoted_script}' @({quoted_arguments}); exit $LASTEXITCODE"
        return subprocess.run(
            [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=root,
            env=self.environment(fake_dir, log, capture, remote_env, extra_env),
            capture_output=True,
            text=True,
        )

    def remote_env_with(self, destination: Path, **values: str) -> Path:
        content = repo_path("tests/fixtures/remote.env").read_text(encoding="utf-8")
        for key, value in values.items():
            replacement = f"{key}={value}"
            content = re.sub(
                rf"(?m)^{re.escape(key)}=.*$",
                lambda _match, replacement=replacement: replacement,
                content,
            )
        destination.write_text(content, encoding="utf-8", newline="\n")
        return destination

    def assert_for_each_shell(self, callback):
        for shell in POWERSHELLS:
            with self.subTest(shell=Path(shell).name):
                callback(shell)

    def test_bootstrap_matches_serialized_bash_operation_contract(self):
        def verify(shell: str):
            with self.operator_repository() as (base, root, fake_dir):
                log = base / "fake.log"
                result = self.run_script(shell, root, fake_dir, "bootstrap.ps1", log=log)
                self.assertEqual(0, result.returncode, result.stderr)
                operations = read_operations(log)
                self.assertEqual(["ssh", "scp", "ssh", "ssh"], [item[0] for item in operations])
                scp = operations[1][1:]
                self.assertEqual(["-P", "2222", "-i", "C:/fixtures/test-remote-infra-stack"], scp[:4])
                self.assertTrue(Path(scp[4]).is_absolute(), scp[4])
                self.assertTrue(scp[4].replace("\\", "/").endswith("/scripts/remote/bootstrap-host.sh"))
                remote_bootstrap = scp[5].split(":", 1)[1]
                self.assertRegex(remote_bootstrap, r"^remote-infra-stack/incoming/bootstrap-[A-Za-z0-9]+\.sh$")
                for operation in (item for item in operations if item[0] == "ssh"):
                    target_index = operation.index("tester@test-remote-infra-stack")
                    self.assertEqual("--", operation[target_index - 1])
                    self.assertEqual(1, len(operation[target_index + 1 :]))
                    shlex.split(operation[-1])
                self.assertEqual(["sudo", "bash", remote_bootstrap, "--install"], shlex.split(operations[2][-1]))
                self.assertEqual(["rm", "-f", "--", remote_bootstrap], shlex.split(operations[3][-1]))

        self.assert_for_each_shell(verify)

    def test_deploy_matches_archive_upload_receiver_and_cleanup_contract(self):
        def verify(shell: str):
            with self.operator_repository() as (base, root, fake_dir):
                log = base / "fake.log"
                capture = base / "capture"
                capture.mkdir()
                result = self.run_script(
                    shell, root, fake_dir, "deploy.ps1", "core", "vector", log=log, capture=capture
                )
                self.assertEqual(0, result.returncode, result.stderr)
                operations = read_operations(log)
                self.assertEqual(["scp", "ssh", "ssh"], [item[0] for item in operations])
                scp = operations[0][1:]
                self.assertEqual(["-P", "2222", "-i", "C:/fixtures/test-remote-infra-stack"], scp[:4])
                positionals = scp_positionals(scp)
                sources, destination = positionals[:-1], positionals[-1]
                self.assertEqual(4, len(sources))
                self.assertTrue(all(Path(source).is_absolute() for source in sources))
                self.assertTrue(
                    all("repository with spaces;quo'te$(literal)" in source for source in sources),
                    sources,
                )
                staging_parents = {Path(source).parent for source in sources}
                self.assertEqual(1, len(staging_parents))
                self.assertRegex(
                    next(iter(staging_parents)).as_posix(),
                    r"/\.artifacts/deploy\.[A-Za-z0-9]+$",
                )
                uploaded = [Path(source).name for source in sources]
                archive_name = next(name for name in uploaded if name.endswith(".tar.gz"))
                self.assertRegex(
                    archive_name,
                    r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[A-Za-z0-9]+\.tar\.gz$",
                )
                self.assertIn(f"{archive_name}.sha256", uploaded)
                env_name = next(name for name in uploaded if name.startswith("runtime-env-"))
                receiver_name = next(name for name in uploaded if name.startswith("deploy-release-"))
                self.assertEqual(4, len(set(uploaded)))
                self.assertEqual("tester@test-remote-infra-stack:remote-infra-stack/incoming/", destination)

                archive = capture / archive_name
                with tarfile.open(archive, "r:gz") as release:
                    members = {member.name.removeprefix("./") for member in release.getmembers()}
                self.assertIn("committed.txt", members)
                self.assertNotIn(".env", members)
                checksum = capture / f"{archive_name}.sha256"
                self.assertEqual(
                    f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive_name}\n",
                    checksum.read_text(encoding="ascii"),
                )
                expected_receiver = subprocess.run(
                    ["git", "-C", str(root), "show", "HEAD:scripts/remote/deploy-release.sh"],
                    check=True,
                    capture_output=True,
                ).stdout
                captured_receiver = (capture / receiver_name).read_bytes()
                self.assertEqual(expected_receiver, captured_receiver)
                self.assertNotIn(b"\r\n", captured_receiver)

                receiver_argv = shlex.split(operations[1][-1])
                self.assertEqual(
                    [
                        "bash", f"remote-infra-stack/incoming/{receiver_name}",
                        "--root", "remote-infra-stack",
                        "--archive", f"remote-infra-stack/incoming/{archive_name}",
                        "--checksum", f"remote-infra-stack/incoming/{archive_name}.sha256",
                        "--env", f"remote-infra-stack/incoming/{env_name}",
                        "--profiles", "core,vector",
                    ],
                    receiver_argv,
                )
                cleanup_argv = shlex.split(operations[2][-1])
                self.assertEqual(["rm", "-f", "--"], cleanup_argv[:3])
                self.assertEqual(
                    {f"remote-infra-stack/incoming/{name}" for name in uploaded},
                    set(cleanup_argv[3:]),
                )
                self.assertFalse((root / ".artifacts").exists())

        self.assert_for_each_shell(verify)

    def test_deploy_rejects_symlinked_artifact_parent_before_remote_calls(self):
        def verify(shell: str):
            with self.operator_repository() as (base, root, fake_dir):
                outside = base / "outside artifacts"
                outside.mkdir()
                artifact_parent = root / ".artifacts"
                junction_created = False
                try:
                    os.symlink(outside, artifact_parent, target_is_directory=True)
                except OSError as error:
                    if os.name != "nt":
                        self.skipTest(f"directory symlinks unavailable: {error}")
                    junction = subprocess.run(
                        ["cmd.exe", "/d", "/c", "mklink", "/J", str(artifact_parent), str(outside)],
                        capture_output=True,
                        text=True,
                    )
                    if junction.returncode != 0:
                        self.skipTest(f"directory links unavailable: {error}; {junction.stderr}")
                    junction_created = True
                try:
                    log = base / "fake.log"
                    result = self.run_script(
                        shell, root, fake_dir, "deploy.ps1", "core", log=log
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("real non-symlink", result.stderr)
                    self.assertEqual([], list(outside.iterdir()))
                    self.assertEqual([], read_operations(log))
                finally:
                    if junction_created and artifact_parent.exists():
                        os.rmdir(artifact_parent)

        self.assert_for_each_shell(verify)

    def test_deploy_fails_closed_when_private_staging_acl_cannot_be_proven(self):
        def verify(shell: str):
            cases = (
                (
                    "function global:Set-Acl { [CmdletBinding()] param($LiteralPath, $AclObject); "
                    "if ([IO.Path]::GetFileName([string]$LiteralPath) -clike 'deploy.*') { "
                    "throw 'forced ACL-set failure' }; "
                    "Microsoft.PowerShell.Security\\Set-Acl @PSBoundParameters }",
                    "could not establish a private staging ACL",
                ),
                (
                    "function global:Set-Acl { [CmdletBinding()] param($LiteralPath, $AclObject); "
                    "if ([IO.Path]::GetFileName([string]$LiteralPath) -clike 'deploy.*') { return }; "
                    "Microsoft.PowerShell.Security\\Set-Acl @PSBoundParameters }",
                    "private staging ACL verification failed",
                ),
            )
            for prelude, message in cases:
                with self.subTest(message=message), self.operator_repository() as (base, root, fake_dir):
                    log = base / "fake.log"
                    capture = base / "capture"
                    capture.mkdir()
                    original_secret = (root / ".env").read_bytes()
                    result = self.run_script_with_prelude(
                        shell,
                        root,
                        fake_dir,
                        "deploy.ps1",
                        prelude,
                        "core",
                        log=log,
                        capture=capture,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(message, result.stderr)
                    self.assertEqual([], read_operations(log))
                    self.assertEqual([], list(capture.iterdir()))
                    self.assertFalse((root / ".artifacts").exists())
                    self.assertEqual(original_secret, (root / ".env").read_bytes())
                    self.assertEqual([], list(base.rglob("runtime-env-*")))

        self.assert_for_each_shell(verify)

    def test_deploy_pins_verified_artifact_and_staging_directories_against_replacement(self):
        def verify(shell: str):
            attacks = (
                (
                    "rename-directory",
                    """
                    [System.IO.Directory]::Move($LiteralPath, $env:STACK_ATTACK_MOVED)
                    [void][System.IO.Directory]::CreateDirectory($LiteralPath)
                    [System.IO.File]::WriteAllText($env:STACK_ATTACK_MARKER, 'renamed')
                    """,
                ),
                (
                    "delete-junction",
                    """
                    [System.IO.Directory]::Delete($LiteralPath, $false)
                    New-Item -ItemType Junction -Path $LiteralPath -Target $env:STACK_ATTACK_TARGET -ErrorAction Stop | Out-Null
                    [System.IO.File]::WriteAllText($env:STACK_ATTACK_MARKER, 'junction')
                    """,
                ),
            )
            for name, attack in attacks:
                with self.subTest(attack=name), self.operator_repository() as (base, root, fake_dir):
                    log = base / "fake.log"
                    capture = base / "capture"
                    capture.mkdir()
                    moved = base / "renamed staging"
                    target = base / "junction target"
                    target.mkdir()
                    attempt_marker = base / f"{name}.attempted"
                    marker = base / f"{name}.marker"
                    original_secret = (root / ".env").read_bytes()
                    prelude = f"""
                    $global:StackAttackAttempted = $false
                    function global:Get-Acl {{
                        [CmdletBinding()]
                        param([string]$LiteralPath)
                        $acl = Microsoft.PowerShell.Security\\Get-Acl -LiteralPath $LiteralPath -ErrorAction Stop
                        if (-not $global:StackAttackAttempted -and
                            [System.IO.Path]::GetFileName([string]$LiteralPath) -clike 'deploy.*' -and
                            $acl.AreAccessRulesProtected) {{
                            $global:StackAttackAttempted = $true
                            [System.IO.File]::WriteAllText($env:STACK_ATTACK_ATTEMPT, 'attempted')
                            {attack}
                        }}
                        return $acl
                    }}
                    """
                    result = self.run_script_with_prelude(
                        shell,
                        root,
                        fake_dir,
                        "deploy.ps1",
                        prelude,
                        "core",
                        log=log,
                        capture=capture,
                        extra_env={
                            "STACK_ATTACK_ATTEMPT": str(attempt_marker),
                            "STACK_ATTACK_MARKER": str(marker),
                            "STACK_ATTACK_MOVED": str(moved),
                            "STACK_ATTACK_TARGET": str(target),
                        },
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertTrue(attempt_marker.exists(), result.stderr)
                    self.assertFalse(marker.exists(), result.stderr)
                    self.assertFalse(moved.exists(), result.stderr)
                    self.assertEqual([], list(target.iterdir()), result.stderr)
                    self.assertEqual([], read_operations(log))
                    self.assertEqual([], list(capture.iterdir()))
                    self.assertFalse((root / ".artifacts").exists(), result.stderr)
                    self.assertEqual(original_secret, (root / ".env").read_bytes())
                    self.assertEqual([], list(base.rglob("runtime-env-*")))

        self.assert_for_each_shell(verify)

    def test_deploy_fails_closed_when_artifact_parent_acl_hardening_is_a_no_op(self):
        def verify(shell: str):
            with self.operator_repository() as (base, root, fake_dir):
                artifact_parent = root / ".artifacts"
                artifact_parent.mkdir()
                log = base / "fake.log"
                capture = base / "capture"
                capture.mkdir()
                original_secret = (root / ".env").read_bytes()
                prelude = """
                function global:Set-Acl {
                    [CmdletBinding()]
                    param([string]$LiteralPath, $AclObject)
                    if ([System.IO.Path]::GetFileName([string]$LiteralPath) -ceq '.artifacts') {
                        return
                    }
                    Microsoft.PowerShell.Security\\Set-Acl @PSBoundParameters
                }
                """
                result = self.run_script_with_prelude(
                    shell,
                    root,
                    fake_dir,
                    "deploy.ps1",
                    prelude,
                    "core",
                    log=log,
                    capture=capture,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("private artifact parent ACL", result.stderr)
                self.assertEqual([], read_operations(log))
                self.assertEqual([], list(capture.iterdir()))
                self.assertEqual([], list(artifact_parent.iterdir()))
                self.assertEqual(original_secret, (root / ".env").read_bytes())
                self.assertEqual([], list(base.rglob("runtime-env-*")))

        self.assert_for_each_shell(verify)

    def test_deploy_uses_get_file_hash_sha256_for_exact_checksum_record(self):
        def verify(shell: str):
            with self.operator_repository() as (base, root, fake_dir):
                log = base / "fake.log"
                capture = base / "capture"
                capture.mkdir()
                digest = "a" * 64
                prelude = (
                    "function global:Invoke-FakeFileHash { [CmdletBinding()] "
                    "param([string]$LiteralPath, [string]$Algorithm); "
                    "if ($Algorithm -cne 'SHA256') { throw 'wrong hash algorithm' }; "
                    f"[pscustomobject]@{{ Hash = '{digest}'; Path = $LiteralPath }} }}; "
                    "Set-Alias -Name Get-FileHash -Value Invoke-FakeFileHash -Scope Global"
                )
                result = self.run_script_with_prelude(
                    shell,
                    root,
                    fake_dir,
                    "deploy.ps1",
                    prelude,
                    "core",
                    log=log,
                    capture=capture,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                checksum_files = list(capture.glob("*.sha256"))
                self.assertEqual(1, len(checksum_files))
                archive_name = checksum_files[0].name.removesuffix(".sha256")
                self.assertEqual(
                    f"{digest}  {archive_name}\n",
                    checksum_files[0].read_text(encoding="ascii"),
                )

        self.assert_for_each_shell(verify)

    def test_deploy_rejects_dirty_and_tracked_secret_inputs_before_remote_calls(self):
        def verify(shell: str):
            mutations = []
            mutations.append(
                (
                    lambda root: (root / "committed.txt").write_text("dirty\n", encoding="utf-8"),
                    "clean committed Git HEAD",
                )
            )

            def staged_change(root: Path):
                (root / "committed.txt").write_text("staged\n", encoding="utf-8")
                run_git(root, "add", "committed.txt")

            def untracked_change(root: Path):
                (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")

            def tracked_env(root: Path):
                run_git(root, "add", "-f", ".env")
                run_git(root, "commit", "-m", "track env")

            def tracked_repo_remote(root: Path):
                shutil.copy2(repo_path("tests/fixtures/remote.env"), root / "remote.env")
                run_git(root, "add", "-f", "remote.env")
                run_git(root, "commit", "-m", "track remote env")

            mutations.extend(
                (
                    (staged_change, "clean committed Git HEAD"),
                    (untracked_change, "clean committed Git HEAD"),
                    (tracked_env, ".env must not be tracked"),
                    (tracked_repo_remote, "repository remote.env must not be tracked"),
                )
            )
            for mutate, message in mutations:
                with self.subTest(mutation=mutate.__name__), self.operator_repository() as (base, root, fake_dir):
                    mutate(root)
                    log = base / "fake.log"
                    result = self.run_script(shell, root, fake_dir, "deploy.ps1", "core", log=log)
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(message, result.stderr)
                    self.assertEqual([], read_operations(log))

        self.assert_for_each_shell(verify)

    def test_deploy_uses_one_full_oid_and_rejects_head_or_receiver_mutation(self):
        def verify(shell: str):
            for mutation, expected in (
                ("move-head", "HEAD changed during deployment preparation"),
                ("edit-receiver", "clean committed Git HEAD"),
            ):
                with self.subTest(mutation=mutation), self.operator_repository() as (base, root, fake_dir):
                    log = base / "fake.log"
                    git_log = base / "git.log"
                    marker = base / f"{mutation}.marker"
                    expected_oid = run_git(root, "rev-parse", "HEAD").stdout.strip()
                    environment = {
                        "STACK_GIT_LOG": str(git_log),
                        "STACK_GIT_MUTATION": mutation,
                        "STACK_GIT_MUTATION_MARKER": str(marker),
                        "STACK_GIT_REPO": str(root),
                    }
                    result = self.run_script(
                        shell, root, fake_dir, "deploy.ps1", "core", log=log, extra_env=environment
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(expected, result.stderr)
                    self.assertEqual([], read_operations(log))
                    archives = [item for item in read_operations(git_log) if "archive" in item]
                    self.assertEqual(1, len(archives))
                    self.assertEqual(expected_oid, archives[0][-1])

        self.assert_for_each_shell(verify)

    def test_deploy_snapshots_env_before_archive_and_sanitizes_inherited_hooks(self):
        def verify(shell: str):
            with self.operator_repository() as (base, root, fake_dir):
                log = base / "fake.log"
                capture = base / "capture"
                capture.mkdir()
                original = (root / ".env").read_bytes()
                marker = base / "edit-env.marker"
                result = self.run_script(
                    shell,
                    root,
                    fake_dir,
                    "deploy.ps1",
                    "core",
                    log=log,
                    capture=capture,
                    extra_env={
                        "STACK_GIT_MUTATION": "edit-env",
                        "STACK_GIT_MUTATION_MARKER": str(marker),
                        "STACK_GIT_REPO": str(root),
                    },
                )
                self.assertEqual(0, result.returncode, result.stderr)
                env_uploads = [path for path in capture.iterdir() if path.name.startswith("runtime-env-")]
                self.assertEqual(1, len(env_uploads))
                self.assertEqual(original, env_uploads[0].read_bytes())

            with self.operator_repository() as (base, root, fake_dir):
                inherited_marker = base / "inherited.marker"
                inherited = {
                    "STACK_GIT_MUTATION": "move-head",
                    "STACK_GIT_MUTATION_MARKER": str(inherited_marker),
                    "STACK_GIT_REPO": str(root),
                }
                log = base / "fake.log"
                with mock.patch.dict(os.environ, inherited):
                    result = self.run_script(shell, root, fake_dir, "deploy.ps1", "core", log=log)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertFalse(inherited_marker.exists())

        self.assert_for_each_shell(verify)

    def test_stack_status_logs_profiles_and_destroy_match_bash_semantics(self):
        def verify(shell: str):
            with self.operator_repository() as (base, root, fake_dir):
                log = base / "fake.log"
                result = self.run_script(shell, root, fake_dir, "stack.ps1", "status", log=log)
                self.assertEqual(0, result.returncode, result.stderr)
                operation = read_operations(log)[0]
                self.assertEqual("--", operation[-3])
                self.assertEqual("tester@test-remote-infra-stack", operation[-2])
                self.assertEqual(
                    ["bash", "remote-infra-stack/current/scripts/remote/stack.sh", "status"],
                    shlex.split(operation[-1]),
                )

            for arguments, message in (
                (("up", "tools"), "tools requires core"),
                (("up", "core", "core"), "duplicate profile"),
                (("stop", "unknown"), "unknown profile"),
                (("logs", "core;id"), "unknown log target"),
                (("logs", "app-postgres'bad"), "unknown log target"),
            ):
                with self.subTest(arguments=arguments), self.operator_repository() as (base, root, fake_dir):
                    log = base / "fake.log"
                    result = self.run_script(shell, root, fake_dir, "stack.ps1", *arguments, log=log)
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(message, result.stderr)
                    self.assertEqual([], read_operations(log))

            with self.operator_repository() as (base, root, fake_dir):
                log = base / "fake.log"
                result = self.run_script(
                    shell,
                    root,
                    fake_dir,
                    "stack.ps1",
                    "destroy",
                    log=log,
                    input_text="test-remote-infra-stack\nDESTROY-remote-infra-stack\n",
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(
                    [
                        "bash", "remote-infra-stack/current/scripts/remote/stack.sh",
                        "destroy", "remote-infra-stack", "DESTROY-remote-infra-stack",
                    ],
                    shlex.split(read_operations(log)[0][-1]),
                )

            for input_text, message in (
                ("wrong-host\nDESTROY-remote-infra-stack\n", "remote target confirmation did not match"),
                ("test-remote-infra-stack\nwrong-token\n", "destroy token did not match"),
            ):
                with self.subTest(message=message), self.operator_repository() as (base, root, fake_dir):
                    log = base / "fake.log"
                    result = self.run_script(
                        shell,
                        root,
                        fake_dir,
                        "stack.ps1",
                        "destroy",
                        log=log,
                        input_text=input_text,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(message, result.stderr)
                    self.assertEqual([], read_operations(log))

        self.assert_for_each_shell(verify)

    def test_remote_env_parser_is_allowlisted_strict_and_supports_empty_port(self):
        def verify(shell: str):
            base_content = repo_path("tests/fixtures/remote.env").read_text(encoding="utf-8")
            variants = (
                (base_content + "EXTRA=value\n", "unknown remote.env key"),
                (base_content + "REMOTE_HOST=duplicate\n", "duplicate remote.env key"),
                (base_content.replace("REMOTE_ROOT=remote-infra-stack", "REMOTE_ROOT=/srv/stack"), "relative REMOTE_ROOT"),
                (base_content.replace("REMOTE_ROOT=remote-infra-stack", "REMOTE_ROOT=C:/srv/stack"), "relative REMOTE_ROOT"),
                (base_content.replace("REMOTE_ROOT=remote-infra-stack", "REMOTE_ROOT=stack/../other"), "must not contain .."),
                (base_content.replace("REMOTE_HOST=test-remote-infra-stack", "REMOTE_HOST=$(touch evaluated)"), "unsupported characters"),
                (base_content.replace("REMOTE_HOST=test-remote-infra-stack", "REMOTE_HOST=-host"), "option prefix"),
                (base_content.replace("REMOTE_HOST=test-remote-infra-stack", "REMOTE_HOST=host:22"), "must not contain a colon"),
                (base_content.replace("REMOTE_USER=tester", "REMOTE_USER=-root"), "option prefix"),
                (base_content.replace("REMOTE_PORT=2222", "REMOTE_PORT=not-a-port"), "must be an integer"),
                (base_content.replace("REMOTE_PORT=2222", "REMOTE_PORT=65536"), "between 1 and 65535"),
            )
            for content, message in variants:
                with self.subTest(message=message), self.operator_repository() as (base, root, fake_dir):
                    remote_env = base / "invalid.env"
                    remote_env.write_text(content, encoding="utf-8", newline="\n")
                    log = base / "fake.log"
                    result = self.run_script(
                        shell, root, fake_dir, "stack.ps1", "status", log=log, remote_env=remote_env
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(message, result.stderr)
                    self.assertEqual([], read_operations(log))

            with self.operator_repository() as (base, root, fake_dir):
                remote_env = self.remote_env_with(base / "empty-port.env", REMOTE_PORT="")
                log = base / "fake.log"
                result = self.run_script(
                    shell, root, fake_dir, "stack.ps1", "status", log=log, remote_env=remote_env
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertNotIn("-p", read_operations(log)[0])

        self.assert_for_each_shell(verify)

    def test_configuration_dispatch_and_secrets_are_exactly_case_sensitive(self):
        def verify(shell: str):
            remote_content = repo_path("tests/fixtures/remote.env").read_text(encoding="utf-8")
            remote_variants = (
                (
                    remote_content.replace("REMOTE_HOST=test-remote-infra-stack", "remote_host=test-remote-infra-stack"),
                    "unknown remote.env key: remote_host",
                ),
                (remote_content.replace("REMOTE_PORT=2222", "REMOTE_PORT=+22"), "REMOTE_PORT must be an integer"),
                (remote_content.replace("REMOTE_PORT=2222", "REMOTE_PORT= 22"), "REMOTE_PORT must be an integer"),
                (remote_content.replace("REMOTE_PORT=2222", "REMOTE_PORT=22 "), "REMOTE_PORT must be an integer"),
            )
            for content, message in remote_variants:
                with self.subTest(message=message), self.operator_repository() as (base, root, fake_dir):
                    remote_env = base / "exact-remote.env"
                    remote_env.write_text(content, encoding="utf-8", newline="\n")
                    log = base / "fake.log"
                    result = self.run_script(
                        shell, root, fake_dir, "stack.ps1", "status", log=log, remote_env=remote_env
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(message, result.stderr)
                    self.assertEqual([], read_operations(log))

            for arguments, message in (
                (("UP", "core"), "unsupported stack action: UP"),
                (("up", "Core"), "unknown profile: Core"),
                (("logs", "App-Postgres"), "unknown log target: App-Postgres"),
            ):
                with self.subTest(arguments=arguments), self.operator_repository() as (base, root, fake_dir):
                    log = base / "fake.log"
                    result = self.run_script(shell, root, fake_dir, "stack.ps1", *arguments, log=log)
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(message, result.stderr)
                    self.assertEqual([], read_operations(log))

            stack_content = repo_path("tests/fixtures/stack.env").read_text(encoding="utf-8")
            stack_variants = (
                (
                    stack_content.replace("APP_POSTGRES_USER=", "app_postgres_user=", 1),
                    "missing required .env key: APP_POSTGRES_USER",
                ),
                (
                    re.sub(
                        r"(?m)^OPENSEARCH_INITIAL_ADMIN_PASSWORD=.*$",
                        "OPENSEARCH_INITIAL_ADMIN_PASSWORD=alllowercase1234",
                        stack_content,
                    ),
                    "OPENSEARCH_INITIAL_ADMIN_PASSWORD does not meet",
                ),
                (
                    re.sub(
                        r"(?m)^OPENSEARCH_INITIAL_ADMIN_PASSWORD=.*$",
                        "OPENSEARCH_INITIAL_ADMIN_PASSWORD=ALLUPPERCASE1234",
                        stack_content,
                    ),
                    "OPENSEARCH_INITIAL_ADMIN_PASSWORD does not meet",
                ),
                (
                    re.sub(
                        r"(?m)^LANGFUSE_ENCRYPTION_KEY=.*$",
                        "LANGFUSE_ENCRYPTION_KEY=" + "A" * 64,
                        stack_content,
                    ),
                    "64 lowercase hexadecimal",
                ),
            )
            for content, message in stack_variants:
                with self.subTest(message=message), self.operator_repository() as (base, root, fake_dir):
                    (root / ".env").write_text(content, encoding="utf-8", newline="\n")
                    remote_env = self.remote_env_with(base / "remote.env", REMOTE_IDENTITY_FILE="")
                    log = base / "fake.log"
                    result = self.run_script(
                        shell, root, fake_dir, "check.ps1", "core", log=log, remote_env=remote_env
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(message, result.stderr)
                    self.assertEqual([], read_operations(log))

        self.assert_for_each_shell(verify)

    def test_posix_serializer_preserves_apostrophes_and_metacharacters(self):
        arguments = ["semi;colon", "$(touch escaped)", "quo'te", "space value"]
        for shell in POWERSHELLS:
            with self.subTest(shell=Path(shell).name), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "escaped"
                environment = os.environ.copy()
                for index, argument in enumerate(arguments):
                    environment[f"SERIALIZER_ARG_{index}"] = argument
                command = (
                    "$module=Import-Module '"
                    + str(repo_path("scripts/lib/Common.psm1")).replace("'", "''")
                    + "' -Force -PassThru -DisableNameChecking; "
                    + "$values=@($env:SERIALIZER_ARG_0,$env:SERIALIZER_ARG_1,$env:SERIALIZER_ARG_2,$env:SERIALIZER_ARG_3); "
                    + "ConvertTo-PosixCommand -Arguments $values"
                )
                result = subprocess.run(
                    [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                    env=environment,
                    capture_output=True,
                    text=True,
                    cwd=directory,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(arguments, shlex.split(result.stdout.strip()), repr(result.stdout))
                self.assertFalse(marker.exists())

    def test_native_windows_ssh_boundary_receives_one_serialized_remote_argument(self):
        arguments = ["semi;colon", "$(touch escaped)", "quo'te", "space value"]

        def verify(shell: str):
            with self.operator_repository() as (base, root, fake_dir):
                log = base / "fake.log"
                environment = self.environment(fake_dir, log)
                for index, argument in enumerate(arguments):
                    environment[f"NATIVE_BOUNDARY_ARG_{index}"] = argument
                module_path = str(root / "scripts" / "lib" / "Common.psm1").replace("'", "''")
                remote_env = str(repo_path("tests/fixtures/remote.env")).replace("'", "''")
                command = (
                    f"Import-Module '{module_path}' -Force -DisableNameChecking; "
                    f"$configuration=Import-RemoteEnv -Path '{remote_env}'; "
                    "$values=@($env:NATIVE_BOUNDARY_ARG_0,$env:NATIVE_BOUNDARY_ARG_1,"
                    "$env:NATIVE_BOUNDARY_ARG_2,$env:NATIVE_BOUNDARY_ARG_3); "
                    "Invoke-SshCommand -Configuration $configuration -CommandArguments $values"
                )
                result = subprocess.run(
                    [
                        shell,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        command,
                    ],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                operation = read_operations(log)[0]
                self.assertEqual("ssh", operation[0])
                target_index = operation.index("tester@test-remote-infra-stack")
                self.assertEqual("--", operation[target_index - 1])
                self.assertEqual(1, len(operation[target_index + 1 :]), operation)
                self.assertEqual(arguments, shlex.split(operation[target_index + 1]))
                remote = read_operations(log.with_suffix(".remote.log"))[0]
                self.assertEqual(
                    ["ssh-remote", "tester@test-remote-infra-stack", operation[target_index + 1]],
                    remote,
                )
                self.assertFalse((root / "escaped").exists())

        self.assert_for_each_shell(verify)

    def test_check_validates_identity_and_exact_opensearch_render_without_remote_calls(self):
        def verify(shell: str):
            with self.operator_repository() as (base, root, fake_dir):
                log = base / "fake.log"
                content_log = base / "content.log"
                remote_env = self.remote_env_with(base / "remote.env", REMOTE_IDENTITY_FILE="")
                result = self.run_script(
                    shell,
                    root,
                    fake_dir,
                    "check.ps1",
                    "search",
                    log=log,
                    remote_env=remote_env,
                    extra_env={
                        "STACK_FAKE_REQUIRE_OPENSEARCH_B64": "1",
                        "STACK_FAKE_CONTENT_LOG": str(content_log),
                    },
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("Local checks passed for profiles: search", result.stdout)
                self.assertIn("local Docker daemon is unavailable", result.stderr)
                self.assertEqual([], read_operations(log))
                content = content_log.read_text(encoding="utf-8")
                for relative in (
                    "config/opensearch/opensearch.yml",
                    "config/opensearch/docker-entrypoint.sh",
                ):
                    digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
                    self.assertIn(f"{relative} {digest}", content)

            with self.operator_repository() as (base, root, fake_dir):
                identity_directory = base / "identity directory"
                identity_directory.mkdir()
                remote_env = self.remote_env_with(
                    base / "remote.env", REMOTE_IDENTITY_FILE=str(identity_directory)
                )
                log = base / "fake.log"
                result = self.run_script(
                    shell, root, fake_dir, "check.ps1", "core", log=log, remote_env=remote_env
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("identity file must be a readable regular file", result.stderr)
                self.assertEqual([], read_operations(log))

        self.assert_for_each_shell(verify)

    def test_check_rejects_missing_dependencies_placeholders_and_sanitizes_hooks(self):
        def verify(shell: str):
            with self.operator_repository() as (base, root, fake_dir):
                (fake_dir / "scp.exe").unlink()
                log = base / "fake.log"
                remote_env = self.remote_env_with(base / "remote.env", REMOTE_IDENTITY_FILE="")
                result = self.run_script(
                    shell, root, fake_dir, "check.ps1", "core", log=log, remote_env=remote_env
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("required command is unavailable: scp", result.stderr)
                self.assertEqual([], read_operations(log))

            with self.operator_repository() as (base, root, fake_dir):
                shutil.copy2(root / ".env.example", root / ".env")
                log = base / "fake.log"
                remote_env = self.remote_env_with(base / "remote.env", REMOTE_IDENTITY_FILE="")
                result = self.run_script(
                    shell, root, fake_dir, "check.ps1", "core", log=log, remote_env=remote_env
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("placeholder", result.stderr)
                self.assertEqual([], read_operations(log))

            with self.operator_repository() as (base, root, fake_dir):
                log = base / "fake.log"
                inherited_log = base / "inherited.log"
                remote_env = self.remote_env_with(base / "remote.env", REMOTE_IDENTITY_FILE="")
                inherited = {
                    "STACK_FAKE_FAIL_COMMAND": "config",
                    "STACK_DOCKER_LOG": str(inherited_log),
                }
                with mock.patch.dict(os.environ, inherited):
                    result = self.run_script(
                        shell, root, fake_dir, "check.ps1", "core", log=log, remote_env=remote_env
                    )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertFalse(inherited_log.exists())

        self.assert_for_each_shell(verify)


if __name__ == "__main__":
    unittest.main()
