import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import repo_path


class BootstrapTests(unittest.TestCase):
    cuda_image = (
        "docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:"
        "5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df"
    )
    docker_packages = (
        "docker-ce",
        "docker-ce-cli",
        "containerd.io",
        "docker-buildx-plugin",
        "docker-compose-plugin",
    )

    @classmethod
    def setUpClass(cls):
        cls.shell = shutil.which("bash")
        if not cls.shell:
            raise unittest.SkipTest("bash is not installed")
        if subprocess.run([cls.shell, "--version"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("bash is not available")

    def create_repository(self, root: Path, codename: str):
        suite = root / "dists" / codename
        suite.mkdir(parents=True, exist_ok=True)
        (suite / "Release").write_text("Origin: Docker\n", encoding="utf-8")
        packages = suite / "stable" / "binary-amd64" / "Packages"
        packages.parent.mkdir(parents=True)
        packages.write_text(
            "\n".join(f"Package: {package}\nVersion: 1" for package in self.docker_packages),
            encoding="utf-8",
        )

    def create_host_shims(self, directory: Path, gpu_names: str | None = None):
        shim = directory / "bootstrap-host-shim"
        shim.write_text(
            """#!/bin/bash
set -u
name=${0##*/}
record() { printf '%s %s' "$1" "$name" >>"$STACK_BOOTSTRAP_TEST_LOG"; shift; printf ' %q' "$@" >>"$STACK_BOOTSTRAP_TEST_LOG"; printf '\n' >>"$STACK_BOOTSTRAP_TEST_LOG"; }
case "$name" in
  uname)
    record READ "$@"
    printf '%s\n' "${STACK_FAKE_UNAME:-x86_64}"
    ;;
  dpkg)
    record READ "$@"
    printf '%s\n' "${STACK_FAKE_DPKG_ARCH:-amd64}"
    ;;
  systemctl)
    if [[ "${1:-}" == show-environment ]]; then
      record READ "$@"
      [[ "${STACK_FAKE_SYSTEMD:-operational}" == operational ]]
    elif [[ "${1:-}" == is-active ]]; then
      record READ "$@"
      [[ "${STACK_FAKE_DOCKER_ACTIVE:-1}" == 1 ]]
    else
      record MUTATE "$@"
    fi
    ;;
  sudo)
    if [[ "${1:-}" == -n && "${2:-}" == true ]]; then
      record READ "$@"
    elif [[ "${1:-}" == test ]]; then
      shift
      "$STACK_FAKE_COMMAND_DIR/test" "$@"
    else
      "$@"
    fi
    ;;
  curl)
    record READ "$@"
    url=
    output_file=
    previous=
    for argument in "$@"; do
      case "$argument" in
        *://*) url=$argument ;;
      esac
      [[ "$previous" == -o ]] && output_file=$argument
      previous=$argument
    done
    case "$url" in
      https://nvidia.github.io/libnvidia-container/gpgkey)
        content='FAKE NVIDIA ARMORED KEY'
        ;;
      https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list)
        content='deb https://nvidia.github.io/libnvidia-container/stable/deb/$(ARCH) /'
        ;;
      file://*/gpg)
        content='FAKE DOCKER ARMORED KEY'
        ;;
      *)
        relative=${url#*/dists/}
        source_file="$STACK_FAKE_REPO_ROOT/dists/$relative"
        [[ -r "$source_file" ]] || exit 22
        content=$(<"$source_file")
        ;;
    esac
    [[ -n "$output_file" ]] || printf '%s\n' "$content"
    ;;
  grep)
    record READ "$@"
    needle=
    for argument in "$@"; do needle=$argument; done
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" == "$needle" ]] && exit 0
    done
    exit 1
    ;;
  cat)
    record READ "$@"
    while IFS= read -r line || [[ -n "$line" ]]; do printf '%s\n' "$line"; done
    ;;
  dpkg-query|getent)
    record READ "$@"
    exit 1
    ;;
  id)
    record READ "$@"
    if [[ "${1:-}" == -un ]]; then printf '%s\n' test-user; else printf '%s\n' 'test-user docker'; fi
    ;;
  nvidia-smi)
    record READ "$@"
    [[ "$*" == "--query-gpu=name --format=csv,noheader" ]] || exit 96
    [[ -z "${STACK_FAKE_GPU_NAMES:-}" ]] || printf '%s\n' "$STACK_FAKE_GPU_NAMES"
    exit "${STACK_FAKE_NVIDIA_SMI_STATUS:-0}"
    ;;
  docker)
    if [[ "${1:-}" == run ]]; then
      record MUTATE "$@"
      [[ "${STACK_FAKE_DOCKER_RUN_STATUS:-0}" == 0 ]] || exit "$STACK_FAKE_DOCKER_RUN_STATUS"
      [[ -z "${STACK_FAKE_CONTAINER_GPU_NAMES:-}" ]] ||
        printf '%s\n' "$STACK_FAKE_CONTAINER_GPU_NAMES"
    else
      record READ "$@"
    fi
    ;;
  gpg)
    record READ "$@"
    [[ "${STACK_FAKE_GPG_STATUS:-0}" == 0 ]] || exit "$STACK_FAKE_GPG_STATUS"
    [[ "${STACK_FAKE_GPG_EMPTY:-0}" == 0 ]] || exit 0
    while IFS= read -r line || [[ -n "$line" ]]; do printf 'DEARMORED:%s\n' "$line"; done
    ;;
  nvidia-ctk)
    record MUTATE "$@"
    [[ "$*" == "runtime configure --runtime=docker" ]] || exit 96
    printf '%s\n' '{"runtimes":{"nvidia":{}}}' >"$STACK_FAKE_DAEMON_JSON"
    ;;
  sha256sum)
    record READ "$@"
    [[ -r "${STACK_FAKE_DAEMON_JSON:-}" ]] || exit 1
    /usr/bin/sha256sum "$STACK_FAKE_DAEMON_JSON"
    ;;
  test)
    record READ "$@"
    [[ "$*" == "-e /etc/docker/daemon.json" ]] || exit 96
    [[ -e "${STACK_FAKE_DAEMON_JSON:-}" ]]
    ;;
  sysctl)
    if [[ "${1:-}" == -n ]]; then
      record READ "$@"
      case "${2:-}" in
        vm.max_map_count) printf '262144\n' ;;
        net.ipv4.ip_forward) printf '1\n' ;;
        *) exit 1 ;;
      esac
    else
      record MUTATE "$@"
    fi
    ;;
  tee)
    record MUTATE "$@"
    while IFS= read -r line || [[ -n "$line" ]]; do :; done
    ;;
  tr)
    record READ "$@"
    /usr/bin/tr "$@"
    ;;
  apt-get|chmod|groupadd|install|usermod)
    record MUTATE "$@"
    ;;
  *)
    record UNEXPECTED "$@"
    exit 98
    ;;
esac
""",
            encoding="utf-8",
        )
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
        for name in (
            "uname",
            "dpkg",
            "systemctl",
            "apt-get",
            "sudo",
            "curl",
            "grep",
            "cat",
            "dpkg-query",
            "getent",
            "id",
            "chmod",
            "docker",
            "gpg",
            "groupadd",
            "install",
            "nvidia-ctk",
            "sha256sum",
            "sysctl",
            "tee",
            "test",
            "tr",
            "usermod",
        ):
            shutil.copyfile(shim, directory / name)
            (directory / name).chmod(shim.stat().st_mode)
        if gpu_names is not None:
            shutil.copyfile(shim, directory / "nvidia-smi")
            (directory / "nvidia-smi").chmod(shim.stat().st_mode)

    def run_bootstrap(
        self,
        fixture: str,
        repo_root: Path,
        mode: str = "--check",
        arguments: tuple[str, ...] = (),
        gpu_names: str | None = None,
        extra_env: dict[str, str] | None = None,
        script_path: Path | None = None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            shim_directory = Path(directory)
            log = shim_directory / "invocations.log"
            log.write_text("", encoding="utf-8")
            self.create_host_shims(shim_directory, gpu_names)
            env = os.environ.copy()
            env.update({
                "PATH": f"{shim_directory.as_posix()}{os.pathsep}{env['PATH']}",
                "STACK_OS_RELEASE_FILE": repo_path(
                    f"tests/fixtures/os-release/{fixture}"
                ).as_posix(),
                "STACK_DOCKER_REPO_BASE": repo_root.as_uri(),
                "STACK_BOOTSTRAP_DRY_RUN": "1",
                "STACK_BOOTSTRAP_TEST_LOG": log.as_posix(),
                "STACK_FAKE_REPO_ROOT": repo_root.as_posix(),
                "STACK_FAKE_GPU_NAMES": gpu_names or "",
                "STACK_FAKE_NVIDIA_SMI_STATUS": "0",
                "STACK_FAKE_CONTAINER_GPU_NAMES": "NVIDIA T4",
                "STACK_FAKE_DOCKER_ACTIVE": "1",
                "STACK_FAKE_GPG_STATUS": "0",
                "STACK_FAKE_GPG_EMPTY": "0",
                "STACK_FAKE_COMMAND_DIR": shim_directory.as_posix(),
            })
            env.update(extra_env or {})
            result = subprocess.run(
                [
                    self.shell,
                    str(script_path or repo_path("scripts/remote/bootstrap-host.sh")),
                    mode,
                    *arguments,
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            return result, log.read_text(encoding="utf-8").splitlines()

    def run_check(self, fixture: str, repo_root: Path, extra_env=None):
        return self.run_bootstrap(fixture, repo_root, extra_env=extra_env)

    def assert_sysctl_install_plan(self, output: str):
        expected_fragments = (
            "write /etc/sysctl.d/99-remote-infra-stack.conf\n"
            "vm.max_map_count=262144\n"
            "net.ipv4.ip_forward=1\n",
            "sysctl --system",
            "verify sysctl vm.max_map_count equals 262144",
            "sysctl -n vm.max_map_count",
            "verify sysctl net.ipv4.ip_forward equals 1",
            "sysctl -n net.ipv4.ip_forward",
        )
        position = -1
        for fragment in expected_fragments:
            next_position = output.find(fragment, position + 1)
            self.assertGreater(next_position, position, fragment)
            position = next_position

    def assert_real_install_sysctl_verification(self, source: str):
        real_install_marker = "  exit 0\nfi\n\nrun_root docker version\n"
        self.assertEqual(1, source.count(real_install_marker))
        real_install = source.split(real_install_marker, 1)[1]
        self.assertIn(
            "run_root systemctl is-active --quiet docker || "
            'die "Docker service is not active"\n'
            "verify_sysctl_setting vm.max_map_count 262144\n"
            "verify_sysctl_setting net.ipv4.ip_forward 1\n"
            "if (( gpu_mode == 1 )); then",
            real_install,
        )
        self.assertIn("fi\nprintf 'Docker bootstrap complete", real_install)

    def test_supported_and_future_ubuntu_are_repository_gated(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for codename in ("jammy", "noble", "resolute", "future"):
                self.create_repository(repo, codename)
            for fixture in (
                "ubuntu-22.04",
                "ubuntu-24.04",
                "ubuntu-26.04",
                "ubuntu-future-lts",
            ):
                with self.subTest(fixture=fixture):
                    result, _ = self.run_check(fixture, repo)
                    self.assertEqual(0, result.returncode, result.stderr)

    def test_non_ubuntu_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.run_check("debian", Path(directory))
            self.assertNotEqual(0, result.returncode)
            self.assertIn("ID=ubuntu", result.stderr)

    def test_unsupported_architecture_is_rejected_from_shimmed_host_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.run_check(
                "ubuntu-26.04", Path(directory), {"STACK_FAKE_UNAME": "aarch64"}
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("require Ubuntu amd64", result.stderr)

    def test_gpu_requires_cuda_image_before_host_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            result, invocations = self.run_bootstrap(
                "ubuntu-26.04", Path(directory), "--install", arguments=("--gpu",)
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("--gpu requires --cuda-image", result.stderr)
        self.assertEqual([], invocations)
        self.assertNotIn("apt-get", result.stdout)

    def test_cuda_image_requires_gpu_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            result, invocations = self.run_bootstrap(
                "ubuntu-26.04",
                Path(directory),
                arguments=("--cuda-image", self.cuda_image),
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("--cuda-image requires --gpu", result.stderr)
        self.assertEqual([], invocations)

    def test_cuda_image_must_be_a_single_non_latest_sha256_pinned_value(self):
        invalid_images = {
            "latest tag": "docker.io/nvidia/cuda:latest@sha256:" + "a" * 64,
            "tag containing latest": (
                "docker.io/nvidia/cuda:latest-candidate@sha256:" + "a" * 64
            ),
            "missing digest": "docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04",
            "space": self.cuda_image + " extra",
            "newline": self.cuda_image + "\nextra",
            "short digest": "docker.io/nvidia/cuda:12.9.1@sha256:abc",
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, image in invalid_images.items():
                with self.subTest(name=name):
                    result, invocations = self.run_bootstrap(
                        "ubuntu-26.04",
                        Path(directory),
                        "--install",
                        arguments=("--gpu", "--cuda-image", image),
                        gpu_names="NVIDIA T4",
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("digest-pinned", result.stderr)
                    self.assertEqual([], invocations)
                    self.assertNotIn("apt-get", result.stdout)

    def test_gpu_check_accepts_an_untagged_digest_pinned_cuda_image(self):
        untagged_image = "docker.io/nvidia/cuda@sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.create_repository(repo, "resolute")

            result, invocations = self.run_bootstrap(
                "ubuntu-26.04",
                repo,
                arguments=("--gpu", "--cuda-image", untagged_image),
                gpu_names="NVIDIA T4",
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse([line for line in invocations if line.startswith("MUTATE")])

    def test_cuda_image_rejects_leading_dash_option_injection_before_host_checks(self):
        injected_image = "-v/tmp:/host@sha256:" + "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            result, invocations = self.run_bootstrap(
                "ubuntu-26.04",
                Path(directory),
                "--install",
                arguments=("--gpu", "--cuda-image", injected_image),
                gpu_names="NVIDIA T4",
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("must not begin with '-'", result.stderr)
        self.assertEqual([], invocations)
        self.assertNotIn("apt-get", result.stdout)

    def test_gpu_options_reject_duplicates_missing_values_and_unknowns(self):
        invalid_arguments = (
            ("--gpu", "--gpu", "--cuda-image", self.cuda_image),
            (
                "--gpu",
                "--cuda-image",
                self.cuda_image,
                "--cuda-image",
                self.cuda_image,
            ),
            ("--gpu", "--cuda-image"),
            ("--gpu", "--cuda-image", "--gpu"),
            ("--gpu", "--cuda-image", self.cuda_image, "--unexpected"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for arguments in invalid_arguments:
                with self.subTest(arguments=arguments):
                    result, invocations = self.run_bootstrap(
                        "ubuntu-26.04", Path(directory), arguments=arguments
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("usage:", result.stderr)
                    self.assertEqual([], invocations)

    def test_gpu_requires_deep_learning_vm_driver_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.create_repository(repo, "resolute")

            result, invocations = self.run_bootstrap(
                "ubuntu-26.04",
                repo,
                arguments=("--gpu", "--cuda-image", self.cuda_image),
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "GPU mode requires the NVIDIA driver and nvidia-smi from the Deep Learning VM image",
            result.stderr,
        )
        self.assertNotIn("apt-get", result.stdout)
        self.assertFalse([line for line in invocations if line.startswith("MUTATE")])

    def test_gpu_rejects_a_failing_driver_query_even_when_it_prints_t4(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.create_repository(repo, "resolute")

            result, invocations = self.run_bootstrap(
                "ubuntu-26.04",
                repo,
                "--install",
                arguments=("--gpu", "--cuda-image", self.cuda_image),
                gpu_names="NVIDIA T4",
                extra_env={"STACK_FAKE_NVIDIA_SMI_STATUS": "19"},
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("working NVIDIA driver", result.stderr)
        self.assertFalse([line for line in invocations if line.startswith("MUTATE")])
        self.assertNotIn("apt-get", result.stdout)

    def test_gpu_inventory_must_be_exactly_one_t4_before_apt_changes(self):
        invalid_inventories = ("", "NVIDIA T4\nNVIDIA T4", "NVIDIA L4")
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.create_repository(repo, "resolute")
            for gpu_names in invalid_inventories:
                with self.subTest(gpu_names=gpu_names):
                    result, invocations = self.run_bootstrap(
                        "ubuntu-26.04",
                        repo,
                        "--install",
                        arguments=("--gpu", "--cuda-image", self.cuda_image),
                        gpu_names=gpu_names,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("GPU mode requires exactly one NVIDIA T4", result.stderr)
                    self.assertNotIn("apt-get", result.stdout)
                    self.assertFalse(
                        [line for line in invocations if line.startswith("MUTATE")]
                    )

    def test_cpu_modes_never_use_nvidia_tools_or_repository_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.create_repository(repo, "resolute")
            for mode in ("--check", "--install"):
                with self.subTest(mode=mode):
                    result, invocations = self.run_bootstrap(
                        "ubuntu-26.04", repo, mode, gpu_names="NVIDIA T4"
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    evidence = "\n".join((result.stdout, result.stderr, *invocations)).lower()
                    self.assertNotIn("nvidia", evidence)

    def test_gpu_install_dry_run_prints_complete_plan_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.create_repository(repo, "resolute")

            result, invocations = self.run_bootstrap(
                "ubuntu-26.04",
                repo,
                "--install",
                arguments=("--gpu", "--cuda-image", self.cuda_image),
                gpu_names="NVIDIA T4",
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(
            [line for line in invocations if line.startswith(("MUTATE", "UNEXPECTED"))],
            invocations,
        )
        expected_plan = (
            "https://nvidia.github.io/libnvidia-container/gpgkey",
            "/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
            "chmod a+r /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
            "https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list",
            "write /etc/apt/sources.list.d/nvidia-container-toolkit.list",
            "deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/$(ARCH) /",
            "apt-get install --yes --no-install-recommends nvidia-container-toolkit",
            "nvidia-ctk runtime configure --runtime=docker",
            "systemctl restart docker",
            f"docker run --rm --gpus all {self.cuda_image} nvidia-smi --query-gpu=name --format=csv\\,noheader",
        )
        position = -1
        for fragment in expected_plan:
            next_position = result.stdout.find(fragment, position + 1)
            self.assertGreater(next_position, position, fragment)
            position = next_position

    def test_gpu_runtime_configuration_is_byte_idempotent_and_restarts_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repository"
            daemon_json = root / "daemon.json"
            self.create_repository(repo, "resolute")
            environment = {
                "STACK_BOOTSTRAP_DRY_RUN": "0",
                "STACK_FAKE_DAEMON_JSON": daemon_json.as_posix(),
            }

            first, first_invocations = self.run_bootstrap(
                "ubuntu-26.04",
                repo,
                "--install",
                arguments=("--gpu", "--cuda-image", self.cuda_image),
                gpu_names="NVIDIA T4",
                extra_env=environment,
            )
            first_bytes = daemon_json.read_bytes() if daemon_json.exists() else b""
            second, second_invocations = self.run_bootstrap(
                "ubuntu-26.04",
                repo,
                "--install",
                arguments=("--gpu", "--cuda-image", self.cuda_image),
                gpu_names="NVIDIA T4",
                extra_env=environment,
            )
            second_bytes = daemon_json.read_bytes() if daemon_json.exists() else b""

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(b'{"runtimes":{"nvidia":{}}}\n', first_bytes)
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(
            ["MUTATE systemctl restart docker"],
            [line for line in first_invocations if "systemctl restart docker" in line],
        )
        self.assertEqual(
            [],
            [line for line in second_invocations if "systemctl restart docker" in line],
        )
        for invocations in (first_invocations, second_invocations):
            self.assertIn(
                "MUTATE apt-get install --yes --no-install-recommends nvidia-container-toolkit",
                invocations,
            )
            self.assertIn(
                "MUTATE nvidia-ctk runtime configure --runtime=docker", invocations
            )
            self.assertIn(
                f"MUTATE docker run --rm --gpus all {self.cuda_image} nvidia-smi --query-gpu=name --format=csv\\,noheader",
                invocations,
            )

    def test_gpu_install_writes_official_keyring_and_signed_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repository"
            self.create_repository(repo, "resolute")
            result, invocations = self.run_bootstrap(
                "ubuntu-26.04",
                repo,
                "--install",
                arguments=("--gpu", "--cuda-image", self.cuda_image),
                gpu_names="NVIDIA T4",
                extra_env={
                    "STACK_BOOTSTRAP_DRY_RUN": "0",
                    "STACK_FAKE_DAEMON_JSON": (root / "daemon.json").as_posix(),
                },
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "MUTATE tee /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
            invocations,
        )
        self.assertIn(
            "MUTATE chmod a+r /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg",
            invocations,
        )
        self.assertIn(
            "MUTATE tee /etc/apt/sources.list.d/nvidia-container-toolkit.list",
            invocations,
        )

    def test_invalid_nvidia_signing_key_fails_before_host_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repository"
            self.create_repository(repo, "resolute")

            result, invocations = self.run_bootstrap(
                "ubuntu-26.04",
                repo,
                "--install",
                arguments=("--gpu", "--cuda-image", self.cuda_image),
                gpu_names="NVIDIA T4",
                extra_env={
                    "STACK_BOOTSTRAP_DRY_RUN": "0",
                    "STACK_FAKE_DAEMON_JSON": (root / "daemon.json").as_posix(),
                    "STACK_FAKE_GPG_STATUS": "23",
                },
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("signing key", result.stderr)
        self.assertIn("READ gpg --dearmor", invocations)
        self.assertFalse([line for line in invocations if line.startswith("MUTATE")])

    def test_empty_dearmored_nvidia_key_fails_before_host_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repository"
            self.create_repository(repo, "resolute")

            result, invocations = self.run_bootstrap(
                "ubuntu-26.04",
                repo,
                "--install",
                arguments=("--gpu", "--cuda-image", self.cuda_image),
                gpu_names="NVIDIA T4",
                extra_env={
                    "STACK_BOOTSTRAP_DRY_RUN": "0",
                    "STACK_FAKE_DAEMON_JSON": (root / "daemon.json").as_posix(),
                    "STACK_FAKE_GPG_EMPTY": "1",
                },
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("empty keyring", result.stderr)
        self.assertFalse([line for line in invocations if line.startswith("MUTATE")])

    def test_wrong_container_gpu_inventory_is_a_hard_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repository"
            self.create_repository(repo, "resolute")
            for container_gpu_names in (
                "",
                "NVIDIA T4\n",
                "NVIDIA T4\nNVIDIA T4",
                "NVIDIA L4",
            ):
                with self.subTest(container_gpu_names=container_gpu_names):
                    result, invocations = self.run_bootstrap(
                        "ubuntu-26.04",
                        repo,
                        "--install",
                        arguments=("--gpu", "--cuda-image", self.cuda_image),
                        gpu_names="NVIDIA T4",
                        extra_env={
                            "STACK_BOOTSTRAP_DRY_RUN": "0",
                            "STACK_FAKE_DAEMON_JSON": (
                                root / f"daemon-{len(container_gpu_names)}.json"
                            ).as_posix(),
                            "STACK_FAKE_CONTAINER_GPU_NAMES": container_gpu_names,
                        },
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(
                        "GPU container validation requires exactly one NVIDIA T4",
                        result.stderr,
                    )
                    self.assertTrue(
                        [line for line in invocations if "docker run --rm --gpus all" in line]
                    )

    def test_gpu_install_fails_when_docker_service_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repository"
            self.create_repository(repo, "resolute")
            result, invocations = self.run_bootstrap(
                "ubuntu-26.04",
                repo,
                "--install",
                arguments=("--gpu", "--cuda-image", self.cuda_image),
                gpu_names="NVIDIA T4",
                extra_env={
                    "STACK_BOOTSTRAP_DRY_RUN": "0",
                    "STACK_FAKE_DAEMON_JSON": (root / "daemon.json").as_posix(),
                    "STACK_FAKE_DOCKER_ACTIVE": "0",
                },
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Docker service is not active", result.stderr)
        self.assertFalse(
            [line for line in invocations if "docker run --rm --gpus all" in line]
        )

    def test_inoperative_systemd_is_rejected_from_shimmed_host_fact(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.run_check(
                "ubuntu-26.04", Path(directory), {"STACK_FAKE_SYSTEMD": "down"}
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("systemd is not operational", result.stderr)

    def test_missing_repository_suite_reports_release_and_remediation(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.run_check("ubuntu-26.04", Path(directory))
            self.assertNotEqual(0, result.returncode)
            self.assertIn("VERSION_ID=26.04", result.stderr)
            self.assertIn("codename=resolute", result.stderr)
            self.assertIn("wait for Docker support", result.stderr)

    def test_repository_requires_every_docker_package_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.create_repository(repo, "resolute")
            packages = repo / "dists" / "resolute" / "stable" / "binary-amd64" / "Packages"
            packages.write_text(
                packages.read_text(encoding="utf-8").replace(
                    "Package: docker-compose-plugin\nVersion: 1", ""
                ),
                encoding="utf-8",
            )

            result, _ = self.run_check("ubuntu-26.04", repo)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("docker-compose-plugin", result.stderr)
            self.assertIn("before modifying the host", result.stderr)

    def test_install_dry_run_executes_no_mutation_and_prints_complete_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.create_repository(repo, "resolute")

            result, invocations = self.run_bootstrap("ubuntu-26.04", repo, "--install")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(
                [line for line in invocations if line.startswith(("MUTATE", "UNEXPECTED"))],
                invocations,
            )
            expected_plan = (
                "apt-get update",
                "apt-get install --yes ca-certificates curl gnupg tar gzip openssl util-linux coreutils jq python3 procps",
                "install -m 0755 -d /etc/apt/keyrings",
                "curl --fail --silent --show-error --location",
                "chmod a+r /etc/apt/keyrings/docker.asc",
                "write /etc/apt/sources.list.d/docker.sources",
                "Types: deb",
                "Suites: resolute",
                "Components: stable",
                "Architectures: amd64",
                "Signed-By: /etc/apt/keyrings/docker.asc",
                "apt-get update",
                "apt-get install --yes docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin",
                "systemctl enable --now docker",
                "groupadd docker",
                "usermod -aG docker test-user",
                "verify Docker group membership for test-user",
                "write /etc/sysctl.d/99-remote-infra-stack.conf",
                "vm.max_map_count=262144",
                "net.ipv4.ip_forward=1",
                "sysctl --system",
                "docker version",
                "docker compose version",
                "systemctl is-active docker",
                "verify sysctl vm.max_map_count equals 262144",
                "sysctl -n vm.max_map_count",
                "verify sysctl net.ipv4.ip_forward equals 1",
                "sysctl -n net.ipv4.ip_forward",
            )
            position = -1
            for fragment in expected_plan:
                next_position = result.stdout.find(fragment, position + 1)
                self.assertGreater(next_position, position, fragment)
                position = next_position

            self.assert_sysctl_install_plan(result.stdout)

    def test_install_plan_rejects_missing_or_wrong_forwarding_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repository"
            self.create_repository(repo, "resolute")
            original = repo_path("scripts/remote/bootstrap-host.sh").read_text(
                encoding="utf-8"
            )
            mutations = {
                "missing forwarding key": original.replace(
                    "net.ipv4.ip_forward=1\n", ""
                ),
                "disabled forwarding value": original.replace(
                    "net.ipv4.ip_forward=1", "net.ipv4.ip_forward=0"
                ),
                "wrong forwarding verification value": original.replace(
                    "verify_sysctl_setting net.ipv4.ip_forward 1",
                    "verify_sysctl_setting net.ipv4.ip_forward 0",
                ),
            }

            for name, source in mutations.items():
                with self.subTest(mutation=name):
                    mutated = root / f"{name.replace(' ', '-')}.sh"
                    mutated.write_text(source, encoding="utf-8", newline="\n")
                    result, invocations = self.run_bootstrap(
                        "ubuntu-26.04", repo, "--install", script_path=mutated
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertFalse(
                        [
                            line
                            for line in invocations
                            if line.startswith(("MUTATE", "UNEXPECTED"))
                        ],
                        invocations,
                    )
                    with self.assertRaises(AssertionError):
                        self.assert_sysctl_install_plan(result.stdout)

    def test_real_install_verifier_rejects_wrong_forwarding_expectation(self):
        original = repo_path("scripts/remote/bootstrap-host.sh").read_text(
            encoding="utf-8"
        )
        verifier = "verify_sysctl_setting net.ipv4.ip_forward 1"
        prefix, separator, suffix = original.rpartition(verifier)
        self.assertEqual(verifier, separator)
        self.assertIn(
            verifier,
            prefix,
            "dry-run verifier must remain independently covered",
        )

        self.assert_real_install_sysctl_verification(original)
        wrong_real_verifier = prefix + verifier[:-1] + "0" + suffix
        with self.assertRaises(AssertionError):
            self.assert_real_install_sysctl_verification(wrong_real_verifier)


if __name__ == "__main__":
    unittest.main()
