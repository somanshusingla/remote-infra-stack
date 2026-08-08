import base64
import contextlib
import os
import re
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import repo_path


PROFILE_FORWARDS = {
    "core": ["127.0.0.1:5432:127.0.0.1:15432", "127.0.0.1:6379:127.0.0.1:16379"],
    "vector": ["127.0.0.1:18000:127.0.0.1:18000", "127.0.0.1:18001:127.0.0.1:18001"],
    "dynamodb": ["127.0.0.1:18002:127.0.0.1:18002", "127.0.0.1:18003:127.0.0.1:18003"],
    "inference": ["127.0.0.1:11440:127.0.0.1:11440", "127.0.0.1:11441:127.0.0.1:11441"],
    "search": ["127.0.0.1:9200:127.0.0.1:9200", "127.0.0.1:5601:127.0.0.1:5601"],
    "observability": [
        "127.0.0.1:3000:127.0.0.1:3000",
        "127.0.0.1:9090:127.0.0.1:9090",
        "127.0.0.1:9091:127.0.0.1:9091",
    ],
    "tools": ["127.0.0.1:5050:127.0.0.1:5050", "127.0.0.1:5540:127.0.0.1:5540"],
}

NATIVE_SSH_FAKE = r"""
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

public static class NativeSshFake
{
    public static int Main(string[] arguments)
    {
        string log = Environment.GetEnvironmentVariable("STACK_FAKE_LOG");
        using (FileStream stream = new FileStream(log, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
        {
            List<string> fields = new List<string>();
            fields.Add("ssh");
            fields.AddRange(arguments);
            foreach (string field in fields)
            {
                byte[] bytes = new UTF8Encoding(false).GetBytes(field ?? string.Empty);
                stream.Write(bytes, 0, bytes.Length);
                stream.WriteByte(0);
            }
            stream.WriteByte((byte)'\n');
        }
        return 0;
    }
}
"""


