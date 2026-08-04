import contextlib
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
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

    def environment(
        self,
        log: Path,
        capture: Path | None = None,
        remote_env: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ):
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("STACK_FAKE_")
            and not key.startswith("STACK_DOCKER_")
            and not key.startswith("STACK_GIT_")
        }
        environment.update(
            {
                "PATH": str(repo_path("tests/fakes")) + os.pathsep + environment.get("PATH", ""),
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
        root: Path,
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
                BASH,
                "-c",
                'PATH="$1:$PATH"; shift; exec bash "$@"',
                "operator-test",
                bash_path(repo_path("tests/fakes")),
                str(root / "scripts" / script),
                *arguments,
            ],
            cwd=root,
            env=self.environment(log, capture, remote_env, extra_env),
            input=input_text,
            capture_output=True,
            text=True,
        )

    def remote_env_with(self, root: Path, **values: str) -> Path:
        content = repo_path("tests/fixtures/remote.env").read_text(encoding="utf-8")
        for key, value in values.items():
            content = re.sub(rf"(?m)^{re.escape(key)}=.*$", f"{key}={value}", content)
        destination = root / "remote.env"
        destination.write_text(content, encoding="utf-8")
        return destination

    def test_bootstrap_uploads_before_install_and_prepares_remote_layout(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(root, "bootstrap.sh", log=log)
            self.assertEqual(0, result.returncode, result.stderr)

            operations = read_operations(log)
            self.assertEqual(["ssh", "scp", "ssh", "ssh"], [operation[0] for operation in operations])
            scp = operations[1][1:]
            self.assertEqual(["-P", "2222", "-i", "C:/fixtures/test-remote-infra-stack"], scp[:4])
            self.assertTrue(
                scp[4].replace("\\", "/").endswith("/scripts/remote/bootstrap-host.sh"),
                scp[4],
            )
            remote_bootstrap = scp[5].split(":", 1)[1]
            self.assertRegex(remote_bootstrap, r"^remote-infra-stack/incoming/bootstrap-[A-Za-z0-9]+\.sh$")
            self.assertEqual(
                ["sudo", "bash", remote_bootstrap, "--install"],
                shlex.split(operations[2][-1]),
            )
            self.assertEqual(["rm", "-f", "--", remote_bootstrap], shlex.split(operations[3][-1]))

    def test_bootstrap_gpu_forwards_pinned_cuda_image_to_overridden_target(self):
        with self.operator_repository() as root:
            remote_env = self.remote_env_with(
                root,
                REMOTE_HOST="gpu-target",
                REMOTE_USER="gpu-operator",
                REMOTE_ROOT="gpu-stack",
            )
            log = root / "fake.log"
            result = self.run_script(
                root, "bootstrap.sh", "--gpu", log=log, remote_env=remote_env
            )
            self.assertEqual(0, result.returncode, result.stderr)

            operations = read_operations(log)
            remote_bootstrap = scp_positionals(operations[1][1:])[-1].split(":", 1)[1]
            self.assertEqual("gpu-operator@gpu-target", operations[2][-2])
            self.assertEqual(
                [
                    "sudo", "bash", remote_bootstrap, "--install", "--gpu", "--cuda-image",
                    "docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df",
                ],
                shlex.split(operations[2][-1]),
            )

    def test_bootstrap_gpu_rejects_invalid_cuda_image_catalog_before_remote_calls(self):
        variants = {
            "absent": lambda content: re.sub(r"(?m)^NVIDIA_CUDA_IMAGE=.*\n?", "", content),
            "duplicate": lambda content: content + "\nNVIDIA_CUDA_IMAGE=docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df\n",
            "malformed": lambda content: re.sub(r"(?m)^NVIDIA_CUDA_IMAGE=.*$", "NVIDIA_CUDA_IMAGE : invalid", content),
            "unpinned": lambda content: re.sub(r"(?m)^NVIDIA_CUDA_IMAGE=.*$", "NVIDIA_CUDA_IMAGE=docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04", content),
        }
        for name, mutate in variants.items():
            with self.subTest(name=name), self.operator_repository() as root:
                versions = root / "versions.env"
                versions.write_text(mutate(versions.read_text(encoding="utf-8")), encoding="utf-8")
                log = root / "fake.log"
                result = self.run_script(root, "bootstrap.sh", "--gpu", log=log)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("NVIDIA_CUDA_IMAGE", result.stderr)
                self.assertEqual([], read_operations(log))

    def test_bootstrap_rejects_unknown_options_before_remote_calls(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(root, "bootstrap.sh", "--unexpected", log=log)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("usage: bootstrap.sh [--gpu]", result.stderr)
            self.assertEqual([], read_operations(log))

    def test_bootstrap_uses_serialized_commands_and_unique_incoming_name(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(root, "bootstrap.sh", log=log)
            self.assertEqual(0, result.returncode, result.stderr)
            operations = read_operations(log)
            scp_operation = next(operation for operation in operations if operation[0] == "scp")
            destination = scp_positionals(scp_operation[1:])[-1]
            self.assertRegex(
                destination,
                r":remote-infra-stack/incoming/bootstrap-[A-Za-z0-9]+\.sh$",
            )
            for operation in (item for item in operations if item[0] == "ssh"):
                target_index = operation.index("tester@test-remote-infra-stack")
                self.assertEqual("--", operation[target_index - 1])
                self.assertEqual(1, len(operation[target_index + 1 :]))
                shlex.split(operation[-1])

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
            self.assertEqual(["scp", "ssh", "ssh"], [operation[0] for operation in operations])
            uploaded = [
                Path(item).name
                for item in scp_positionals(operations[0][1:])[:-1]
            ]
            archive_name = next(name for name in uploaded if name.endswith(".tar.gz"))
            self.assertRegex(
                archive_name,
                r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[A-Za-z0-9]+\.tar\.gz$",
            )
            self.assertIn(f"{archive_name}.sha256", uploaded)
            env_name = next(name for name in uploaded if name.startswith("runtime-env-"))
            receiver_name = next(name for name in uploaded if name.startswith("deploy-release-"))
            self.assertEqual(4, len(set(uploaded)))

            archive = capture / archive_name
            checksum = capture / f"{archive_name}.sha256"
            with tarfile.open(archive, "r:gz") as release:
                members = {member.name.removeprefix("./") for member in release.getmembers()}
            self.assertIn("committed.txt", members)
            self.assertNotIn(".env", members)
            self.assertNotIn("ignored-note.txt", members)
            expected_checksum = f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive_name}\n"
            self.assertEqual(expected_checksum, checksum.read_text(encoding="utf-8"))

            receiver = operations[1][1:]
            self.assertEqual(
                ["-p", "2222", "-i", "C:/fixtures/test-remote-infra-stack"],
                receiver[:4],
            )
            self.assertEqual("--", receiver[4])
            self.assertEqual("tester@test-remote-infra-stack", receiver[5])
            receiver_argv = shlex.split(receiver[6])
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
                {
                    f"remote-infra-stack/incoming/{name}"
                    for name in uploaded
                },
                set(cleanup_argv[3:]),
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

    def test_deploy_rejects_clean_force_tracked_secret_files(self):
        for secret_name in (".env", "remote.env"):
            with self.subTest(secret_name=secret_name), self.operator_repository() as root:
                remote_env = repo_path("tests/fixtures/remote.env")
                if secret_name == "remote.env":
                    remote_env = root / "remote.env"
                    shutil.copy2(repo_path("tests/fixtures/remote.env"), remote_env)
                run_git(root, "add", "-f", secret_name)
                run_git(root, "commit", "-m", f"force track {secret_name}")
                log = root / "fake.log"
                result = self.run_script(
                    root, "deploy.sh", "core", log=log, remote_env=remote_env
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("must not be tracked", result.stderr)
                self.assertEqual([], read_operations(log))

    def test_deploy_rejects_tracked_repo_remote_env_with_external_override(self):
        with self.operator_repository() as root:
            shutil.copy2(repo_path("tests/fixtures/remote.env"), root / "remote.env")
            run_git(root, "add", "-f", "remote.env")
            run_git(root, "commit", "-m", "force track repository remote env")
            log = root / "fake.log"

            result = self.run_script(
                root,
                "deploy.sh",
                "core",
                log=log,
                remote_env=repo_path("tests/fixtures/remote.env"),
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("repository remote.env must not be tracked", result.stderr)
            self.assertEqual([], read_operations(log))

    def git_mutation_environment(self, root: Path, mutation: str) -> dict[str, str]:
        return {
            "STACK_GIT_MUTATION": mutation,
            "STACK_GIT_MUTATION_MARKER": bash_path(root / ".artifacts" / f"{mutation}.marker"),
            "STACK_GIT_REPO": bash_path(root),
        }

    def test_deploy_rejects_head_move_before_remote_calls(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(
                root,
                "deploy.sh",
                "core",
                log=log,
                extra_env=self.git_mutation_environment(root, "move-head"),
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("HEAD changed during deployment preparation", result.stderr)
            self.assertEqual([], read_operations(log))

    def test_deploy_sanitizes_inherited_git_mutation_hooks(self):
        with self.operator_repository() as root:
            inherited = self.git_mutation_environment(root, "move-head")
            marker = root / ".artifacts/move-head.marker"
            log = root / "fake.log"

            with mock.patch.dict(os.environ, inherited):
                result = self.run_script(root, "deploy.sh", "core", log=log)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(marker.exists())

    def test_deploy_rejects_receiver_edit_before_remote_calls(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(
                root,
                "deploy.sh",
                "core",
                log=log,
                extra_env=self.git_mutation_environment(root, "edit-receiver"),
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("clean committed Git HEAD", result.stderr)
            self.assertEqual([], read_operations(log))

    def test_deploy_snapshots_ignored_env_before_archive_mutation(self):
        with self.operator_repository() as root:
            original_env = (root / ".env").read_bytes()
            log = root / "fake.log"
            capture = root / "capture"
            capture.mkdir()
            result = self.run_script(
                root,
                "deploy.sh",
                "core",
                log=log,
                capture=capture,
                extra_env=self.git_mutation_environment(root, "edit-env"),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            env_uploads = [path for path in capture.iterdir() if "runtime-env" in path.name]
            self.assertEqual(1, len(env_uploads))
            self.assertEqual(original_env, env_uploads[0].read_bytes())

    def test_deploy_archives_and_materializes_receiver_from_one_exact_oid(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            (root / ".artifacts").mkdir()
            git_log = root / ".artifacts/git.log"
            expected_oid = run_git(root, "rev-parse", "HEAD").stdout.strip()
            result = self.run_script(
                root,
                "deploy.sh",
                "core",
                log=log,
                extra_env={"STACK_GIT_LOG": bash_path(git_log)},
            )
            self.assertEqual(0, result.returncode, result.stderr)
            operations = read_operations(git_log)
            archive = next(operation for operation in operations if "archive" in operation)
            self.assertEqual(expected_oid, archive[-1])
            receiver_blob = [
                operation
                for operation in operations
                if "scripts/remote/deploy-release.sh" in " ".join(operation)
                and ("cat-file" in operation or "show" in operation)
            ]
            self.assertEqual(1, len(receiver_blob))
            self.assertIn(expected_oid, " ".join(receiver_blob[0]))

    def test_deploy_uses_private_unique_staging_and_incoming_names(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(root, "deploy.sh", "core", log=log)
            self.assertEqual(0, result.returncode, result.stderr)
            scp_operations = [operation for operation in read_operations(log) if operation[0] == "scp"]
            sources = []
            destinations = []
            for operation in scp_operations:
                positionals = scp_positionals(operation[1:])
                sources.extend(positionals[:-1])
                destinations.append(positionals[-1])
            self.assertEqual(4, len(sources))
            staging_parents = {Path(source).parent.as_posix() for source in sources}
            self.assertEqual(1, len(staging_parents))
            self.assertRegex(next(iter(staging_parents)), r"/\.artifacts/deploy\.[A-Za-z0-9]+$")
            names = {Path(source).name for source in sources}
            self.assertEqual(4, len(names))
            self.assertTrue(any(name.startswith("runtime-env-") for name in names))
            self.assertTrue(any(name.startswith("deploy-release-") for name in names))
            self.assertTrue(all(":remote-infra-stack/incoming/" in item for item in destinations))

    def test_deploy_rejects_symlinked_artifact_parent_without_remote_calls(self):
        with self.operator_repository() as root, tempfile.TemporaryDirectory() as outside_directory:
            artifact_parent = root / ".artifacts"
            try:
                os.symlink(outside_directory, artifact_parent, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory symlinks unavailable: {error}")
            log = root / "fake.log"
            result = self.run_script(root, "deploy.sh", "core", log=log)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("real non-symlink", result.stderr)
            self.assertEqual([], list(Path(outside_directory).iterdir()))
            self.assertEqual([], read_operations(log))

    def test_stack_status_forwards_to_current_remote_release(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(root, "stack.sh", "status", log=log)
            self.assertEqual(0, result.returncode, result.stderr)
            operation = read_operations(log)[0]
            self.assertEqual("ssh", operation[0])
            self.assertEqual("--", operation[-3])
            self.assertEqual("tester@test-remote-infra-stack", operation[-2])
            self.assertEqual(
                ["bash", "remote-infra-stack/current/scripts/remote/stack.sh", "status"],
                shlex.split(operation[-1]),
            )

    def test_stack_serializes_one_posix_remote_command(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            result = self.run_script(root, "stack.sh", "status", log=log)
            self.assertEqual(0, result.returncode, result.stderr)
            operation = read_operations(log)[0]
            target_index = operation.index("tester@test-remote-infra-stack")
            self.assertEqual("--", operation[target_index - 1])
            self.assertEqual(1, len(operation[target_index + 1 :]))
            remote = read_operations(log.with_suffix(".remote.log"))[0]
            self.assertEqual("ssh-remote", remote[0])
            self.assertEqual("tester@test-remote-infra-stack", remote[1])
            self.assertEqual(
                ["bash", "remote-infra-stack/current/scripts/remote/stack.sh", "status"],
                shlex.split(remote[2]),
            )

    def test_stack_rejects_dangerous_log_targets_before_ssh(self):
        for target in ("core;id", "$(touch escaped)", "app-postgres'bad"):
            with self.subTest(target=target), self.operator_repository() as root:
                log = root / "fake.log"
                result = self.run_script(root, "stack.sh", "logs", target, log=log)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("unknown log target", result.stderr)
                self.assertEqual([], read_operations(log))

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
            operation = read_operations(log)[0]
            self.assertEqual("--", operation[-3])
            self.assertEqual("tester@test-remote-infra-stack", operation[-2])
            self.assertEqual(
                [
                    "bash", "remote-infra-stack/current/scripts/remote/stack.sh",
                    "destroy", "remote-infra-stack", "DESTROY-remote-infra-stack",
                ],
                shlex.split(operation[-1]),
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
            (("stop", "DynamoDB"), "unknown profile: DynamoDB"),
        ):
            with self.subTest(arguments=arguments), self.operator_repository() as root:
                log = root / "fake.log"
                result = self.run_script(root, "stack.sh", *arguments, log=log)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
                self.assertEqual([], read_operations(log))

    def test_stack_forwards_data_and_inference_log_targets_unchanged(self):
        for target in (
            "dynamodb", "inference", "chroma-admin", "dynamodb-local",
            "dynamodb-admin", "ollama-llm", "ollama-embedding",
        ):
            with self.subTest(target=target), self.operator_repository() as root:
                log = root / "fake.log"
                result = self.run_script(root, "stack.sh", "logs", target, log=log)
                self.assertEqual(0, result.returncode, result.stderr)
                remote = read_operations(log.with_suffix(".remote.log"))[0]
                self.assertEqual(
                    [
                        "bash", "remote-infra-stack/current/scripts/remote/stack.sh",
                        "logs", target,
                    ],
                    shlex.split(remote[2]),
                )

    def test_remote_env_values_are_never_evaluated(self):
        with self.operator_repository() as root:
            marker = root / "evaluated"
            remote_env = root / "safe-remote.env"
            remote_env.write_text(
                repo_path("tests/fixtures/remote.env")
                .read_text(encoding="utf-8")
                .replace("REMOTE_HOST=test-remote-infra-stack", "REMOTE_HOST=$(touch evaluated)"),
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

    def test_empty_remote_port_omits_ssh_port_option(self):
        with self.operator_repository() as root:
            remote_env = root / "empty-port.env"
            remote_env.write_text(
                repo_path("tests/fixtures/remote.env")
                .read_text(encoding="utf-8")
                .replace("REMOTE_PORT=2222", "REMOTE_PORT="),
                encoding="utf-8",
            )
            log = root / "fake.log"
            result = self.run_script(root, "stack.sh", "status", log=log, remote_env=remote_env)
            self.assertEqual(0, result.returncode, result.stderr)
            operation = read_operations(log)[0]
            self.assertNotIn("-p", operation)
            self.assertNotIn("-P", operation)

    def test_remote_port_is_normalized_as_bounded_decimal_before_ssh(self):
        for configured, expected in (("00022", "22"), ("00008", "8")):
            with self.subTest(configured=configured), self.operator_repository() as root:
                remote_env = self.remote_env_with(root, REMOTE_PORT=configured)
                log = root / "fake.log"
                result = self.run_script(
                    root, "stack.sh", "status", log=log, remote_env=remote_env
                )
                self.assertEqual(0, result.returncode, result.stderr)
                operation = read_operations(log)[0]
                self.assertEqual(expected, operation[operation.index("-p") + 1])

        for configured in ("00000", "65536", "0100000", "999999999999999999999"):
            with self.subTest(configured=configured), self.operator_repository() as root:
                remote_env = self.remote_env_with(root, REMOTE_PORT=configured)
                log = root / "fake.log"
                result = self.run_script(
                    root, "stack.sh", "status", log=log, remote_env=remote_env
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("REMOTE_PORT", result.stderr)
                self.assertEqual([], read_operations(log))

    def test_remote_target_rejects_option_like_and_colon_values(self):
        cases = (
            ({"REMOTE_HOST": "-host"}, "REMOTE_HOST must not begin with an option prefix"),
            ({"REMOTE_HOST": "host:22"}, "REMOTE_HOST must not contain a colon"),
            ({"REMOTE_USER": "-root"}, "REMOTE_USER must not begin with an option prefix"),
        )
        for values, message in cases:
            with self.subTest(values=values), self.operator_repository() as root:
                remote_env = self.remote_env_with(root, **values)
                log = root / "fake.log"
                result = self.run_script(
                    root, "stack.sh", "status", log=log, remote_env=remote_env
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
                self.assertEqual([], read_operations(log))

    def test_posix_serializer_preserves_shell_metacharacters_as_literal_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "escaped"
            arguments = ["semi;colon", "$(touch escaped)", "quo'te", str(marker)]
            environment = os.environ.copy()
            for index, argument in enumerate(arguments):
                environment[f"SERIALIZER_ARG_{index}"] = argument
            result = subprocess.run(
                [
                    BASH,
                    "-c",
                    'source "$1"; build_remote_command "$SERIALIZER_ARG_0" "$SERIALIZER_ARG_1" "$SERIALIZER_ARG_2" "$SERIALIZER_ARG_3"; printf "%s" "$remote_command"',
                    "serializer-test",
                    str(repo_path("scripts/lib/common.sh")),
                ],
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(arguments, shlex.split(result.stdout), repr(result.stdout))
            self.assertFalse(marker.exists())

    def test_check_validates_clean_configuration_without_ssh_or_scp(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            remote_env = self.remote_env_with(root, REMOTE_IDENTITY_FILE="")
            result = self.run_script(
                root, "check.sh", "core", "vector", log=log, remote_env=remote_env
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("Local checks passed for profiles: core vector", result.stdout)
            self.assertEqual([], read_operations(log))

    def test_check_treats_an_unusable_local_docker_cli_as_best_effort(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            remote_env = self.remote_env_with(root, REMOTE_IDENTITY_FILE="")

            result = self.run_script(
                root,
                "check.sh",
                "core",
                log=log,
                remote_env=remote_env,
                extra_env={"STACK_FAKE_FAIL_COMMAND": "version"},
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("Local checks passed for profiles: core", result.stdout)
            self.assertIn("local Docker Compose is unavailable", result.stderr)
            self.assertEqual([], read_operations(log))

    def test_check_transports_exact_opensearch_sources_into_compose_render(self):
        with self.operator_repository() as root:
            log = root / "fake.log"
            content_log = root / "compose-content.log"
            remote_env = self.remote_env_with(root, REMOTE_IDENTITY_FILE="")
            environment = {
                "STACK_FAKE_REQUIRE_OPENSEARCH_B64": "1",
                "STACK_FAKE_CONTENT_LOG": str(content_log),
            }
            result = self.run_script(
                root, "check.sh", "search", log=log, remote_env=remote_env,
                extra_env=environment
            )
            self.assertEqual(0, result.returncode, result.stderr)
            content = content_log.read_text(encoding="utf-8")
            for relative in (
                "config/opensearch/opensearch.yml",
                "config/opensearch/docker-entrypoint.sh",
            ):
                digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
                self.assertIn(f"{relative} {digest}", content)

    def test_check_rejects_placeholder_secrets_without_ssh_or_scp(self):
        with self.operator_repository() as root:
            shutil.copy2(root / ".env.example", root / ".env")
            log = root / "fake.log"
            remote_env = self.remote_env_with(root, REMOTE_IDENTITY_FILE="")
            result = self.run_script(
                root, "check.sh", "core", log=log, remote_env=remote_env
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("placeholder", result.stderr)
            self.assertEqual([], read_operations(log))

    def test_check_rejects_unreadable_identity_before_other_operations(self):
        with self.operator_repository() as root:
            missing_identity = root / "missing-identity"
            remote_env = self.remote_env_with(
                root, REMOTE_IDENTITY_FILE=bash_path(missing_identity)
            )
            log = root / "fake.log"
            result = self.run_script(
                root, "check.sh", "core", log=log, remote_env=remote_env
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("identity file must be a readable regular file", result.stderr)
            self.assertEqual([], read_operations(log))

    def test_check_rejects_readable_identity_directory(self):
        with self.operator_repository() as root:
            identity_directory = root / "identity-directory"
            identity_directory.mkdir()
            remote_env = self.remote_env_with(
                root, REMOTE_IDENTITY_FILE=bash_path(identity_directory)
            )
            log = root / "fake.log"

            result = self.run_script(
                root, "check.sh", "core", log=log, remote_env=remote_env
            )

            self.assertNotEqual(0, result.returncode)
            self.assertIn("identity file must be a readable regular file", result.stderr)
            self.assertEqual([], read_operations(log))

    def test_check_sanitizes_inherited_fake_and_docker_hooks(self):
        with self.operator_repository() as root, tempfile.TemporaryDirectory() as outside:
            remote_env = self.remote_env_with(root, REMOTE_IDENTITY_FILE="")
            inherited_log = Path(outside) / "inherited.log"
            inherited = {
                "STACK_FAKE_FAIL_COMMAND": "config",
                "STACK_DOCKER_LOG": str(inherited_log),
            }
            log = root / "fake.log"
            with mock.patch.dict(os.environ, inherited):
                result = self.run_script(
                    root, "check.sh", "core", log=log, remote_env=remote_env
                )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(inherited_log.exists())


if __name__ == "__main__":
    unittest.main()
