import hashlib
import io
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import repo_path
from tests.test_remote_runtime import (
    BASH_BIN,
    shell_path,
    usable_bash,
    write_generation_phase_curl,
    write_test_env,
)


@unittest.skipUnless(usable_bash(), "requires a usable Bash")
class ReleaseLifecycleTests(unittest.TestCase):
    RELEASE_MEMBERS = (
        "compose.yaml", "versions.env", "scripts/remote/compose.sh",
        "scripts/remote/preflight.sh", "scripts/remote/health.sh",
        "scripts/remote/stack.sh", "config/ollama/bootstrap.sh",
        "images/chromadb-admin/Dockerfile",
        "images/chromadb-admin/install-dependencies.sh", ".dockerignore",
        "vendor/chromadb-admin/package.json",
        "vendor/chromadb-admin/package-lock.json",
        "vendor/chromadb-admin/LICENSE.txt",
        "vendor/chromadb-admin/UPSTREAM.md",
        "config/opensearch/opensearch.yml",
        "config/opensearch/docker-entrypoint.sh",
    )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "stack root;safe"
        for child in ("incoming", "runtime", "releases"):
            (self.root / child).mkdir(parents=True, exist_ok=True)
        write_test_env(self.root / "runtime/.env")
        self.env_counter = 0
        self.log = self.root / "docker.log"
        self.sysctl_log = self.root / "sysctl.log"
        self.nvidia_log = self.root / "nvidia.log"
        self.curl_state = self.root / "curl-state"
        self.fake_curl = self.root / "curl"
        write_generation_phase_curl(self.fake_curl)
        self.fake_sysctl = self.root / "sysctl"
        self.fake_sysctl.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
{
  printf 'sysctl'
  printf ' %q' "$@"
  printf '\n'
} >>"$STACK_SYSCTL_LOG"
[[ "$#" == 2 && "$1" == -n && "$2" == net.ipv4.ip_forward ]] || exit 72
printf '%s\n' "${STACK_FAKE_IP_FORWARD-1}"
""",
            encoding="utf-8",
        )
        self.fake_sysctl.chmod(0o755)
        self.fake_df = self.root / "df"
        self.fake_df.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'Avail\\n32212254720\\n'\n",
            encoding="ascii",
        )
        self.fake_df.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{shell_path(repo_path('tests/fakes'))}{os.pathsep}{os.environ.get('PATH', '')}",
            "DOCKER_BIN": shell_path(repo_path("tests/fakes/docker")),
            "CURL_BIN": shell_path(self.fake_curl),
            "SYSCTL_BIN": shell_path(self.fake_sysctl),
            "DF_BIN": shell_path(self.fake_df),
            "NVIDIA_SMI_BIN": shell_path(repo_path("tests/fakes/nvidia-smi")),
            "STACK_DOCKER_LOG": shell_path(self.log),
            "STACK_SYSCTL_LOG": shell_path(self.sysctl_log),
            "STACK_NVIDIA_LOG": shell_path(self.nvidia_log),
            "STACK_FAKE_CURL_DELEGATE": shell_path(repo_path("tests/fakes/docker")),
            "STACK_FAKE_CURL_STATE": shell_path(self.curl_state),
        }

    def tearDown(self):
        self.temp.cleanup()

    def make_archive(
        self, name="20260802T120000Z-a1b2c3d", extra_members=(), omit_members=()
    ):
        archive = self.root / "incoming" / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for relative in self.RELEASE_MEMBERS:
                if relative in omit_members:
                    continue
                output.add(repo_path(relative), arcname=relative)
            for info, content in extra_members:
                output.addfile(info, io.BytesIO(content) if content is not None else None)
        checksum = self.root / "incoming" / f"{name}.sha256"
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        return archive, checksum, name

    def make_incoming_env(self, content=None):
        self.env_counter += 1
        incoming_env = self.root / "incoming" / f"runtime-env-{self.env_counter:04d}"
        if content is None:
            content = (self.root / "runtime/.env").read_bytes()
        if isinstance(content, str):
            content = content.encode("utf-8")
        incoming_env.write_bytes(content)
        return incoming_env

    def deploy(self, archive, checksum, profiles="core", runtime_env=None, **env):
        if runtime_env is None:
            runtime_env = self.make_incoming_env()
        return subprocess.run([
            BASH_BIN, shell_path(repo_path("scripts/remote/deploy-release.sh")),
            "--root", shell_path(self.root), "--archive", shell_path(archive),
            "--checksum", shell_path(checksum), "--env", shell_path(runtime_env),
            "--profiles", profiles,
        ], env={**self.env, **env}, capture_output=True, text=True)

    def set_current(self, name):
        release = self.root / "releases" / name
        release.mkdir()
        self.mark_success(release)
        os.symlink(f"releases/{name}", self.root / "current")

    def install_prior_release(self, name, compose_script=None):
        archive, checksum, _ = self.make_archive(name)
        release = self.root / "releases" / name
        release.mkdir()
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(release, filter="data")
        archive.unlink()
        checksum.unlink()
        if compose_script is not None:
            (release / "scripts/remote/compose.sh").write_text(
                compose_script, encoding="utf-8"
            )
        self.mark_success(release)
        os.symlink(f"releases/{name}", self.root / "current")
        return release

    def docker_calls(self):
        if not self.log.exists():
            return []
        return [
            shlex.split(line)
            for line in self.log.read_text(encoding="utf-8").splitlines()
        ]

    def sysctl_calls(self):
        return [
            shlex.split(line)
            for line in self.sysctl_log.read_text(encoding="utf-8").splitlines()
        ]

    def compose_calls(self, operation):
        return [
            call for call in self.docker_calls()
            if call and call[0] == "docker" and operation in call
        ]

    @staticmethod
    def mark_success(release):
        (release / ".release-digest").write_text(f"{'a' * 64}\n", encoding="ascii")
        (release / ".successful").touch()

    @staticmethod
    def regular_member(name, content=b"fixture"):
        info = tarfile.TarInfo(name)
        info.size = len(content)
        info.mode = 0o644
        return info, content

    def assert_no_private_staging(self):
        self.assertEqual([], [
            path.name for path in (self.root / "runtime").iterdir()
            if path.name.startswith(".deploy-")
        ])

    def make_env_swap_copy(self, mode):
        wrapper = self.root / f"swap-{mode}-cp"
        real_copy = shutil.which("cp")
        self.assertIsNotNone(real_copy)
        if mode == "leaf":
            mutation = (
                'mv -- "$STACK_RUNTIME_ENV" "$STACK_RUNTIME_ENV.original"\n'
                'ln -s -- "$STACK_REDIRECT_ENV" "$STACK_RUNTIME_ENV"\n'
            )
        else:
            mutation = (
                'mv -- "$STACK_RUNTIME_DIR" "$STACK_RUNTIME_DIR.original"\n'
                'mkdir -- "$STACK_RUNTIME_DIR"\n'
                'ln -s -- "$STACK_REDIRECT_ENV" "$STACK_RUNTIME_ENV"\n'
            )
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ ! -e "$STACK_SWAP_MARKER" ]]; then\n'
            f"{mutation}"
            '  : >"$STACK_SWAP_MARKER"\n'
            "fi\n"
            f"exec {shlex.quote(real_copy)} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def make_failing_docker(self):
        wrapper = self.root / "failing-docker"
        fake = shell_path(repo_path("tests/fakes/docker"))
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -u\n"
            f"{shlex.quote(fake)} \"$@\"\n"
            "delegate_status=$?\n"
            "joined=\" $* \"\n"
            "if [[ -n \"${STACK_TEST_FAIL_OPERATION:-}\" && "
            "\"$joined\" == *\" ${STACK_TEST_FAIL_OPERATION} \"* ]]; then\n"
            "  exit \"${STACK_TEST_FAIL_STATUS:-73}\"\n"
            "fi\n"
            "if [[ -n \"${STACK_TEST_FAIL_CLEANUP:-}\" && \"$joined\" == *\" rm \"* ]]; then\n"
            "  exit \"${STACK_TEST_CLEANUP_STATUS:-91}\"\n"
            "fi\n"
            "exit \"$delegate_status\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def make_failing_move(self):
        wrapper = self.root / "failing-current-mv"
        real_move = shutil.which("mv")
        self.assertIsNotNone(real_move)
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "destination=${@: -1}\n"
            "if [[ \"$destination\" == */current ]]; then exit 79; fi\n"
            f"exec {shlex.quote(real_move)} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def make_corrupting_activation_move(self, mode):
        wrapper = self.root / f"corrupting-current-mv-{mode}"
        real_move = shutil.which("mv")
        self.assertIsNotNone(real_move)
        if mode == "outside":
            mutation = (
                'rm -f -- "$source"\n'
                'ln -s -- "$STACK_TEST_OUTSIDE_TARGET" "$source"\n'
                f'exec {shlex.quote(real_move)} "$@"\n'
            )
        else:
            mutation = (
                f'{shlex.quote(real_move)} "$@"\n'
                f'{shlex.quote(real_move)} -- "$STACK_TEST_SWAP_RELEASE" '
                '"$STACK_TEST_SWAP_RELEASE.verified"\n'
                'mkdir -- "$STACK_TEST_SWAP_RELEASE"\n'
                'printf replacement >"$STACK_TEST_SWAP_RELEASE/replacement-sentinel"\n'
                'exit 0\n'
            )
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "source=${@: -2:1}\n"
            "destination=${@: -1}\n"
            "if [[ \"$destination\" == */current ]]; then\n"
            f"{''.join('  ' + line + chr(10) for line in mutation.splitlines())}"
            "fi\n"
            f"exec {shlex.quote(real_move)} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def make_post_up_release_swap_docker(self):
        wrapper = self.root / "post-up-release-swap-docker"
        fake = shell_path(repo_path("tests/fakes/docker"))
        real_move = shutil.which("mv")
        self.assertIsNotNone(real_move)
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"{shlex.quote(fake)} \"$@\"\n"
            "joined=\" $* \"\n"
            "if [[ \"$joined\" == *\" up \"* && ! -e \"$STACK_TEST_SWAP_MARKER\" ]]; then\n"
            f"  {shlex.quote(real_move)} -- \"$STACK_TEST_SWAP_RELEASE\" \"$STACK_TEST_SWAP_RELEASE.verified\"\n"
            "  mkdir -- \"$STACK_TEST_SWAP_RELEASE\"\n"
            "  printf replacement >\"$STACK_TEST_SWAP_RELEASE/replacement-sentinel\"\n"
            "  : >\"$STACK_TEST_SWAP_MARKER\"\n"
            "fi\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def assert_private_runtime_env_used(self):
        calls = [
            shlex.split(line)
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if " --env-file " in line
        ]
        runtime_envs = []
        for call in calls:
            indices = [index for index, value in enumerate(call) if value == "--env-file"]
            self.assertEqual(2, len(indices))
            runtime_envs.append(call[indices[1] + 1])
        self.assertEqual(1, len(set(runtime_envs)))
        snapshot = runtime_envs[0]
        self.assertIn(".deploy-", snapshot)
        self.assertTrue(snapshot.endswith("/runtime.env"), snapshot)
        self.assertNotEqual(shell_path(self.root / "runtime/.env"), snapshot)

    def make_release_leaf_swap_move(self, relative, replacement):
        wrapper = self.root / f"swap-{relative.replace('/', '-')}-mv"
        real_move = shutil.which("mv")
        self.assertIsNotNone(real_move)
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'source_path=${@: -2:1}\n'
            'destination=${@: -1}\n'
            f"{shlex.quote(real_move)} \"$@\"\n"
            'if [[ "$source_path" == */extracted ]]; then\n'
            f"  leaf=\"$destination/{relative}\"\n"
            '  mv -- "$leaf" "$leaf.verified"\n'
            f"  printf %s {shlex.quote(replacement)} >\"$leaf\"\n"
            "fi\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def test_release_requires_preflight_ollama_bootstrap_and_chroma_build_inputs(self):
        required = (
            "scripts/remote/preflight.sh",
            "config/ollama/bootstrap.sh",
            "images/chromadb-admin/Dockerfile",
            "images/chromadb-admin/install-dependencies.sh",
            ".dockerignore",
            "vendor/chromadb-admin/package.json",
            "vendor/chromadb-admin/package-lock.json",
            "vendor/chromadb-admin/LICENSE.txt",
            "vendor/chromadb-admin/UPSTREAM.md",
        )
        for index, relative in enumerate(required):
            with self.subTest(relative=relative):
                archive, checksum, _ = self.make_archive(
                    f"20260802T115{index}00Z-missing", omit_members=(relative,)
                )
                before = self.log.read_bytes() if self.log.exists() else b""
                result = self.deploy(
                    archive, checksum, STACK_FAKE_FAIL_COMMAND="config"
                )
                self.assertNotEqual(0, result.returncode)
                after = self.log.read_bytes() if self.log.exists() else b""
                self.assertEqual(before, after, f"Docker ran without {relative}")
                self.assertIn(relative, result.stderr)

    def test_deploy_runs_preflight_pull_ignore_buildable_build_up_health_in_order(self):
        archive, checksum, _ = self.make_archive("20260802T115800Z-order")
        result = self.deploy(archive, checksum, "vector")
        self.assertEqual(0, result.returncode, result.stderr)
        operations = []
        for call in self.docker_calls():
            if "config" in call:
                operations.append(
                    "preflight" if "--format" in call else "config"
                )
            elif "pull" in call:
                self.assertEqual(["pull", "--ignore-buildable"], call[-2:])
                operations.append("pull --ignore-buildable")
            elif "build" in call:
                self.assertEqual(["build", "--pull", "chroma-admin"], call[-3:])
                operations.append("build --pull chroma-admin")
            elif "up" in call:
                self.assertEqual(["up", "-d", "--wait"], call[-3:])
                operations.append("up -d --wait")
            elif "ps" in call:
                operations.append("health")
        self.assertEqual([
            "config", "preflight", "pull --ignore-buildable",
            "build --pull chroma-admin", "up -d --wait", "health",
        ], operations)

    def test_deploy_rejects_disabled_ipv4_forwarding_before_image_or_container_mutation(self):
        archive, checksum, _ = self.make_archive(
            "20260802T115850Z-forwarding-disabled"
        )

        result = self.deploy(
            archive, checksum, "core", STACK_FAKE_IP_FORWARD="0"
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("net.ipv4.ip_forward must equal 1; found 0", result.stderr)
        self.assertEqual(
            [["sysctl", "-n", "net.ipv4.ip_forward"]], self.sysctl_calls()
        )
        calls = self.docker_calls()
        self.assertEqual(1, sum("config" in call for call in calls))
        cleanup = [call for call in calls if "rm" in call]
        self.assertEqual(1, len(cleanup))
        self.assertEqual(
            ["rm", "-sf", "app-postgres", "app-redis"], cleanup[0][-4:]
        )
        self.assertNotIn("-v", cleanup[0])
        self.assertNotIn("--volumes", cleanup[0])
        for operation in ("pull", "build", "up", "ps"):
            self.assertFalse(
                any(operation in call for call in calls),
                f"unexpected {operation} after forwarding preflight failure: {calls}",
            )

    def test_non_vector_deploy_does_not_build_chroma_admin(self):
        archive, checksum, _ = self.make_archive("20260802T115900Z-no-build")
        result = self.deploy(archive, checksum, "core")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.compose_calls("build"))

    def test_failed_first_inference_deploy_removes_containers_but_preserves_volumes(self):
        archive, checksum, name = self.make_archive("20260802T120010Z-first-inference")
        result = self.deploy(
            archive, checksum, "inference", STACK_FAKE_FAIL_COMMAND="up"
        )
        self.assertNotEqual(0, result.returncode)
        cleanup = self.compose_calls("rm")
        self.assertEqual(1, len(cleanup), self.log.read_text(encoding="utf-8") if self.log.exists() else "")
        self.assertEqual(
            ["rm", "-sf", "ollama-llm", "ollama-embedding"], cleanup[0][-4:]
        )
        self.assertNotIn("-v", cleanup[0])
        self.assertEqual([], self.compose_calls("down"))
        self.assertFalse((self.root / "current").exists())
        failed = self.root / "releases" / name
        self.assertTrue((failed / ".release-digest").is_file())
        self.assertFalse((failed / ".successful").exists())

    def test_each_inference_health_failure_preserves_current_and_ollama_volumes(self):
        cases = (
            ("cold", {"STACK_FAKE_FAIL_OLLAMA_GENERATION_PHASE": "cold"}, "/api/generate"),
            ("warm", {"STACK_FAKE_FAIL_OLLAMA_GENERATION_PHASE": "warm"}, "11440/api/ps"),
            ("embed", {"STACK_FAKE_FAIL_OLLAMA_REQUEST": "embed"}, "/api/embed"),
            ("json", {"STACK_FAKE_GENERATE_RESPONSE": "not-json"}, "/api/generate"),
            ("model", {"STACK_FAKE_LLM_PS_RESPONSE": '{"models":[]}'}, "11440/api/ps"),
            ("partial-vram", {"STACK_FAKE_LLM_PS_RESPONSE": '{"models":[{"name":"gemma4:e4b","size":1024,"size_vram":512}]}'}, "11440/api/ps"),
            ("vram", {"STACK_FAKE_EMBEDDING_PS_RESPONSE": '{"models":[{"name":"embeddinggemma:300m","size_vram":0}]}'}, "11441/api/ps"),
            ("llm-device", {"STACK_FAKE_DEVICE_REQUEST_OLLAMA_LLM": "[]"}, "container-ollama-llm"),
            ("embedding-device", {"STACK_FAKE_DEVICE_REQUEST_OLLAMA_EMBEDDING": "[]"}, "container-ollama-embedding"),
            ("host-memory", {"STACK_FAKE_COMPUTE_MEMORY": "0"}, "/api/embed"),
        )
        original_root = self.root
        original_log = self.log
        try:
            for index, (label, failure_env, reached_boundary) in enumerate(cases):
                with self.subTest(label=label):
                    self.curl_state.unlink(missing_ok=True)
                    case_root = original_root / f"inference-health-{label}"
                    for child in ("incoming", "runtime", "releases"):
                        (case_root / child).mkdir(parents=True)
                    write_test_env(case_root / "runtime/.env")
                    self.root = case_root
                    self.log = case_root / "docker.log"
                    prior_name = f"20260802T01{index:02d}00Z-prior"
                    self.install_prior_release(prior_name)
                    archive, checksum, name = self.make_archive(
                        f"20260802T13{index:02d}00Z-{label}"
                    )

                    result = self.deploy(
                        archive, checksum, "inference",
                        STACK_DOCKER_LOG=shell_path(self.log),
                        STACK_NVIDIA_LOG=shell_path(case_root / "nvidia.log"),
                        **failure_env,
                    )

                    self.assertNotEqual(0, result.returncode)
                    rendered_calls = "\n".join(
                        " ".join(call) for call in self.docker_calls()
                    )
                    self.assertIn(reached_boundary, rendered_calls, result.stderr)
                    self.assertEqual(
                        f"releases/{prior_name}", os.readlink(case_root / "current")
                    )
                    cleanup = self.compose_calls("rm")
                    self.assertEqual(1, len(cleanup))
                    self.assertNotIn("-v", cleanup[0])
                    self.assertNotIn("--volumes", cleanup[0])
                    self.assertFalse(any("volume" in call for call in self.docker_calls()))
                    failed = case_root / "releases" / name
                    self.assertTrue((failed / ".release-digest").is_file())
                    self.assertFalse((failed / ".successful").exists())
        finally:
            self.root = original_root
            self.log = original_log

    def test_failed_upgrade_restores_previous_runtime_env_and_selected_services(self):
        prior = self.install_prior_release("20260802T010000Z-prior")
        prior_versions = prior / "versions.env"
        prior_compose_file = prior / "compose.yaml"
        prior_versions.write_bytes(prior_versions.read_bytes() + b"# prior versions\n")
        prior_compose_file.write_bytes(prior_compose_file.read_bytes() + b"# prior compose\n")
        prior_versions_hash = hashlib.sha256(prior_versions.read_bytes()).hexdigest()
        prior_compose_hash = hashlib.sha256(prior_compose_file.read_bytes()).hexdigest()
        prior_script = prior / "scripts/remote/compose.sh"
        prior_real = prior / "scripts/remote/compose-real.sh"
        prior_script.rename(prior_real)
        prior_log = self.root / "prior-inputs.log"
        prior_script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "{\n"
            "  printf 'release=%s\\nheld=%s\\nruntime=' \"$STACK_RELEASE_DIR\" \"$STACK_RELEASE_HELD_DIR\"\n"
            "  sha256sum -- \"$STACK_RUNTIME_ENV_FILE\" | awk '{ print $1 }'\n"
            "  printf 'versions='\n"
            "  sha256sum -- \"$STACK_VERSIONS_ENV_FILE\" | awk '{ print $1 }'\n"
            "  printf 'compose='\n"
            "  sha256sum -- \"$STACK_COMPOSE_FILE\" | awk '{ print $1 }'\n"
            f"}} >>{shlex.quote(shell_path(prior_log))}\n"
            "exec bash \"$STACK_RELEASE_HELD_DIR/scripts/remote/compose-real.sh\" \"$@\"\n",
            encoding="utf-8",
        )
        prior_script.chmod(0o755)
        old_env = (self.root / "runtime/.env").read_bytes() + b"PAIR_MARKER=prior\n"
        (self.root / "runtime/.env").write_bytes(old_env)
        incoming = self.make_incoming_env(old_env + b"PAIR_MARKER=new\n")
        archive, checksum, _ = self.make_archive("20260802T120020Z-upgrade")
        unhealthy = '[{"Service":"app-postgres","State":"running","Health":"unhealthy"}]'
        result = self.deploy(
            archive, checksum, "core,vector", runtime_env=incoming,
            STACK_FAKE_PS_JSON=unhealthy,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(old_env, (self.root / "runtime/.env").read_bytes())
        up_calls = self.compose_calls("up")
        self.assertGreaterEqual(len(up_calls), 2)
        restored = up_calls[-1]
        project_index = restored.index("--project-directory") + 1
        self.assertEqual(str(prior.resolve()), restored[project_index])
        self.assertIn("--profile", restored)
        self.assertIn("core", restored)
        self.assertIn("vector", restored)
        prior_inputs = prior_log.read_text(encoding="utf-8")
        self.assertIn(f"release={prior.resolve()}", prior_inputs)
        self.assertIn(hashlib.sha256(old_env).hexdigest(), prior_inputs)
        self.assertIn(f"versions={prior_versions_hash}", prior_inputs)
        self.assertIn(f"compose={prior_compose_hash}", prior_inputs)
        self.assertNotIn("120020Z-upgrade", prior_inputs)

    def test_failed_first_deploy_restores_absent_runtime_env(self):
        old_env = (self.root / "runtime/.env").read_bytes()
        (self.root / "runtime/.env").unlink()
        incoming = self.make_incoming_env(old_env)
        archive, checksum, _ = self.make_archive("20260802T120030Z-absent-env")
        result = self.deploy(
            archive, checksum, "inference", runtime_env=incoming,
            STACK_FAKE_FAIL_COMMAND="up",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "runtime/.env").exists())
        self.assertEqual(1, len(self.compose_calls("rm")))

    def test_failed_upgrade_ignores_profile_unsupported_by_prior_release(self):
        prior = self.install_prior_release("20260802T010100Z-prior-old")
        prior_script = prior / "scripts/remote/compose.sh"
        prior_real = prior / "scripts/remote/compose-real.sh"
        prior_script.rename(prior_real)
        prior_script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "for argument in \"$@\"; do\n"
            "  [[ \"$argument\" != inference ]] || exit 64\n"
            "  [[ \"$argument\" != -- ]] || break\n"
            "done\n"
            "exec bash \"$STACK_RELEASE_HELD_DIR/scripts/remote/compose-real.sh\" \"$@\"\n",
            encoding="utf-8",
        )
        prior_script.chmod(0o755)
        archive, checksum, _ = self.make_archive("20260802T120040Z-new-profile")
        result = self.deploy(
            archive, checksum, "core,inference", STACK_FAKE_FAIL_COMMAND="up"
        )
        self.assertNotEqual(0, result.returncode)
        up_calls = self.compose_calls("up")
        prior_up = [
            call for call in up_calls
            if str(prior.resolve()) in call
        ]
        self.assertEqual(1, len(prior_up))
        self.assertIn("core", prior_up[0])
        self.assertNotIn("inference", prior_up[0])

    def test_failed_deploy_never_writes_success_or_changes_current_or_prunes(self):
        self.install_prior_release("20260802T010200Z-current")
        preserved = self.root / "releases/20260802T000000Z-preserved"
        preserved.mkdir()
        self.mark_success(preserved)
        (preserved / "sentinel").write_bytes(b"preserve exactly\x00\xff")
        current_before = os.readlink(self.root / "current")
        existing_before = {
            str(path.relative_to(self.root / "releases")): path.read_bytes()
            for path in (self.root / "releases").rglob("*") if path.is_file()
        }
        archive, checksum, name = self.make_archive("20260802T120050Z-failed")
        result = self.deploy(archive, checksum, "vector", STACK_FAKE_FAIL_COMMAND="up")
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(current_before, os.readlink(self.root / "current"))
        existing_after = {
            key: (self.root / "releases" / key).read_bytes()
            for key in existing_before
        }
        self.assertEqual(existing_before, existing_after)
        failed = self.root / "releases" / name
        self.assertTrue((failed / ".release-digest").is_file())
        self.assertFalse((failed / ".successful").exists())

    def test_preflight_failure_rolls_back_before_pull_build_or_up(self):
        previous = (self.root / "runtime/.env").read_bytes()
        incoming = self.make_incoming_env(previous + b"PAIR_MARKER=new\n")
        archive, checksum, _ = self.make_archive("20260802T120060Z-preflight-fails")
        failing_df = self.root / "failing-df"
        failing_df.write_text("#!/usr/bin/env bash\nexit 74\n", encoding="ascii")
        failing_df.chmod(0o755)
        result = self.deploy(
            archive, checksum, "vector", runtime_env=incoming,
            DF_BIN=shell_path(failing_df),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(previous, (self.root / "runtime/.env").read_bytes())
        self.assertEqual(1, len(self.compose_calls("rm")))
        for operation in ("pull", "build", "up", "ps"):
            self.assertEqual([], self.compose_calls(operation))

    def test_build_failure_rolls_back_vector_services(self):
        previous = (self.root / "runtime/.env").read_bytes()
        incoming = self.make_incoming_env(previous + b"PAIR_MARKER=new\n")
        archive, checksum, _ = self.make_archive("20260802T120070Z-build-fails")
        result = self.deploy(
            archive, checksum, "vector", runtime_env=incoming,
            DOCKER_BIN=shell_path(self.make_failing_docker()),
            STACK_TEST_FAIL_OPERATION="build", STACK_TEST_FAIL_STATUS="73",
        )
        self.assertEqual(73, result.returncode, result.stderr)
        self.assertEqual(previous, (self.root / "runtime/.env").read_bytes())
        cleanup = self.compose_calls("rm")
        self.assertEqual(1, len(cleanup))
        self.assertEqual(["rm", "-sf", "chroma", "chroma-admin"], cleanup[0][-4:])
        self.assertEqual([], self.compose_calls("up"))

    def test_cleanup_failure_does_not_replace_original_failure_status(self):
        previous = (self.root / "runtime/.env").read_bytes()
        incoming = self.make_incoming_env(previous + b"PAIR_MARKER=new\n")
        archive, checksum, _ = self.make_archive("20260802T120080Z-cleanup-fails")
        result = self.deploy(
            archive, checksum, "core", runtime_env=incoming,
            DOCKER_BIN=shell_path(self.make_failing_docker()),
            STACK_TEST_FAIL_OPERATION="up", STACK_TEST_FAIL_STATUS="73",
            STACK_TEST_FAIL_CLEANUP="1", STACK_TEST_CLEANUP_STATUS="91",
        )
        self.assertEqual(73, result.returncode, result.stderr)
        self.assertEqual(previous, (self.root / "runtime/.env").read_bytes())
        cleanup = self.compose_calls("rm")
        self.assertEqual(1, len(cleanup))
        self.assertNotIn("-v", cleanup[0])

    def test_activation_failure_restores_prior_current_env_and_has_no_success_marker(self):
        self.install_prior_release("20260802T010300Z-prior")
        previous = (self.root / "runtime/.env").read_bytes()
        incoming = self.make_incoming_env(previous + b"PAIR_MARKER=new\n")
        archive, checksum, name = self.make_archive("20260802T120090Z-activation-fails")
        result = self.deploy(
            archive, checksum, "core", runtime_env=incoming,
            MV_BIN=shell_path(self.make_failing_move()),
        )
        self.assertEqual(79, result.returncode, result.stderr)
        self.assertEqual(
            "releases/20260802T010300Z-prior", os.readlink(self.root / "current")
        )
        self.assertEqual(previous, (self.root / "runtime/.env").read_bytes())
        self.assertFalse((self.root / "releases" / name / ".successful").exists())
        self.assertEqual(1, len(self.compose_calls("rm")))
        self.assertEqual([], list(self.root.glob(".current.*")))

    def test_activation_rejects_move_that_replaces_current_with_outside_target(self):
        self.install_prior_release("20260802T010310Z-prior")
        previous = (self.root / "runtime/.env").read_bytes()
        incoming = self.make_incoming_env(previous + b"PAIR_MARKER=new\n")
        outside = self.root / "outside-activation-target"
        outside.mkdir()
        archive, checksum, name = self.make_archive("20260802T120091Z-outside-current")
        result = self.deploy(
            archive, checksum, "core", runtime_env=incoming,
            MV_BIN=shell_path(self.make_corrupting_activation_move("outside")),
            STACK_TEST_OUTSIDE_TARGET=shell_path(outside),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(
            "releases/20260802T010310Z-prior", os.readlink(self.root / "current")
        )
        self.assertEqual(previous, (self.root / "runtime/.env").read_bytes())
        self.assertFalse((self.root / "releases" / name / ".successful").exists())
        self.assertFalse((outside / ".successful").exists())

    def test_activation_release_swap_never_marks_replacement_or_displaced_release_successful(self):
        self.install_prior_release("20260802T010320Z-prior")
        previous = (self.root / "runtime/.env").read_bytes()
        incoming = self.make_incoming_env(previous + b"PAIR_MARKER=new\n")
        archive, checksum, name = self.make_archive("20260802T120092Z-release-swapped")
        release = self.root / "releases" / name
        result = self.deploy(
            archive, checksum, "core", runtime_env=incoming,
            MV_BIN=shell_path(self.make_corrupting_activation_move("release")),
            STACK_TEST_SWAP_RELEASE=shell_path(release),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(
            "releases/20260802T010320Z-prior", os.readlink(self.root / "current")
        )
        self.assertEqual(previous, (self.root / "runtime/.env").read_bytes())
        self.assertFalse((release / ".successful").exists())
        self.assertFalse((Path(f"{release}.verified") / ".successful").exists())

    def test_post_up_release_swap_still_removes_exact_attempted_services(self):
        previous = (self.root / "runtime/.env").read_bytes()
        incoming = self.make_incoming_env(previous + b"PAIR_MARKER=new\n")
        archive, checksum, name = self.make_archive("20260802T120093Z-post-up-swap")
        release = self.root / "releases" / name
        result = self.deploy(
            archive, checksum, "core", runtime_env=incoming,
            DOCKER_BIN=shell_path(self.make_post_up_release_swap_docker()),
            STACK_TEST_SWAP_RELEASE=shell_path(release),
            STACK_TEST_SWAP_MARKER=shell_path(self.root / "post-up-swap-done"),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(1, len(self.compose_calls("up")))
        cleanup = self.compose_calls("rm")
        self.assertEqual(1, len(cleanup))
        self.assertEqual(
            ["rm", "-sf", "app-postgres", "app-redis"], cleanup[0][-4:]
        )
        self.assertNotIn("-v", cleanup[0])
        project_index = cleanup[0].index("--project-directory") + 1
        self.assertEqual(str(release), cleanup[0][project_index])
        compose_index = cleanup[0].index("--file") + 1
        self.assertRegex(cleanup[0][compose_index], r"^/proc/self/fd/[0-9]+$")
        env_indices = [
            index for index, value in enumerate(cleanup[0]) if value == "--env-file"
        ]
        self.assertEqual(2, len(env_indices))
        self.assertRegex(cleanup[0][env_indices[0] + 1], r"^/proc/self/fd/[0-9]+$")
        self.assertIn(".deploy-", cleanup[0][env_indices[1] + 1])
        self.assertTrue(cleanup[0][env_indices[1] + 1].endswith("/runtime.env"))
        self.assertEqual([], self.compose_calls("down"))
        self.assertEqual(previous, (self.root / "runtime/.env").read_bytes())
        self.assertFalse((self.root / "current").exists())
        self.assertFalse((release / ".successful").exists())

    def test_parent_die_after_release_swap_still_restores_runtime_env(self):
        previous = (self.root / "runtime/.env").read_bytes()
        incoming = self.make_incoming_env(previous + b"PAIR_MARKER=new\n")
        archive, checksum, name = self.make_archive("20260802T120100Z-parent-die")
        release = self.root / "releases" / name
        result = self.deploy(
            archive, checksum, "core", runtime_env=incoming,
            STACK_FAKE_SWAP_RELEASE=shell_path(release),
            STACK_FAKE_SWAP_MARKER=shell_path(self.root / "release-swapped-for-die"),
            STACK_FAKE_TAMPER_MARKER=shell_path(self.root / "replacement-executed-for-die"),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(previous, (self.root / "runtime/.env").read_bytes())
        self.assertFalse((self.root / "current").exists())
        self.assertFalse((release / ".successful").exists())

    def test_prior_restart_failure_does_not_replace_health_failure_status(self):
        prior = self.install_prior_release("20260802T010400Z-prior-restart-fails")
        prior_script = prior / "scripts/remote/compose.sh"
        prior_real = prior / "scripts/remote/compose-real.sh"
        prior_script.rename(prior_real)
        prior_script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "joined=\" $* \"\n"
            "[[ \"$joined\" != *\" up \"* ]] || exit 91\n"
            "exec bash \"$STACK_RELEASE_HELD_DIR/scripts/remote/compose-real.sh\" \"$@\"\n",
            encoding="utf-8",
        )
        prior_script.chmod(0o755)
        previous = (self.root / "runtime/.env").read_bytes()
        incoming = self.make_incoming_env(previous + b"PAIR_MARKER=new\n")
        archive, checksum, _ = self.make_archive("20260802T120110Z-restart-fails")
        unhealthy = '[{"Service":"app-postgres","State":"running","Health":"unhealthy"}]'
        result = self.deploy(
            archive, checksum, "core", runtime_env=incoming,
            STACK_FAKE_PS_JSON=unhealthy,
        )
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual(previous, (self.root / "runtime/.env").read_bytes())
        self.assertEqual(
            "releases/20260802T010400Z-prior-restart-fails",
            os.readlink(self.root / "current"),
        )

    def test_rollback_never_executes_current_release_symlink_outside_releases(self):
        outside = self.root / "outside-prior"
        outside.mkdir()
        marker = self.root / "outside-prior-executed"
        (outside / ".successful").touch()
        (outside / ".release-digest").write_text(f"{'a' * 64}\n", encoding="ascii")
        (outside / "scripts/remote").mkdir(parents=True)
        (outside / "scripts/remote/compose.sh").write_text(
            "#!/usr/bin/env bash\n"
            f": >{shlex.quote(shell_path(marker))}\n",
            encoding="utf-8",
        )
        os.symlink(outside, self.root / "releases/escaped", target_is_directory=True)
        os.symlink("releases/escaped", self.root / "current")
        archive, checksum, _ = self.make_archive("20260802T120120Z-untrusted-prior")
        result = self.deploy(archive, checksum, "core", STACK_FAKE_FAIL_COMMAND="up")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(marker.exists())
        self.assertEqual("releases/escaped", os.readlink(self.root / "current"))

    def test_checksum_mismatch_fails_before_extraction_or_docker(self):
        archive, checksum, name = self.make_archive()
        self.set_current("20260802T010000Z-current")
        previous_env = (self.root / "runtime/.env").read_bytes()
        incoming_env = self.make_incoming_env(previous_env + b"PAIR_MARKER=new\n")
        checksum.write_text(f"{'0' * 64}  {archive.name}\n", encoding="ascii")
        result = self.deploy(archive, checksum, runtime_env=incoming_env)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "releases" / name).exists())
        self.assertFalse(self.log.exists())
        self.assertEqual("releases/20260802T010000Z-current", os.readlink(self.root / "current"))
        self.assertEqual(previous_env, (self.root / "runtime/.env").read_bytes())
        self.assertFalse(incoming_env.exists())

    def test_requires_direct_regular_incoming_environment(self):
        archive, checksum, _ = self.make_archive("20260802T120100Z-env-input")
        command = [
            BASH_BIN, shell_path(repo_path("scripts/remote/deploy-release.sh")),
            "--root", shell_path(self.root), "--archive", shell_path(archive),
            "--checksum", shell_path(checksum), "--profiles", "core",
        ]
        missing = subprocess.run(
            command, env=self.env, capture_output=True, text=True
        )
        self.assertNotEqual(0, missing.returncode)
        self.assertIn("--env", missing.stderr)

        outside = Path(self.temp.name) / "outside.env"
        outside.write_bytes((self.root / "runtime/.env").read_bytes())
        rejected = self.deploy(archive, checksum, runtime_env=outside)
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("incoming", rejected.stderr.lower())
        self.assertTrue(outside.exists())

        target = self.make_incoming_env()
        link = self.root / "incoming/runtime-env-link"
        os.symlink(target.name, link)
        rejected = self.deploy(archive, checksum, runtime_env=link)
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("non-symlink", rejected.stderr.lower())
        self.assertTrue(target.exists())

    def test_success_installs_exact_incoming_environment_atomically(self):
        archive, checksum, _ = self.make_archive("20260802T120200Z-env-install")
        incoming = self.make_incoming_env(
            (self.root / "runtime/.env").read_bytes() + b"PAIR_MARKER=exact\n"
        )
        expected = incoming.read_bytes()
        result = self.deploy(archive, checksum, runtime_env=incoming)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, (self.root / "runtime/.env").read_bytes())
        self.assertEqual(0o600, (self.root / "runtime/.env").stat().st_mode & 0o777)
        self.assertFalse(incoming.exists())
        self.assert_private_runtime_env_used()

    def test_success_activates_atomically_and_prunes_only_old_successes(self):
        self.set_current("20260802T090000Z-oldcurrent")
        for name in ("20260802T100000Z-old", "20260802T110000Z-keep"):
            release = self.root / "releases" / name
            release.mkdir()
            self.mark_success(release)
        failed = self.root / "releases/20260802T080000Z-failed"
        failed.mkdir()
        archive, checksum, name = self.make_archive()
        result = self.deploy(archive, checksum, "core,vector")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(f"releases/{name}", os.readlink(self.root / "current"))
        self.assertTrue((self.root / "releases" / name / ".successful").exists())
        self.assertFalse((self.root / "releases/20260802T090000Z-oldcurrent").exists())
        self.assertTrue((self.root / "releases/20260802T100000Z-old").exists())
        self.assertTrue((self.root / "releases/20260802T110000Z-keep").exists())
        self.assertTrue(failed.exists())
        calls = self.log.read_text(encoding="utf-8")
        for operation in ("config", "pull", "up", "ps"):
            self.assertIn(operation, calls)
        self.assertEqual(0o600, (self.root / "runtime/.env").stat().st_mode & 0o777)

    def test_compose_project_directory_uses_canonical_host_release_path(self):
        archive, checksum, name = self.make_archive("20260802T121000Z-canonical")
        render_log = self.root / "rendered-paths.log"
        result = self.deploy(
            archive, checksum, STACK_FAKE_RENDER_LOG=shell_path(render_log)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        release = str((self.root / "releases" / name).resolve())
        paths = render_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual([release, release], paths)
        self.assertNotIn("/proc/self/fd", "\n".join(paths))

    def test_post_install_leaf_swaps_cannot_change_verified_inputs(self):
        expected_hashes = {
            relative: hashlib.sha256(repo_path(relative).read_bytes()).hexdigest()
            for relative in (
                "compose.yaml", "versions.env",
                "config/opensearch/opensearch.yml",
                "config/opensearch/docker-entrypoint.sh",
            )
        }
        leaves = (
            "scripts/remote/compose.sh",
            "scripts/remote/preflight.sh",
            "scripts/remote/health.sh",
            "compose.yaml",
            "versions.env",
            "config/opensearch/opensearch.yml",
            "config/opensearch/docker-entrypoint.sh",
        )
        for index, relative in enumerate(leaves):
            with self.subTest(relative=relative):
                marker = self.root / f"executed-{index}"
                if relative.endswith(".sh"):
                    replacement = (
                        "#!/usr/bin/env bash\n"
                        f": >{shlex.quote(shell_path(marker))}\n"
                        "exit 91\n"
                    )
                else:
                    replacement = f"tampered {relative}\n"
                move = self.make_release_leaf_swap_move(relative, replacement)
                archive, checksum, _ = self.make_archive(
                    f"20260802T124{index}00Z-leaf-{index}"
                )
                content_log = self.root / f"content-{index}.log"
                result = self.deploy(
                    archive, checksum, "search", MV_BIN=shell_path(move),
                    STACK_FAKE_CONTENT_LOG=shell_path(content_log),
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertFalse(marker.exists())
                actual = dict(
                    line.split(" ", 1)
                    for line in content_log.read_text(encoding="ascii").splitlines()
                )
                self.assertEqual(expected_hashes, actual)

    def test_runtime_env_leaf_swap_uses_held_private_snapshot(self):
        redirect_env = self.root / "redirect.env"
        write_test_env(redirect_env)
        wrapper = self.make_env_swap_copy("leaf")
        archive, checksum, _ = self.make_archive("20260802T122000Z-env-leaf")
        incoming_env = self.make_incoming_env(
            (self.root / "runtime/.env").read_bytes() + b"PAIR_MARKER=held-leaf\n"
        )
        expected = incoming_env.read_bytes()
        result = self.deploy(
            archive, checksum, "search", runtime_env=incoming_env,
            CP_BIN=shell_path(wrapper),
            STACK_RUNTIME_DIR=shell_path(self.root / "incoming"),
            STACK_RUNTIME_ENV=shell_path(incoming_env),
            STACK_REDIRECT_ENV=shell_path(redirect_env),
            STACK_SWAP_MARKER=shell_path(self.root / "leaf-swapped"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, (self.root / "runtime/.env").read_bytes())
        self.assertFalse((self.root / "runtime/.env").is_symlink())
        self.assert_private_runtime_env_used()

    def test_runtime_directory_swap_uses_held_private_snapshot(self):
        redirect_env = self.root / "redirect.env"
        write_test_env(redirect_env)
        wrapper = self.make_env_swap_copy("directory")
        archive, checksum, _ = self.make_archive("20260802T123000Z-env-directory")
        incoming_env = self.make_incoming_env(
            (self.root / "runtime/.env").read_bytes() + b"PAIR_MARKER=held-directory\n"
        )
        expected = incoming_env.read_bytes()
        result = self.deploy(
            archive, checksum, "search", runtime_env=incoming_env,
            CP_BIN=shell_path(wrapper),
            STACK_RUNTIME_DIR=shell_path(self.root / "incoming"),
            STACK_RUNTIME_ENV=shell_path(incoming_env),
            STACK_REDIRECT_ENV=shell_path(redirect_env),
            STACK_SWAP_MARKER=shell_path(self.root / "directory-swapped"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, (self.root / "runtime/.env").read_bytes())
        self.assertFalse((self.root / "runtime/.env").is_symlink())
        self.assertTrue((self.root / "incoming.original").is_dir())
        self.assert_private_runtime_env_used()

    def test_config_pull_up_and_health_failures_preserve_current_and_do_not_prune(self):
        for failure, env in (
            ("config", {"STACK_FAKE_FAIL_COMMAND": "config"}),
            ("pull", {"STACK_FAKE_FAIL_COMMAND": "pull"}),
            ("up", {"STACK_FAKE_FAIL_COMMAND": "up"}),
            ("health", {"STACK_FAKE_PS_JSON": '[{"Service":"app-postgres","State":"running","Health":"unhealthy"},{"Service":"app-redis","State":"running","Health":"healthy"}]'}),
        ):
            with self.subTest(failure=failure):
                case_root = self.root / failure
                for child in ("incoming", "runtime", "releases"):
                    (case_root / child).mkdir(parents=True, exist_ok=True)
                write_test_env(case_root / "runtime/.env")
                old = case_root / "releases/20260802T010000Z-current"
                old.mkdir()
                self.mark_success(old)
                prune_candidate = case_root / "releases/20260802T000000Z-prune-me"
                prune_candidate.mkdir()
                self.mark_success(prune_candidate)
                os.symlink("releases/20260802T010000Z-current", case_root / "current")
                original_root, self.root = self.root, case_root
                try:
                    archive, checksum, name = self.make_archive(f"20260802T12{failure[:2]}00Z-new")
                    result = self.deploy(archive, checksum, **env)
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual("releases/20260802T010000Z-current", os.readlink(case_root / "current"))
                    self.assertTrue(prune_candidate.exists())
                    self.assertTrue((case_root / "releases" / name).exists())
                    self.assertFalse((case_root / "releases" / name / ".successful").exists())
                    self.assertTrue((case_root / "releases" / name / ".release-digest").is_file())
                finally:
                    self.root = original_root

    def test_rejects_paths_outside_root_and_traversing_checksum_record(self):
        archive, checksum, name = self.make_archive()
        outside = Path(self.temp.name) / "outside.tar.gz"
        shutil.copyfile(archive, outside)
        result = self.deploy(outside, checksum)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "releases" / name).exists())
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksum.write_text(f"{digest}  ../{archive.name}\n", encoding="ascii")
        result = self.deploy(archive, checksum)
        self.assertNotEqual(0, result.returncode)

    def test_rejects_all_link_and_special_member_types_before_extraction(self):
        cases = []
        for label, member_type in (
            ("symlink", tarfile.SYMTYPE),
            ("hardlink", tarfile.LNKTYPE),
            ("fifo", tarfile.FIFOTYPE),
            ("character", tarfile.CHRTYPE),
            ("block", tarfile.BLKTYPE),
            ("socket", b"s"),
        ):
            info = tarfile.TarInfo(f"unsafe-{label}")
            info.type = member_type
            if member_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = "compose.yaml"
            if member_type in (tarfile.CHRTYPE, tarfile.BLKTYPE):
                info.devmajor, info.devminor = 1, 3
            cases.append((label, (info, None)))
        for index, (label, member) in enumerate(cases):
            with self.subTest(member_type=label):
                archive, checksum, name = self.make_archive(
                    f"20260802T13{index:02d}00Z-{label}", (member,)
                )
                result = self.deploy(archive, checksum)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse((self.root / "releases" / name).exists())
                self.assert_no_private_staging()

    def test_rejects_unsafe_names_and_deployer_markers_before_extraction(self):
        names = (
            "../escape", "/absolute", "control\nname", "control\x01name",
            ".successful", ".release-digest", "nested/.successful",
            "nested/.release-digest",
        )
        for index, member_name in enumerate(names):
            with self.subTest(member_name=repr(member_name)):
                archive, checksum, name = self.make_archive(
                    f"20260802T14{index:02d}00Z-name", (self.regular_member(member_name),)
                )
                result = self.deploy(archive, checksum)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse((self.root / "releases" / name).exists())
                self.assert_no_private_staging()

    def test_runtime_releases_and_lock_cannot_redirect_deployment(self):
        for child in ("runtime", "releases"):
            with self.subTest(child=child):
                case_root = self.root / f"symlink-{child}"
                (case_root / "incoming").mkdir(parents=True)
                outside = self.root / f"outside-{child}"
                outside.mkdir()
                if child == "runtime":
                    write_test_env(outside / ".env")
                    (case_root / "releases").mkdir()
                else:
                    (case_root / "runtime").mkdir()
                    write_test_env(case_root / "runtime/.env")
                outside_before = {
                    path.name: path.read_bytes() for path in outside.iterdir() if path.is_file()
                }
                os.symlink(outside, case_root / child, target_is_directory=True)
                original_root, self.root = self.root, case_root
                try:
                    archive, checksum, _ = self.make_archive(f"20260802T150000Z-{child}")
                    result = self.deploy(archive, checksum)
                    self.assertNotEqual(0, result.returncode)
                    outside_after = {
                        path.name: path.read_bytes() for path in outside.iterdir() if path.is_file()
                    }
                    self.assertEqual(outside_before, outside_after)
                finally:
                    self.root = original_root

        sentinel = self.root / "outside-lock-sentinel"
        sentinel.write_text("must-not-change\n", encoding="ascii")
        os.symlink(sentinel, self.root / "runtime/deploy.lock")
        archive, checksum, _ = self.make_archive("20260802T151000Z-lock")
        result = self.deploy(archive, checksum)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("must-not-change\n", sentinel.read_text(encoding="ascii"))

    def test_source_replacement_after_snapshot_cannot_change_release(self):
        archive, checksum, name = self.make_archive("20260802T160000Z-snapshot")
        original_compose = repo_path("compose.yaml").read_bytes()
        sha_wrapper = self.root / "snapshot-sha256sum"
        real_sha = shutil.which("sha256sum")
        self.assertIsNotNone(real_sha)
        sha_wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf replaced >\"$STACK_MUTABLE_SOURCE\"\n"
            f"exec {shlex.quote(real_sha)} \"$@\"\n",
            encoding="utf-8",
        )
        sha_wrapper.chmod(0o755)
        result = self.deploy(
            archive, checksum,
            SHA256SUM_BIN=shell_path(sha_wrapper),
            STACK_MUTABLE_SOURCE=shell_path(archive),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"replaced", archive.read_bytes())
        self.assertEqual(original_compose, (self.root / "releases" / name / "compose.yaml").read_bytes())

    def test_identical_retry_resumes_retained_release_but_collision_is_rejected(self):
        archive, checksum, name = self.make_archive("20260802T170000Z-retry")
        first = self.deploy(archive, checksum, STACK_FAKE_FAIL_COMMAND="config")
        self.assertNotEqual(0, first.returncode)
        release = self.root / "releases" / name
        self.assertTrue((release / ".release-digest").is_file())
        self.assertFalse((release / ".successful").exists())

        tampered_marker = self.root / "tampered-script-executed"
        (release / "scripts/remote/compose.sh").write_text(
            "#!/usr/bin/env bash\n"
            f": >{shlex.quote(shell_path(tampered_marker))}\n"
            "exit 91\n",
            encoding="utf-8",
        )

        second = self.deploy(archive, checksum)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(f"releases/{name}", os.readlink(self.root / "current"))
        self.assertFalse(tampered_marker.exists())
        self.assertEqual(
            repo_path("scripts/remote/compose.sh").read_bytes(),
            (release / "scripts/remote/compose.sh").read_bytes(),
        )

        successful_content = (release / "compose.yaml").read_bytes()
        calls_before_successful_retry = self.log.read_text(encoding="utf-8").count("\n")
        successful_retry = self.deploy(archive, checksum)
        self.assertNotEqual(0, successful_retry.returncode)
        self.assertIn("successful", successful_retry.stderr.lower())
        self.assertEqual(successful_content, (release / "compose.yaml").read_bytes())
        self.assertEqual(
            calls_before_successful_retry,
            self.log.read_text(encoding="utf-8").count("\n"),
        )

        collision_archive, collision_checksum, _ = self.make_archive(
            "20260802T170000Z-retry", (self.regular_member("different"),)
        )
        calls_before = self.log.read_text(encoding="utf-8").count("\n")
        collision = self.deploy(collision_archive, collision_checksum)
        self.assertNotEqual(0, collision.returncode)
        self.assertIn("collision", collision.stderr.lower())
        self.assertEqual(calls_before, self.log.read_text(encoding="utf-8").count("\n"))

    def test_failed_atomic_exchange_preserves_retained_inode_and_content(self):
        archive, checksum, name = self.make_archive("20260802T170500Z-exchange-fails")
        first = self.deploy(archive, checksum, STACK_FAKE_FAIL_COMMAND="config")
        self.assertNotEqual(0, first.returncode)
        release = self.root / "releases" / name
        retained_script = release / "scripts/remote/compose.sh"
        retained_script.write_text("retained-content\n", encoding="ascii")
        before = (release.stat().st_dev, release.stat().st_ino, retained_script.read_bytes())
        python_failure = self.root / "python-exchange-failure"
        python_failure.write_text("#!/usr/bin/env bash\nexit 88\n", encoding="ascii")
        python_failure.chmod(0o755)
        calls_before = self.log.read_bytes()

        retry = self.deploy(
            archive, checksum, PYTHON_BIN=shell_path(python_failure)
        )

        self.assertNotEqual(0, retry.returncode)
        after = (release.stat().st_dev, release.stat().st_ino, retained_script.read_bytes())
        self.assertEqual(before, after)
        self.assertEqual(calls_before, self.log.read_bytes())
        self.assertFalse((self.root / "current").exists())
        self.assert_no_private_staging()

    def test_successful_retry_never_makes_release_path_absent(self):
        archive, checksum, name = self.make_archive("20260802T170600Z-exchange-present")
        first = self.deploy(archive, checksum, STACK_FAKE_FAIL_COMMAND="config")
        self.assertNotEqual(0, first.returncode)
        release = self.root / "releases" / name
        (release / "retained-sentinel").write_text("old\n", encoding="ascii")
        real_move = shutil.which("mv")
        self.assertIsNotNone(real_move)
        delayed_move = self.root / "delayed-retained-mv"
        delayed_move.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"{shlex.quote(real_move)} \"$@\"\n"
            'destination=${@: -1}\n'
            'if [[ "$destination" == */retained-release ]]; then sleep 1; fi\n',
            encoding="ascii",
        )
        delayed_move.chmod(0o755)
        absence = self.root / "release-was-absent"
        stop = self.root / "stop-watcher"
        watcher = subprocess.Popen([
            BASH_BIN, "-c",
            'while [[ ! -e "$1" ]]; do [[ -e "$2" ]] || : >"$3"; done',
            "watch-release", shell_path(stop), shell_path(release), shell_path(absence),
        ])
        try:
            retry = self.deploy(archive, checksum, MV_BIN=shell_path(delayed_move))
        finally:
            stop.touch()
            watcher.wait(timeout=5)

        self.assertEqual(0, retry.returncode, retry.stderr)
        self.assertFalse(absence.exists())
        self.assertFalse((release / "retained-sentinel").exists())
        self.assert_no_private_staging()

    def test_digest_marker_is_installed_atomically_with_fresh_release(self):
        archive, checksum, name = self.make_archive("20260802T171000Z-atomic-marker")
        real_move = shutil.which("mv")
        self.assertIsNotNone(real_move)
        wrapper = self.root / "interrupting-mv"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"{shlex.quote(real_move)} \"$@\"\n"
            "for argument in \"$@\"; do\n"
            "  if [[ \"$argument\" == */extracted ]]; then exit 86; fi\n"
            "done\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        result = self.deploy(archive, checksum, MV_BIN=shell_path(wrapper))
        self.assertNotEqual(0, result.returncode)
        release = self.root / "releases" / name
        self.assertTrue(release.is_dir())
        expected_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(
            f"{expected_digest}\n",
            (release / ".release-digest").read_text(encoding="ascii"),
        )
        self.assertFalse((release / ".successful").exists())
        self.assertFalse((self.root / "current").exists())
        self.assert_no_private_staging()

    def test_installed_release_swap_never_executes_replacement_content(self):
        archive, checksum, name = self.make_archive("20260802T172000Z-held-release")
        release = self.root / "releases" / name
        swap_marker = self.root / "release-swapped"
        tamper_marker = self.root / "replacement-executed"
        content_log = self.root / "held-content.log"
        result = self.deploy(
            archive, checksum,
            STACK_FAKE_SWAP_RELEASE=shell_path(release),
            STACK_FAKE_SWAP_MARKER=shell_path(swap_marker),
            STACK_FAKE_TAMPER_MARKER=shell_path(tamper_marker),
            STACK_FAKE_CONTENT_LOG=shell_path(content_log),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertTrue(swap_marker.exists())
        self.assertFalse(tamper_marker.exists())
        self.assertFalse((self.root / "current").exists())
        first_call = shlex.split(self.log.read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("--file", first_call)
        compose_file = first_call[first_call.index("--file") + 1]
        self.assertRegex(compose_file, r"^/proc/self/fd/[0-9]+$")
        expected_hashes = {
            relative: hashlib.sha256(repo_path(relative).read_bytes()).hexdigest()
            for relative in (
                "compose.yaml", "versions.env",
                "config/opensearch/opensearch.yml",
                "config/opensearch/docker-entrypoint.sh",
            )
        }
        actual_hashes = dict(
            line.split(" ", 1)
            for line in content_log.read_text(encoding="ascii").splitlines()
        )
        self.assertEqual(expected_hashes, actual_hashes)

    def test_pruning_keeps_older_named_active_and_ignores_unexpected_symlink(self):
        self.set_current("20260802T200000Z-previous")
        for name in (
            "20260802T210000Z-one", "20260802T220000Z-two", "20260802T230000Z-three"
        ):
            release = self.root / "releases" / name
            release.mkdir()
            self.mark_success(release)
        outside = self.root / "outside-release"
        outside.mkdir()
        (outside / ".successful").touch()
        os.symlink(outside, self.root / "releases/unexpected-link", target_is_directory=True)

        archive, checksum, active = self.make_archive("20260802T190000Z-active")
        result = self.deploy(archive, checksum)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(f"releases/{active}", os.readlink(self.root / "current"))
        kept = {
            path.name for path in (self.root / "releases").iterdir()
            if path.is_dir() and not path.is_symlink() and (path / ".successful").is_file()
        }
        self.assertEqual({active, "20260802T220000Z-two", "20260802T230000Z-three"}, kept)
        self.assertTrue((self.root / "releases/unexpected-link").is_symlink())
        self.assertTrue((outside / ".successful").exists())

    def test_deployment_lock_rejects_a_concurrent_receiver(self):
        archive, checksum, _ = self.make_archive()
        original = (self.root / "runtime/.env").read_bytes()
        first_env = self.make_incoming_env(original + b"PAIR_MARKER=first\n")
        second_env = self.make_incoming_env(original + b"PAIR_MARKER=second\n")
        first_expected = first_env.read_bytes()
        first = subprocess.Popen([
            BASH_BIN, shell_path(repo_path("scripts/remote/deploy-release.sh")),
            "--root", shell_path(self.root), "--archive", shell_path(archive),
            "--checksum", shell_path(checksum), "--env", shell_path(first_env),
            "--profiles", "core",
        ], env={**self.env, "STACK_FAKE_HOLD_SECONDS": "3"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.5)
            second = self.deploy(archive, checksum, runtime_env=second_env)
            self.assertNotEqual(0, second.returncode)
            self.assertIn("deployment", second.stderr.lower())
        finally:
            first_stdout, first_stderr = first.communicate(timeout=10)
        self.assertEqual(0, first.returncode, first_stderr or first_stdout)
        self.assertEqual(first_expected, (self.root / "runtime/.env").read_bytes())
        self.assertFalse(first_env.exists())
        self.assertTrue(second_env.exists())


if __name__ == "__main__":
    unittest.main()