def usable_bash() -> str | None:
    candidates: list[str] = []
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
            ]
        )
    discovered = shutil.which("bash")
    if discovered:
        candidates.append(discovered)
    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        result = subprocess.run(
            [candidate, "-c", "exit 0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return candidate
    return None


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


def bash_path(path: Path) -> str:
    resolved = path.resolve().as_posix()
    if os.name == "nt" and re.match(r"^[A-Za-z]:/", resolved):
        return f"/{resolved[0].lower()}{resolved[2:]}"
    return resolved


BASH = usable_bash()
POWERSHELLS = available_powershells()


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


def extract_forwards(arguments: list[str]) -> list[str]:
    forwards: list[str] = []
    index = 0
    while index < len(arguments):
        if arguments[index] == "-L":
            if index + 1 >= len(arguments):
                raise AssertionError("-L is missing its forwarding value")
            forwards.append(arguments[index + 1])
            index += 2
        else:
            index += 1
    return forwards


class TunnelTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.native_fake_directory = None
        cls.native_fake_executable = None
        if os.name != "nt" or not POWERSHELLS:
            return
        cls.native_fake_directory = tempfile.TemporaryDirectory()
        cls.native_fake_executable = Path(cls.native_fake_directory.name) / "ssh.exe"
        environment = os.environ.copy()
        environment["NATIVE_SSH_SOURCE_B64"] = base64.b64encode(
            NATIVE_SSH_FAKE.encode("utf-8")
        ).decode("ascii")
        environment["NATIVE_SSH_OUTPUT"] = str(cls.native_fake_executable)
        command = (
            "$source=[Text.Encoding]::UTF8.GetString("
            "[Convert]::FromBase64String($env:NATIVE_SSH_SOURCE_B64)); "
            "Add-Type -TypeDefinition $source -Language CSharp "
            "-OutputAssembly $env:NATIVE_SSH_OUTPUT -OutputType ConsoleApplication"
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
            raise RuntimeError(f"native ssh fake compilation failed: {result.stderr}")

    @classmethod
    def tearDownClass(cls):
        if cls.native_fake_directory is not None:
            cls.native_fake_directory.cleanup()

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.fake_directory = self.base / "controlled fakes"
        self.fake_directory.mkdir()
        shutil.copy2(repo_path("tests/fakes/ssh"), self.fake_directory / "ssh")
        self._write_fake(
            "uname",
            'printf "%s\\n" "${STACK_FAKE_UNAME:-Other}"\n',
        )
        if os.name == "nt" and self.native_fake_executable is not None:
            shutil.copy2(self.native_fake_executable, self.fake_directory / "ssh.exe")
        else:
            shutil.copy2(repo_path("tests/fakes/ssh"), self.fake_directory / "ssh.exe")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write_fake(self, name: str, body: str) -> Path:
        path = self.fake_directory / name
        path.write_text("#!/bin/bash\nset -euo pipefail\n" + body, encoding="utf-8", newline="\n")
        path.chmod(0o755)
        return path

    def remote_env_with(self, **values: str) -> Path:
        content = repo_path("tests/fixtures/remote.env").read_text(encoding="utf-8")
        for key, value in values.items():
            replacement = f"{key}={value}"
            content = re.sub(
                rf"(?m)^{re.escape(key)}=.*$",
                lambda _match, replacement=replacement: replacement,
                content,
            )
        destination = self.base / "remote.env"
        destination.write_text(content, encoding="utf-8", newline="\n")
        return destination

    def environment(self, log: Path, remote_env: Path, **values: str) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("STACK_FAKE_") and key != "STACK_REMOTE_ENV"
        }
        environment.update(
            {
                "PATH": str(self.fake_directory) + os.pathsep + environment.get("PATH", ""),
                "PATHEXT": ".EXE;.CMD;.BAT;.PS1",
                "STACK_FAKE_LOG": str(log),
                "STACK_FAKE_UNAME": "Other",
                "STACK_REMOTE_ENV": str(remote_env),
            }
        )
        environment.update(values)
        return environment

    def run_bash(
        self,
        *profiles: str,
        log: Path,
        remote_env: Path,
        environment_values: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if BASH is None:
            self.skipTest("requires a usable Bash")
        environment = self.environment(log, remote_env, **(environment_values or {}))
        command = (
            'fake_path=$1; shift; PATH="$fake_path:$PATH"; resolved_ssh=$(command -v ssh); '
            + '[[ "$resolved_ssh" == "$fake_path/ssh" ]] || { '
            + 'printf "test fake ssh was not isolated: %s\\n" "$resolved_ssh" >&2; exit 98; }; '
            + 'exec bash "$@"'
        )
        return subprocess.run(
            [
                BASH,
                "-c",
                command,
                "tunnel-test",
                bash_path(self.fake_directory),
                str(repo_path("scripts/tunnel.sh")),
                *profiles,
            ],
            cwd=repo_path("."),
            env=environment,
            capture_output=True,
            text=True,
        )

    def run_powershell(
        self,
        shell: str,
        *profiles: str,
        log: Path,
        remote_env: Path,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(repo_path("scripts/tunnel.ps1")),
                *profiles,
            ],
            cwd=repo_path("."),
            env=self.environment(log, remote_env),
            capture_output=True,
            text=True,
        )

    def assert_exact_tunnel_argv(self, operation: list[str]):
        self.assertEqual(
            [
                "ssh",
                "-p",
                "2222",
                "-i",
                "C:/fixtures/test-remote-infra-stack",
                "-NT",
                "-o",
                "ExitOnForwardFailure=yes",
                "-o",
                "GatewayPorts=no",
                "-o",
                "ServerAliveInterval=30",
                "-o",
                "ServerAliveCountMax=3",
                "-L",
                "127.0.0.1:5432:127.0.0.1:15432",
                "-L",
                "127.0.0.1:6379:127.0.0.1:16379",
                "-L",
                "127.0.0.1:18000:127.0.0.1:18000",
                "-L",
                "127.0.0.1:18001:127.0.0.1:18001",
                "-L",
                "127.0.0.1:9200:127.0.0.1:9200",
                "-L",
                "127.0.0.1:5601:127.0.0.1:5601",
                "-L",
                "127.0.0.1:3000:127.0.0.1:3000",
                "-L",
                "127.0.0.1:9090:127.0.0.1:9090",
                "-L",
                "127.0.0.1:9091:127.0.0.1:9091",
                "-L",
                "127.0.0.1:5050:127.0.0.1:5050",
                "-L",
                "127.0.0.1:5540:127.0.0.1:5540",
                "-L",
                "127.0.0.1:18002:127.0.0.1:18002",
                "-L",
                "127.0.0.1:18003:127.0.0.1:18003",
                "-L",
                "127.0.0.1:11440:127.0.0.1:11440",
                "-L",
                "127.0.0.1:11441:127.0.0.1:11441",
                "tester@test-remote-infra-stack",
            ],
            operation,
        )
        self.assertNotIn("--", operation)
        self.assertNotIn("127.0.0.1:8000:127.0.0.1:8000", operation)

    def test_bash_builds_exact_tunnel_argv_in_stable_profile_order(self):
        log = self.base / "bash.log"
        result = self.run_bash(
            "tools",
            "observability",
            "search",
            "vector",
            "dynamodb",
            "inference",
            "core",
            log=log,
            remote_env=self.remote_env_with(),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        operations = read_operations(log)
        self.assertEqual(1, len(operations))
        self.assert_exact_tunnel_argv(operations[0])

    def test_powershell_native_argv_matches_the_exact_bash_contract(self):
        if not POWERSHELLS:
            self.skipTest("PowerShell is not installed")
        for shell in POWERSHELLS:
            with self.subTest(shell=Path(shell).name):
                log = self.base / f"{Path(shell).name}.log"
                result = self.run_powershell(
                    shell,
                    "tools",
                    "observability",
                    "search",
                    "vector",
                    "dynamodb",
                    "inference",
                    "core",
                    log=log,
                    remote_env=self.remote_env_with(),
                )
                self.assertEqual(0, result.returncode, result.stderr)
                operations = read_operations(log)
                self.assertEqual(1, len(operations))
                self.assert_exact_tunnel_argv(operations[0])

    def test_each_profile_uses_its_exact_documented_forwarding_set(self):
        selections = {
            "core": ("core",),
            "vector": ("vector",),
            "dynamodb": ("dynamodb",),
            "inference": ("inference",),
            "search": ("search",),
            "observability": ("observability",),
            "tools": ("tools", "core"),
        }
        for profile, profiles in selections.items():
            with self.subTest(client="bash", profile=profile):
                log = self.base / f"bash-{profile}.log"
                result = self.run_bash(
                    *profiles,
                    log=log,
                    remote_env=self.remote_env_with(),
                )
                self.assertEqual(0, result.returncode, result.stderr)
                forwards = extract_forwards(read_operations(log)[0][1:])
                expected = PROFILE_FORWARDS[profile]
                if profile == "tools":
                    expected = PROFILE_FORWARDS["core"] + expected
                self.assertEqual(expected, forwards)

    def test_local_overrides_are_literal_and_preserve_native_argument_boundaries(self):
        overrides = {
            "REMOTE_IDENTITY_FILE": "C:/identity folder/key;$(literal)",
            "LOCAL_POSTGRES_PORT": "25001",
            "LOCAL_REDIS_PORT": "25002",
            "LOCAL_CHROMA_PORT": "25003",
            "LOCAL_CHROMA_ADMIN_PORT": "25004",
            "LOCAL_DYNAMODB_PORT": "25005",
            "LOCAL_DYNAMODB_ADMIN_PORT": "25006",
            "LOCAL_OLLAMA_LLM_PORT": "25007",
            "LOCAL_OLLAMA_EMBEDDING_PORT": "25008",
            "LOCAL_OPENSEARCH_PORT": "25009",
            "LOCAL_OPENSEARCH_DASHBOARDS_PORT": "25010",
            "LOCAL_LANGFUSE_PORT": "25011",
            "LOCAL_MINIO_API_PORT": "25012",
            "LOCAL_MINIO_CONSOLE_PORT": "25013",
            "LOCAL_PGADMIN_PORT": "25014",
            "LOCAL_REDISINSIGHT_PORT": "25015",
        }
        expected = [
            "127.0.0.1:25001:127.0.0.1:15432",
            "127.0.0.1:25002:127.0.0.1:16379",
            "127.0.0.1:25003:127.0.0.1:18000",
            "127.0.0.1:25004:127.0.0.1:18001",
            "127.0.0.1:25009:127.0.0.1:9200",
            "127.0.0.1:25010:127.0.0.1:5601",
            "127.0.0.1:25011:127.0.0.1:3000",
            "127.0.0.1:25012:127.0.0.1:9090",
            "127.0.0.1:25013:127.0.0.1:9091",
            "127.0.0.1:25014:127.0.0.1:5050",
            "127.0.0.1:25015:127.0.0.1:5540",
            "127.0.0.1:25005:127.0.0.1:18002",
            "127.0.0.1:25006:127.0.0.1:18003",
            "127.0.0.1:25007:127.0.0.1:11440",
            "127.0.0.1:25008:127.0.0.1:11441",
        ]
        remote_env = self.remote_env_with(**overrides)
        profiles = ("observability", "tools", "search", "vector", "dynamodb", "inference", "core")

        if BASH is not None:
            bash_log = self.base / "bash-overrides.log"
            bash_result = self.run_bash(*profiles, log=bash_log, remote_env=remote_env)
            self.assertEqual(0, bash_result.returncode, bash_result.stderr)
            bash_operation = read_operations(bash_log)[0]
            self.assertEqual("C:/identity folder/key;$(literal)", bash_operation[4])
            self.assertEqual(expected, extract_forwards(bash_operation[1:]))

        if not POWERSHELLS:
            return
        for shell in POWERSHELLS:
            with self.subTest(shell=Path(shell).name):
                log = self.base / f"{Path(shell).name}-overrides.log"
                result = self.run_powershell(
                    shell, *profiles, log=log, remote_env=remote_env
                )
                self.assertEqual(0, result.returncode, result.stderr)
                operation = read_operations(log)[0]
                self.assertEqual("C:/identity folder/key;$(literal)", operation[4])
                self.assertEqual(expected, extract_forwards(operation[1:]))

    def test_duplicate_selected_local_ports_fail_before_ssh(self):
        remote_env = self.remote_env_with(LOCAL_CHROMA_PORT="5432")
        if BASH is not None:
            bash_log = self.base / "bash-duplicate.log"
            bash_result = self.run_bash(
                "vector", "core", log=bash_log, remote_env=remote_env
            )
            self.assertNotEqual(0, bash_result.returncode)
            self.assertIn("duplicate local port", bash_result.stderr)
            self.assertEqual([], read_operations(bash_log))

        for shell in POWERSHELLS:
            with self.subTest(shell=Path(shell).name):
                log = self.base / f"{Path(shell).name}-duplicate.log"
                result = self.run_powershell(
                    shell, "vector", "core", log=log, remote_env=remote_env
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("duplicate local port", result.stderr)
                self.assertEqual([], read_operations(log))

    def test_profile_dependencies_are_rejected_by_both_common_validators(self):
        remote_env = self.remote_env_with()
        if BASH is not None:
            bash_log = self.base / "bash-tools.log"
            bash_result = self.run_bash("tools", log=bash_log, remote_env=remote_env)
            self.assertNotEqual(0, bash_result.returncode)
            self.assertIn("tools requires core", bash_result.stderr)
            self.assertEqual([], read_operations(bash_log))

        for shell in POWERSHELLS:
            with self.subTest(shell=Path(shell).name):
                log = self.base / f"{Path(shell).name}-tools.log"
                result = self.run_powershell(shell, "tools", log=log, remote_env=remote_env)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("tools requires core", result.stderr)
                self.assertEqual([], read_operations(log))

    def test_new_profiles_are_accepted_but_case_mutations_and_duplicates_are_rejected(self):
        """Catches permissive profile matching or missed duplicate detection."""
        remote_env = self.remote_env_with()
        for profile in ("dynamodb", "inference"):
            if BASH is not None:
                with self.subTest(client="bash", profile=profile):
                    log = self.base / f"bash-{profile}.log"
                    result = self.run_bash(profile, log=log, remote_env=remote_env)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(PROFILE_FORWARDS[profile], extract_forwards(read_operations(log)[0][1:]))
            for shell in POWERSHELLS:
                with self.subTest(client=Path(shell).name, profile=profile):
                    log = self.base / f"{Path(shell).name}-{profile}.log"
                    result = self.run_powershell(shell, profile, log=log, remote_env=remote_env)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(PROFILE_FORWARDS[profile], extract_forwards(read_operations(log)[0][1:]))

        invalid_selections = (
            (("dynamodb", "dynamodb"), "duplicate profile"),
            (("Dynamodb",), "unknown profile: Dynamodb"),
        )
        for profiles, message in invalid_selections:
            if BASH is not None:
                with self.subTest(client="bash", profiles=profiles):
                    log = self.base / f"bash-invalid-{'-'.join(profiles)}.log"
                    result = self.run_bash(*profiles, log=log, remote_env=remote_env)
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(message, result.stderr)
                    self.assertEqual([], read_operations(log))
            for shell in POWERSHELLS:
                with self.subTest(client=Path(shell).name, profiles=profiles):
                    log = self.base / f"{Path(shell).name}-invalid-{'-'.join(profiles)}.log"
                    result = self.run_powershell(shell, *profiles, log=log, remote_env=remote_env)
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(message, result.stderr)
                    self.assertEqual([], read_operations(log))

    def test_local_ports_require_ascii_digits_and_range_before_ssh(self):
        cases = ("+5432", " 5432", "5432 ", "0", "65536", "999999999999999999999")
        for value in cases:
            if BASH is not None:
                with self.subTest(client="bash", value=value):
                    remote_env = self.remote_env_with(LOCAL_CHROMA_PORT=value)
                    log = self.base / f"bash-invalid-{len(value)}-{ord(value[0])}.log"
                    result = self.run_bash("vector", log=log, remote_env=remote_env)
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("LOCAL_CHROMA_PORT", result.stderr)
                    self.assertEqual([], read_operations(log))

            for shell in POWERSHELLS:
                with self.subTest(client=Path(shell).name, value=value):
                    remote_env = self.remote_env_with(LOCAL_CHROMA_PORT=value)
                    log = self.base / f"{Path(shell).name}-invalid-{len(value)}-{ord(value[0])}.log"
                    result = self.run_powershell(
                        shell, "vector", log=log, remote_env=remote_env
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("LOCAL_CHROMA_PORT", result.stderr)
                    self.assertEqual([], read_operations(log))

    def test_unselected_new_local_port_is_still_normalized_before_ssh(self):
        """Catches skipping validation for a configured but unselected profile."""
        remote_env = self.remote_env_with(LOCAL_DYNAMODB_PORT="not-a-port")
        if BASH is not None:
            bash_log = self.base / "bash-unselected-invalid.log"
            result = self.run_bash("core", log=bash_log, remote_env=remote_env)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("LOCAL_DYNAMODB_PORT", result.stderr)
            self.assertEqual([], read_operations(bash_log))
        for shell in POWERSHELLS:
            with self.subTest(shell=Path(shell).name):
                log = self.base / f"{Path(shell).name}-unselected-invalid.log"
                result = self.run_powershell(shell, "core", log=log, remote_env=remote_env)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("LOCAL_DYNAMODB_PORT", result.stderr)
                self.assertEqual([], read_operations(log))

    def test_local_port_text_is_never_evaluated_as_code(self):
        marker = self.base / "evaluated"
        value = f"$(touch {marker})"
        remote_env = self.remote_env_with(LOCAL_POSTGRES_PORT=value)
        if BASH is not None:
            bash_log = self.base / "bash-injection.log"
            bash_result = self.run_bash("core", log=bash_log, remote_env=remote_env)
            self.assertNotEqual(0, bash_result.returncode)
            self.assertFalse(marker.exists())
            self.assertEqual([], read_operations(bash_log))

        for shell in POWERSHELLS:
            with self.subTest(shell=Path(shell).name):
                log = self.base / f"{Path(shell).name}-injection.log"
                result = self.run_powershell(shell, "core", log=log, remote_env=remote_env)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(marker.exists())
                self.assertEqual([], read_operations(log))

    def test_bash_uses_linux_ss_and_macos_lsof_before_ssh(self):
        remote_env = self.remote_env_with()
        self._write_fake("ss", 'printf "LISTEN 0 128 127.0.0.1:18000 0.0.0.0:*\\n"\n')
        linux_log = self.base / "linux-occupied.log"
        linux_result = self.run_bash(
            "vector",
            log=linux_log,
            remote_env=remote_env,
            environment_values={"STACK_FAKE_UNAME": "Linux"},
        )
        self.assertNotEqual(0, linux_result.returncode)
        self.assertIn("local port is already in use: 18000", linux_result.stderr)
        self.assertEqual([], read_operations(linux_log))

        (self.fake_directory / "ss").unlink()
        self._write_fake("lsof", "exit 0\n")
        macos_log = self.base / "macos-occupied.log"
        macos_result = self.run_bash(
            "vector",
            log=macos_log,
            remote_env=remote_env,
            environment_values={"STACK_FAKE_UNAME": "Darwin"},
        )
        self.assertNotEqual(0, macos_result.returncode)
        self.assertIn("local port is already in use: 18000", macos_result.stderr)
        self.assertEqual([], read_operations(macos_log))

    def test_bash_warns_and_relies_on_openssh_when_no_probe_is_available(self):
        log = self.base / "fallback.log"
        result = self.run_bash(
            "vector",
            log=log,
            remote_env=self.remote_env_with(),
            environment_values={
                "STACK_FAKE_UNAME": "Other",
            },
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("WARNING:", result.stderr)
        self.assertIn("ExitOnForwardFailure", result.stderr)
        self.assertEqual(1, len(read_operations(log)))

    def test_powershell_loopback_probe_rejects_occupied_port_and_stops_cleanly(self):
        if not POWERSHELLS:
            self.skipTest("PowerShell is not installed")
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as occupied:
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            occupied_port = occupied.getsockname()[1]
            remote_env = self.remote_env_with(LOCAL_CHROMA_PORT=str(occupied_port))
            for shell in POWERSHELLS:
                with self.subTest(shell=Path(shell).name, state="occupied"):
                    log = self.base / f"{Path(shell).name}-occupied.log"
                    result = self.run_powershell(
                        shell, "vector", log=log, remote_env=remote_env
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("local port is already in use", result.stderr)
                    self.assertEqual([], read_operations(log))

        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as reserve:
            reserve.bind(("127.0.0.1", 0))
            available_port = reserve.getsockname()[1]
        remote_env = self.remote_env_with(LOCAL_CHROMA_PORT=str(available_port))
        for shell in POWERSHELLS:
            with self.subTest(shell=Path(shell).name, state="released"):
                log = self.base / f"{Path(shell).name}-released.log"
                result = self.run_powershell(
                    shell, "vector", log=log, remote_env=remote_env
                )
                self.assertEqual(0, result.returncode, result.stderr)
                with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as rebound:
                    rebound.bind(("127.0.0.1", available_port))

    def test_powershell_socket_probe_failures_have_specific_safe_diagnostics(self):
        if not POWERSHELLS:
            self.skipTest("PowerShell is not installed")
        module_path = str(repo_path("scripts/lib/Common.psm1")).replace("'", "''")
        command = (
            f"Import-Module '{module_path}' -Force -DisableNameChecking; "
            "$messages=@("
            "Get-TunnelSocketProbeFailureMessage -Port 18000 "
            "-SocketError ([System.Net.Sockets.SocketError]::AddressAlreadyInUse); "
            "Get-TunnelSocketProbeFailureMessage -Port 18000 "
            "-SocketError ([System.Net.Sockets.SocketError]::AccessDenied); "
            "Get-TunnelSocketProbeFailureMessage -Port 18000 "
            "-SocketError ([System.Net.Sockets.SocketError]::NetworkDown)"
            "); $messages | ForEach-Object { [Console]::Out.WriteLine($_) }"
        )
        expected = [
            "local port is already in use: 18000",
            (
                "local port probe access was denied for 18000; "
                "check permissions and Windows excluded port ranges"
            ),
            "local port probe failed for 18000 (SocketError: NetworkDown)",
        ]
        for shell in POWERSHELLS:
            with self.subTest(shell=Path(shell).name):
                result = subprocess.run(
                    [
                        shell,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        command,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(expected, result.stdout.splitlines())

    def test_remote_ssh_port_is_normalized_as_bounded_decimal_before_tunneling(self):
        valid = (("00022", "22"), ("00008", "8"))
        for configured, expected in valid:
            remote_env = self.remote_env_with(REMOTE_PORT=configured)
            if BASH is not None:
                with self.subTest(client="bash", configured=configured):
                    log = self.base / f"bash-remote-{configured}.log"
                    result = self.run_bash("vector", log=log, remote_env=remote_env)
                    self.assertEqual(0, result.returncode, result.stderr)
                    operation = read_operations(log)[0]
                    self.assertEqual(expected, operation[operation.index("-p") + 1])
            for shell in POWERSHELLS:
                with self.subTest(client=Path(shell).name, configured=configured):
                    log = self.base / f"{Path(shell).name}-remote-{configured}.log"
                    result = self.run_powershell(
                        shell, "vector", log=log, remote_env=remote_env
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    operation = read_operations(log)[0]
                    self.assertEqual(expected, operation[operation.index("-p") + 1])

        invalid = ("00000", "65536", "0100000", "999999999999999999999")
        for configured in invalid:
            remote_env = self.remote_env_with(REMOTE_PORT=configured)
            if BASH is not None:
                with self.subTest(client="bash", invalid=configured):
                    log = self.base / f"bash-invalid-remote-{configured}.log"
                    result = self.run_bash("vector", log=log, remote_env=remote_env)
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("REMOTE_PORT", result.stderr)
                    self.assertEqual([], read_operations(log))
            for shell in POWERSHELLS:
                with self.subTest(client=Path(shell).name, invalid=configured):
                    log = self.base / f"{Path(shell).name}-invalid-remote-{configured}.log"
                    result = self.run_powershell(
                        shell, "vector", log=log, remote_env=remote_env
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("REMOTE_PORT", result.stderr)
                    self.assertEqual([], read_operations(log))


if __name__ == "__main__":
    unittest.main()
