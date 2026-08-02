import contextlib
import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from tests.helpers import repo_path


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


BASH = usable_bash()


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


def bash_path(path: Path) -> str:
    resolved = path.resolve().as_posix()
    if os.name == "nt" and re.match(r"^[A-Za-z]:/", resolved):
        return f"/{resolved[0].lower()}{resolved[2:]}"
    return resolved


class BashOperatorTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        if BASH is None:
            self.skipTest("requires a usable Bash")

    @contextlib.contextmanager
    def operator_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
            yield root

    def environment(self, log: Path, capture: Path | None = None, remote_env: Path | None = None):
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": str(repo_path("tests/fakes")) + os.pathsep + environment.get("PATH", ""),
                "STACK_FAKE_LOG": str(log),
                "STACK_REMOTE_ENV": str(remote_env or repo_path("tests/fixtures/remote.env")),
            }
        )
        if capture is not None:
            environment["STACK_FAKE_CAPTURE_DIR"] = str(capture)
        return environment

    def run_script(
        self,
        root: Path,
        script: str,
        *arguments: str,
        log: Path,
        capture: Path | None = None,
        remote_env: Path | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                BASH,
                "-c",
                'PATH="$1:$PATH"; shift; exec bash "$@"',
                "operator-test",
                bash_path(repo_path("tests/fakes")),
                str(root / "scripts" / script),
                *arguments,
            ],
            cwd=root,
            env=self.environment(log, capture, remote_env),
            input=input_text,
            capture_output=True,
            text=True,
        )

    def test_bootstrap_uploads_before_install_and_prepares_remote_layout(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(root, "bootstrap.sh", log=log)
            self.assertEqual(0, result.returncode, result.stderr)

            operations = read_operations(log)
            self.assertEqual(["scp", "ssh", "ssh"], [operation[0] for operation in operations])
            scp = operations[0][1:]
            self.assertEqual(["-P", "2222", "-i", "C:/fixtures/test-remote-infra-stack"], scp[:4])
            self.assertTrue(
                scp[4].replace("\\", "/").endswith("/scripts/remote/bootstrap-host.sh"),
                scp[4],
            )
            remote_bootstrap = scp[5].split(":", 1)[1]
            self.assertRegex(remote_bootstrap, r"^remote-infra-stack-bootstrap-[0-9TZ-]+-[0-9]+\.sh$")

            install = operations[1][1:]
            self.assertEqual(
                [
                    "-p", "2222", "-i", "C:/fixtures/test-remote-infra-stack",
                    "tester@test-remote-infra-stack", "sudo", "bash", remote_bootstrap, "--install",
                ],
                install,
            )
            prepare = operations[2][1:]
            self.assertEqual(
                [
                    "-p", "2222", "-i", "C:/fixtures/test-remote-infra-stack",
                    "tester@test-remote-infra-stack", "mkdir", "-p",
                    "remote-infra-stack/incoming", "remote-infra-stack/releases",
                    "remote-infra-stack/runtime",
                ],
                prepare,
            )

    def test_deploy_archives_only_clean_head_and_invokes_receiver(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            capture = root / "capture"
            capture.mkdir()
            result = self.run_script(
                root, "deploy.sh", "core", "vector", log=log, capture=capture
            )
            self.assertEqual(0, result.returncode, result.stderr)

            operations = read_operations(log)
            self.assertEqual(["scp", "scp", "ssh"], [operation[0] for operation in operations])
            uploaded = []
            for operation in operations[:2]:
                uploaded.extend(Path(item).name for item in scp_positionals(operation[1:])[:-1])
            archive_name = next(name for name in uploaded if name.endswith(".tar.gz"))
            self.assertRegex(archive_name, r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,}\.tar\.gz$")
            self.assertEqual(
                {archive_name, f"{archive_name}.sha256", ".env", "deploy-release.sh"},
                set(uploaded),
            )

            archive = capture / archive_name
            checksum = capture / f"{archive_name}.sha256"
            with tarfile.open(archive, "r:gz") as release:
                members = {member.name.removeprefix("./") for member in release.getmembers()}
            self.assertIn("committed.txt", members)
            self.assertNotIn(".env", members)
            self.assertNotIn("ignored-note.txt", members)
            expected_checksum = f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive_name}\n"
            self.assertEqual(expected_checksum, checksum.read_text(encoding="utf-8"))

            receiver = operations[2][1:]
            self.assertEqual(
                ["-p", "2222", "-i", "C:/fixtures/test-remote-infra-stack"],
                receiver[:4],
            )
            self.assertEqual("tester@test-remote-infra-stack", receiver[4])
            self.assertEqual("bash", receiver[5])
            self.assertEqual("remote-infra-stack/incoming/deploy-release.sh", receiver[6])
            self.assertEqual(
                [
                    "--root", "remote-infra-stack",
                    "--archive", f"remote-infra-stack/incoming/{archive_name}",
                    "--checksum", f"remote-infra-stack/incoming/{archive_name}.sha256",
                    "--profiles", "core,vector",
                ],
                receiver[7:],
            )
            self.assertFalse((root / ".artifacts").exists())

    def assert_deploy_rejected_before_remote_call(self, mutate):
        with self.operator_repository() as root:
            mutate(root)
            log = root / "fake.log"
            result = self.run_script(root, "deploy.sh", "core", log=log)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("clean committed Git HEAD", result.stderr)
            self.assertEqual([], read_operations(log))

    def test_deploy_rejects_tracked_changes_before_remote_calls(self):
        self.assert_deploy_rejected_before_remote_call(
            lambda root: (root / "committed.txt").write_text("dirty\n", encoding="utf-8")
        )

    def test_deploy_rejects_staged_changes_before_remote_calls(self):
        def stage_change(root: Path):
            (root / "committed.txt").write_text("staged\n", encoding="utf-8")
            run_git(root, "add", "committed.txt")

        self.assert_deploy_rejected_before_remote_call(stage_change)

    def test_deploy_rejects_untracked_files_before_remote_calls(self):
        self.assert_deploy_rejected_before_remote_call(
            lambda root: (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        )

    def test_stack_status_forwards_to_current_remote_release(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(root, "stack.sh", "status", log=log)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                [[
                    "ssh", "-p", "2222", "-i", "C:/fixtures/test-remote-infra-stack",
                    "tester@test-remote-infra-stack", "bash",
                    "remote-infra-stack/current/scripts/remote/stack.sh", "status",
                ]],
                read_operations(log),
            )

    def test_stack_destroy_requires_both_local_confirmations(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(
                root,
                "stack.sh",
                "destroy",
                log=log,
                input_text="test-remote-infra-stack\nDESTROY-remote-infra-stack\n",
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                [[
                    "ssh", "-p", "2222", "-i", "C:/fixtures/test-remote-infra-stack",
                    "tester@test-remote-infra-stack", "bash",
                    "remote-infra-stack/current/scripts/remote/stack.sh",
                    "destroy", "remote-infra-stack", "DESTROY-remote-infra-stack",
                ]],
                read_operations(log),
            )

    def test_stack_destroy_rejects_mismatched_confirmations_before_ssh(self):
        for input_text, message in (
            ("wrong-host\nDESTROY-remote-infra-stack\n", "remote target confirmation did not match"),
            ("test-remote-infra-stack\nwrong-token\n", "destroy token did not match"),
        ):
            with self.subTest(message=message), self.operator_repository() as root:
                log = root / "fake.log"
                result = self.run_script(
                    root, "stack.sh", "destroy", log=log, input_text=input_text
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
                self.assertEqual([], read_operations(log))

    def test_stack_rejects_invalid_profiles_before_ssh(self):
        for arguments, message in (
            (("up", "tools"), "tools requires core"),
            (("up", "core", "core"), "duplicate profile"),
            (("stop", "unknown"), "unknown profile"),
        ):
            with self.subTest(arguments=arguments), self.operator_repository() as root:
                log = root / "fake.log"
                result = self.run_script(root, "stack.sh", *arguments, log=log)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
                self.assertEqual([], read_operations(log))

    def test_remote_env_values_are_never_evaluated(self):
        with self.operator_repository() as root:
            marker = root / "evaluated"
            remote_env = root / "safe-remote.env"
            remote_env.write_text(
                repo_path("tests/fixtures/remote.env")
                .read_text(encoding="utf-8")
                .replace("REMOTE_HOST=test-remote-infra-stack", f"REMOTE_HOST=$(touch {marker})"),
                encoding="utf-8",
            )
            log = root / "fake.log"
            result = self.run_script(
                root, "stack.sh", "status", log=log, remote_env=remote_env
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("REMOTE_HOST contains unsupported characters", result.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual([], read_operations(log))

    def test_remote_env_rejects_malformed_duplicate_and_unsafe_root_entries(self):
        base = repo_path("tests/fixtures/remote.env").read_text(encoding="utf-8")
        variants = (
            (base + "not an assignment\n", "invalid remote.env line"),
            (base + "REMOTE_HOST=duplicate\n", "duplicate remote.env key"),
            (base.replace("LOCAL_MINIO_CONSOLE_PORT=9091\n", ""), "missing remote.env key"),
            (base.replace("REMOTE_ROOT=remote-infra-stack", "REMOTE_ROOT=/srv/stack"), "relative REMOTE_ROOT"),
            (base.replace("REMOTE_ROOT=remote-infra-stack", "REMOTE_ROOT=stack/../other"), "must not contain .."),
            (base.replace("REMOTE_ROOT=remote-infra-stack", "REMOTE_ROOT=stack;echo"), "unsupported REMOTE_ROOT characters"),
        )
        for content, message in variants:
            with self.subTest(message=message), self.operator_repository() as root:
                remote_env = root / "invalid-remote.env"
                remote_env.write_text(content, encoding="utf-8")
                log = root / "fake.log"
                result = self.run_script(
                    root, "stack.sh", "status", log=log, remote_env=remote_env
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
                self.assertEqual([], read_operations(log))

    def test_check_validates_clean_configuration_without_ssh_or_scp(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(root, "check.sh", "core", "vector", log=log)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("Local checks passed for profiles: core vector", result.stdout)
            self.assertEqual([], read_operations(log))

    def test_check_rejects_placeholder_secrets_without_ssh_or_scp(self):
        with self.operator_repository() as root:
            shutil.copy2(root / ".env.example", root / ".env")
            log = root / "fake.log"
            result = self.run_script(root, "check.sh", "core", log=log)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("placeholder", result.stderr)
            self.assertEqual([], read_operations(log))


if __name__ == "__main__":
    unittest.main()
