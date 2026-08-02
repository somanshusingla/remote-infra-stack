import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import repo_path


class BootstrapTests(unittest.TestCase):
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

    def create_host_shims(self, directory: Path):
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
    else
      record MUTATE "$@"
      exit 97
    fi
    ;;
  sudo)
    if [[ "${1:-}" == -n && "${2:-}" == true ]]; then
      record READ "$@"
    else
      record MUTATE "$@"
      exit 97
    fi
    ;;
  curl)
    record READ "$@"
    url=
    for argument in "$@"; do
      case "$argument" in
        *://*) url=$argument ;;
      esac
    done
    relative=${url#*/dists/}
    source_file="$STACK_FAKE_REPO_ROOT/dists/$relative"
    [[ -r "$source_file" ]] || exit 22
    while IFS= read -r line || [[ -n "$line" ]]; do printf '%s\n' "$line"; done <"$source_file"
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
  apt-get|chmod|docker|groupadd|install|sysctl|tee|tr|usermod)
    record MUTATE "$@"
    exit 97
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
            "groupadd",
            "install",
            "sysctl",
            "tee",
            "tr",
            "usermod",
        ):
            shutil.copyfile(shim, directory / name)
            (directory / name).chmod(shim.stat().st_mode)

    def run_bootstrap(
        self,
        fixture: str,
        repo_root: Path,
        mode: str = "--check",
        extra_env: dict[str, str] | None = None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            shim_directory = Path(directory)
            log = shim_directory / "invocations.log"
            log.write_text("", encoding="utf-8")
            self.create_host_shims(shim_directory)
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
            })
            env.update(extra_env or {})
            result = subprocess.run(
                [self.shell, str(repo_path("scripts/remote/bootstrap-host.sh")), mode],
                env=env,
                capture_output=True,
                text=True,
            )
            return result, log.read_text(encoding="utf-8").splitlines()

    def run_check(self, fixture: str, repo_root: Path, extra_env=None):
        return self.run_bootstrap(fixture, repo_root, extra_env=extra_env)

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
                "apt-get install --yes ca-certificates curl gnupg tar gzip openssl util-linux coreutils jq python3",
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
                "sysctl --system",
                "docker version",
                "docker compose version",
                "systemctl is-active docker",
                "sysctl -n vm.max_map_count",
            )
            position = -1
            for fragment in expected_plan:
                next_position = result.stdout.find(fragment, position + 1)
                self.assertGreater(next_position, position, fragment)
                position = next_position


if __name__ == "__main__":
    unittest.main()
