import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import repo_path

BASH_BIN = os.environ.get("BASH_BIN", "bash")


def shell_path(path: Path) -> str:
    return path.as_posix() if os.name == "nt" else str(path)


def usable_bash() -> bool:
    try:
        return subprocess.run(
            [BASH_BIN, "--version"], capture_output=True, timeout=5
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def write_test_env(destination: Path) -> None:
    content = repo_path(".env.example").read_text(encoding="utf-8")
    destination.write_text(
        content.replace("GENERATED_BY_INIT_ENV", "remote-only-test-value"),
        encoding="utf-8",
    )


@unittest.skipUnless(usable_bash(), "requires a usable Bash")
class RemoteRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "runtime").mkdir()
        write_test_env(self.root / "runtime/.env")
        self.log = self.root / "docker.log"
        self.env = {
            **os.environ,
            "STACK_ROOT": shell_path(self.root),
            "STACK_RELEASE_DIR": shell_path(repo_path(".")),
            "DOCKER_BIN": shell_path(repo_path("tests/fakes/docker")),
            "CURL_BIN": shell_path(repo_path("tests/fakes/docker")),
            "STACK_DOCKER_LOG": shell_path(self.log),
        }

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, name, *args, **env):
        return subprocess.run(
            [BASH_BIN, shell_path(repo_path(f"scripts/remote/{name}")), *args],
            env={**self.env, **env}, capture_output=True, text=True,
        )

    def docker_calls(self):
        return [shlex.split(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_compose_builds_canonical_command_with_ordered_profiles(self):
        result = self.run_script("compose.sh", "core", "vector", "--", "config", "--quiet")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([
            "docker", "compose",
            "--env-file", shell_path(repo_path("versions.env")),
            "--env-file", shell_path(self.root / "runtime/.env"),
            "--project-directory", shell_path(repo_path(".")),
            "--file", shell_path(repo_path("compose.yaml")),
            "--profile", "core", "--profile", "vector", "config", "--quiet",
        ], self.docker_calls()[0])

    def test_invalid_profiles_are_rejected_before_docker(self):
        cases = [
            (("tools",), "tools requires core"),
            (("core", "core"), "duplicate profile"),
            (("not-a-profile",), "unknown profile"),
        ]
        for profiles, message in cases:
            with self.subTest(profiles=profiles):
                if self.log.exists():
                    self.log.unlink()
                result = self.run_script("stack.sh", "up", *profiles)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.log.exists())

    def test_down_never_adds_volume_flag(self):
        result = self.run_script("stack.sh", "down")
        self.assertEqual(0, result.returncode, result.stderr)
        call = self.docker_calls()[0]
        self.assertEqual(["--profile", "*", "down"], call[-3:])
        self.assertNotIn("-v", call)
        self.assertNotIn("--volumes", call)

    def test_destroy_requires_exact_target_token_and_is_only_volume_path(self):
        for args in (
            ("remote-infra-stack",),
            ("remote-infra-stack", "DESTROY-remote-infra-stacks"),
            ("prod", "DESTROY-prod"),
            ("typo", "DESTROY-typo"),
        ):
            with self.subTest(args=args):
                result = self.run_script("stack.sh", "destroy", *args)
                self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.log.exists())
        result = self.run_script(
            "stack.sh", "destroy", "remote-infra-stack", "DESTROY-remote-infra-stack"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["--profile", "*", "down", "-v"], self.docker_calls()[0][-4:])

    def test_stop_and_logs_expand_only_valid_profile_services(self):
        result = self.run_script("stack.sh", "stop", "search")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ["stop", "opensearch", "opensearch-dashboards"],
            self.docker_calls()[0][-3:],
        )
        self.log.unlink()
        result = self.run_script("stack.sh", "logs", "core")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ["logs", "-f", "app-postgres", "app-redis"],
            self.docker_calls()[0][-4:],
        )
        self.log.unlink()
        result = self.run_script("stack.sh", "logs", "made-up-service")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.log.exists())

    def test_health_accepts_compose_json_lines_and_checks_exact_core_boundaries(self):
        ps = "\n".join(json.dumps(item) for item in (
            {"Service": "app-postgres", "State": "running", "Health": "healthy"},
            {"Service": "app-redis", "State": "running", "Health": "healthy"},
        ))
        secret = "health-sentinel-value"
        env_file = self.root / "runtime/.env"
        env_file.write_text(
            env_file.read_text(encoding="utf-8").replace(
                "remote-only-test-value", secret, 1
            ), encoding="utf-8",
        )
        result = self.run_script("health.sh", "core", STACK_FAKE_PS_JSON=ps)
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.docker_calls()
        self.assertEqual(["app-postgres", "app-redis"], [
            call[call.index("-T") + 1] for call in calls if "exec" in call
        ])
        combined = result.stdout + result.stderr + self.log.read_text(encoding="utf-8")
        self.assertNotIn(secret, combined)

    def test_health_rejects_unhealthy_selected_container_before_endpoints(self):
        ps = json.dumps([
            {"Service": "chroma", "State": "running", "Health": "unhealthy"}
        ])
        result = self.run_script("health.sh", "vector", STACK_FAKE_PS_JSON=ps)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("chroma", result.stderr)
        self.assertEqual(1, len(self.docker_calls()))

    def test_health_requires_every_replica_healthy_and_every_service_present(self):
        healthy_replicas = json.dumps([
            {"Service": "chroma", "State": "running", "Health": "healthy"},
            {"Service": "chroma", "State": "running", "Health": "healthy"},
        ])
        result = self.run_script("health.sh", "vector", STACK_FAKE_PS_JSON=healthy_replicas)
        self.assertEqual(0, result.returncode, result.stderr)

        cases = {
            "one-starting-replica": [
                {"Service": "chroma", "State": "running", "Health": "healthy"},
                {"Service": "chroma", "State": "running", "Health": "starting"},
            ],
            "one-unhealthy-replica": [
                {"Service": "chroma", "State": "running", "Health": "healthy"},
                {"Service": "chroma", "State": "running", "Health": "unhealthy"},
            ],
            "missing-selected-service": [
                {"Service": "other", "State": "running", "Health": "healthy"},
            ],
        }
        for label, records in cases.items():
            with self.subTest(case=label):
                self.log.unlink()
                result = self.run_script(
                    "health.sh", "vector", STACK_FAKE_PS_JSON=json.dumps(records)
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("chroma", result.stderr)
                self.assertEqual(1, len(self.docker_calls()))

    def test_health_includes_exited_replicas_hidden_by_default_ps(self):
        visible = json.dumps([
            {"Service": "chroma", "State": "running", "Health": "healthy"},
        ])
        all_containers = json.dumps([
            {"Service": "chroma", "State": "running", "Health": "healthy"},
            {"Service": "chroma", "State": "exited", "Health": ""},
        ])
        result = self.run_script(
            "health.sh", "vector", STACK_FAKE_PS_JSON=visible,
            STACK_FAKE_PS_ALL_JSON=all_containers,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("chroma", result.stderr)
        self.assertEqual(1, len(self.docker_calls()))

    def test_health_maps_every_profile_to_its_exact_services_and_endpoints(self):
        result = self.run_script(
            "health.sh", "core", "vector", "search", "observability", "tools"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.docker_calls()
        self.assertEqual([
            "app-postgres", "app-redis", "langfuse-postgres", "langfuse-redis", "clickhouse",
        ], [call[call.index("-T") + 1] for call in calls if "exec" in call])
        rendered = "\n".join(" ".join(call) for call in calls)
        for endpoint in (
            "http://127.0.0.1:18000/api/v2/heartbeat",
            "https://127.0.0.1:9200/_cluster/health",
            "http://127.0.0.1:5601/api/status",
            "http://127.0.0.1:9090/minio/health/ready",
            "http://127.0.0.1:3000/api/public/ready",
            "http://127.0.0.1:5050/misc/ping",
            "http://127.0.0.1:5540/api/health/",
        ):
            self.assertIn(endpoint, rendered)

    def test_status_reports_compose_and_docker_usage(self):
        result = self.run_script("stack.sh", "status")
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.docker_calls()
        self.assertIn("ps", calls[0])
        self.assertEqual(["docker", "system", "df"], calls[-1])

    def test_up_fails_below_ten_gib_disk_and_only_warns_for_memory(self):
        fake_df = self.root / "df"
        fake_df.write_text("#!/usr/bin/env bash\nprintf 'Avail\\n%u\\n' \"${STACK_FAKE_DISK_BYTES}\"\n", encoding="utf-8")
        fake_df.chmod(0o755)
        result = self.run_script(
            "stack.sh", "up", "core", DF_BIN=shell_path(fake_df),
            STACK_FAKE_DISK_BYTES=str(9 * 1024**3),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("10 GiB", result.stderr)
        self.assertFalse(self.log.exists())
        tiny_meminfo = self.root / "tiny-meminfo"
        tiny_meminfo.write_text("MemTotal: 1024 kB\n", encoding="ascii")
        result = self.run_script(
            "stack.sh", "up", "core", MEMINFO_FILE=shell_path(tiny_meminfo),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("memory", result.stderr.lower())
        self.assertIn("up", self.docker_calls()[-1])

    def test_up_warns_between_ten_and_twenty_gib_but_still_runs(self):
        fake_df = self.root / "df-warning"
        fake_df.write_text(
            "#!/usr/bin/env bash\nprintf 'Avail\\n%u\\n' \"${STACK_FAKE_DISK_BYTES}\"\n",
            encoding="utf-8",
        )
        fake_df.chmod(0o755)
        meminfo = self.root / "large-meminfo"
        meminfo.write_text("MemTotal: 67108864 kB\n", encoding="ascii")
        result = self.run_script(
            "stack.sh", "up", "core", DF_BIN=shell_path(fake_df),
            MEMINFO_FILE=shell_path(meminfo), STACK_FAKE_DISK_BYTES=str(15 * 1024**3),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("20 GiB", result.stderr)
        self.assertIn("up", self.docker_calls()[-1])

    def test_up_requires_jq_before_docker_and_sums_exact_selected_memory(self):
        missing = self.run_script("stack.sh", "up", "core", JQ_BIN="/missing/jq")
        self.assertNotEqual(0, missing.returncode)
        self.assertIn("jq", missing.stderr.lower())
        self.assertFalse(self.log.exists())

        config = json.dumps({"services": {
            "app-postgres": {"mem_limit": 1073741824},
            "app-redis": {"mem_limit": 536870912},
            "chroma": {"mem_limit": 999999999999},
        }})
        exact_meminfo = self.root / "exact-meminfo"
        exact_meminfo.write_text("MemTotal: 3670016 kB\n", encoding="ascii")
        exact = self.run_script(
            "stack.sh", "up", "core", STACK_FAKE_CONFIG_JSON=config,
            MEMINFO_FILE=shell_path(exact_meminfo),
        )
        self.assertEqual(0, exact.returncode, exact.stderr)
        self.assertNotIn("host memory", exact.stderr)

        self.log.unlink()
        below_meminfo = self.root / "below-meminfo"
        below_meminfo.write_text("MemTotal: 3670015 kB\n", encoding="ascii")
        below = self.run_script(
            "stack.sh", "up", "core", STACK_FAKE_CONFIG_JSON=config,
            MEMINFO_FILE=shell_path(below_meminfo),
        )
        self.assertEqual(0, below.returncode, below.stderr)
        self.assertIn("host memory", below.stderr)
        self.assertIn("up", self.docker_calls()[-1])


if __name__ == "__main__":
    unittest.main()
