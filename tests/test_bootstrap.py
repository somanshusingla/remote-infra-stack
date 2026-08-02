import os
import shutil
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
        capability = subprocess.run(
            [
                cls.shell,
                "-c",
                "command -v systemctl apt-get sudo curl dpkg >/dev/null && sudo -n true",
            ],
            capture_output=True,
        )
        if capability.returncode != 0:
            raise unittest.SkipTest("Ubuntu bootstrap host capabilities are unavailable")

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

    def run_bootstrap(self, fixture: str, repo_root: Path, mode: str = "--check"):
        env = os.environ.copy()
        env.update({
            "STACK_OS_RELEASE_FILE": str(
                repo_path(f"tests/fixtures/os-release/{fixture}")
            ),
            "STACK_DOCKER_REPO_BASE": repo_root.as_uri(),
            "STACK_BOOTSTRAP_DRY_RUN": "1",
        })
        return subprocess.run(
            [self.shell, str(repo_path("scripts/remote/bootstrap-host.sh")), mode],
            env=env,
            capture_output=True,
            text=True,
        )

    def run_check(self, fixture: str, repo_root: Path):
        return self.run_bootstrap(fixture, repo_root)

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
                    self.assertEqual(0, self.run_check(fixture, repo).returncode)

    def test_non_ubuntu_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_check("debian", Path(directory))
            self.assertNotEqual(0, result.returncode)
            self.assertIn("ID=ubuntu", result.stderr)

    def test_missing_repository_suite_reports_release_and_remediation(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_check("ubuntu-26.04", Path(directory))
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

            result = self.run_check("ubuntu-26.04", repo)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("docker-compose-plugin", result.stderr)
            self.assertIn("before modifying the host", result.stderr)

    def test_install_dry_run_describes_all_privileged_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.create_repository(repo, "resolute")

            result = self.run_bootstrap("ubuntu-26.04", repo, "--install")

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("/etc/apt/sources.list.d/docker.sources", result.stdout)
            self.assertIn("docker-ce docker-ce-cli containerd.io", result.stdout)
            self.assertIn("docker-buildx-plugin docker-compose-plugin", result.stdout)
            self.assertIn("systemctl enable --now docker", result.stdout)
            self.assertIn("usermod -aG docker", result.stdout)
            self.assertIn("verify Docker group membership", result.stdout)
            self.assertIn("/etc/sysctl.d/99-remote-infra-stack.conf", result.stdout)
            self.assertIn("vm.max_map_count=262144", result.stdout)


if __name__ == "__main__":
    unittest.main()
