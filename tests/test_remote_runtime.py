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
        self.df_log = self.root / "df.log"
        self.sysctl_log = self.root / "sysctl.log"
        self.nvidia_log = self.root / "nvidia.log"
        self.http_body_log = self.root / "http-bodies.log"
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
[[ "${STACK_FAKE_SYSCTL_ERROR:-0}" == 0 ]] || exit 73
printf '%s\n' "${STACK_FAKE_IP_FORWARD-1}"
""",
            encoding="utf-8",
        )
        self.fake_sysctl.chmod(0o755)
        self.fake_df = self.root / "df"
        self.fake_df.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
{
  printf 'df'
  printf ' %q' "$@"
  printf '\\n'
} >>"$STACK_DF_LOG"
target=${!#}
if [[ -n "${STACK_FAKE_DF_FAIL_TARGET:-}" && "$target" == "$STACK_FAKE_DF_FAIL_TARGET" ]]; then
  exit 71
fi
if [[ "$target" == "${STACK_FAKE_DOCKER_ROOT_DIR:-/var/lib/docker}" ]]; then
  available=${STACK_FAKE_DOCKER_BYTES:-32212254720}
else
  available=${STACK_FAKE_RELEASE_BYTES:-32212254720}
fi
printf 'Avail\\n%s\\n' "$available"
""",
            encoding="utf-8",
        )
        self.fake_df.chmod(0o755)
        self.large_meminfo = self.root / "large-meminfo"
        self.large_meminfo.write_text("MemTotal: 67108864 kB\n", encoding="ascii")
        self.fake_free = self.root / "free"
        self.fake_free.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.fake_free.chmod(0o755)
        self.env = {
            **os.environ,
            "STACK_ROOT": shell_path(self.root),
            "STACK_RELEASE_DIR": shell_path(repo_path(".")),
            "DOCKER_BIN": shell_path(repo_path("tests/fakes/docker")),
            "CURL_BIN": shell_path(repo_path("tests/fakes/docker")),
            "JQ_BIN": shell_path(repo_path("tests/fakes/jq")),
            "FREE_BIN": shell_path(self.fake_free),
            "SYSCTL_BIN": shell_path(self.fake_sysctl),
            "NVIDIA_SMI_BIN": shell_path(repo_path("tests/fakes/nvidia-smi")),
            "STACK_DOCKER_LOG": shell_path(self.log),
            "STACK_SYSCTL_LOG": shell_path(self.sysctl_log),
            "STACK_NVIDIA_LOG": shell_path(self.nvidia_log),
            "STACK_FAKE_HTTP_BODY_LOG": shell_path(self.http_body_log),
        }

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, name, *args, **env):
        return subprocess.run(
            [BASH_BIN, shell_path(repo_path(f"scripts/remote/{name}")), *args],
            env={**self.env, **env}, capture_output=True, text=True,
        )

    def docker_calls(self):
        if not self.log.exists():
            return []
        return [shlex.split(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def capacity_env(self):
        return {
            "DF_BIN": shell_path(self.fake_df),
            "STACK_DF_LOG": shell_path(self.df_log),
            "MEMINFO_FILE": shell_path(self.large_meminfo),
        }

    def df_calls(self):
        return [shlex.split(line) for line in self.df_log.read_text(encoding="utf-8").splitlines()]

    def sysctl_calls(self):
        return [
            shlex.split(line)
            for line in self.sysctl_log.read_text(encoding="utf-8").splitlines()
        ]

    def nvidia_calls(self):
        if not self.nvidia_log.exists():
            return []
        return [
            shlex.split(line)
            for line in self.nvidia_log.read_text(encoding="utf-8").splitlines()
        ]

    def http_bodies(self):
        if not self.http_body_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.http_body_log.read_text(encoding="utf-8").splitlines()
        ]

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

    def test_new_profiles_expand_to_exact_services_for_stop_and_logs(self):
        result = self.run_script("stack.sh", "stop", "vector", "dynamodb", "inference")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [
                "stop", "chroma", "chroma-admin", "dynamodb-local", "dynamodb-admin",
                "ollama-llm", "ollama-embedding",
            ],
            self.docker_calls()[0][-7:],
        )

        for service in (
            "chroma-admin", "dynamodb-local", "dynamodb-admin", "ollama-llm",
            "ollama-embedding",
        ):
            with self.subTest(service=service):
                self.log.unlink()
                result = self.run_script("stack.sh", "logs", service)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(["logs", "-f", service], self.docker_calls()[0][-3:])

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
            {"Service": "chroma-admin", "State": "running", "Health": "healthy"},
        ])
        result = self.run_script("health.sh", "vector", STACK_FAKE_PS_JSON=healthy_replicas)
        self.assertEqual(0, result.returncode, result.stderr)

        cases = {
            "one-starting-replica": [
                {"Service": "chroma", "State": "running", "Health": "healthy"},
                {"Service": "chroma", "State": "running", "Health": "starting"},
                {"Service": "chroma-admin", "State": "running", "Health": "healthy"},
            ],
            "one-unhealthy-replica": [
                {"Service": "chroma", "State": "running", "Health": "healthy"},
                {"Service": "chroma", "State": "running", "Health": "unhealthy"},
                {"Service": "chroma-admin", "State": "running", "Health": "healthy"},
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
            {"Service": "chroma-admin", "State": "running", "Health": "healthy"},
        ])
        all_containers = json.dumps([
            {"Service": "chroma", "State": "running", "Health": "healthy"},
            {"Service": "chroma", "State": "exited", "Health": ""},
            {"Service": "chroma-admin", "State": "running", "Health": "healthy"},
        ])
        result = self.run_script(
            "health.sh", "vector", STACK_FAKE_PS_JSON=visible,
            STACK_FAKE_PS_ALL_JSON=all_containers,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("chroma", result.stderr)
        self.assertEqual(1, len(self.docker_calls()))

    def test_health_rejects_each_missing_or_unhealthy_data_inference_service_before_probes(self):
        required_services = (
            "chroma", "chroma-admin", "dynamodb-local", "dynamodb-admin",
            "ollama-llm", "ollama-embedding",
        )
        healthy = {
            service: {"Service": service, "State": "running", "Health": "healthy"}
            for service in required_services
        }
        for service in required_services:
            for state in ("missing", "unhealthy"):
                with self.subTest(service=service, state=state):
                    self.log.unlink(missing_ok=True)
                    records = [record.copy() for name, record in healthy.items() if name != service]
                    if state == "unhealthy":
                        records.append(
                            {"Service": service, "State": "running", "Health": "unhealthy"}
                        )
                    result = self.run_script(
                        "health.sh", "vector", "dynamodb", "inference",
                        STACK_FAKE_PS_ALL_JSON=json.dumps(records),
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(service, result.stderr)
                    self.assertEqual(
                        1, len(self.docker_calls()),
                        "functional HTTP, SDK, and Ollama probes must not run",
                    )

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

    def test_health_authenticates_both_search_endpoints_without_exposing_password(self):
        secret = "search-health-sentinel"
        env_file = self.root / "runtime/.env"
        env_file.write_text(
            env_file.read_text(encoding="utf-8").replace(
                "OPENSEARCH_INITIAL_ADMIN_PASSWORD=remote-only-test-value",
                f"OPENSEARCH_INITIAL_ADMIN_PASSWORD={secret}",
            ),
            encoding="utf-8",
        )

        result = self.run_script("health.sh", "search")

        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.docker_calls()
        self.assertEqual(2, sum("--config" in call for call in calls))
        combined = result.stdout + result.stderr + self.log.read_text(encoding="utf-8")
        self.assertNotIn(secret, combined)

    def test_health_checks_new_uis_and_dynamodb(self):
        result = self.run_script("health.sh", "vector", "dynamodb")
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.docker_calls()
        http_calls = [call for call in calls if "--max-time" in call]
        self.assertEqual(
            [
                "http://127.0.0.1:18000/api/v2/heartbeat",
                "http://127.0.0.1:18001",
                "http://127.0.0.1:18003",
            ],
            [call[-1] for call in http_calls],
        )
        self.assertNotIn("http://127.0.0.1:18002", [call[-1] for call in http_calls])

        exec_calls = [call[call.index("-T") + 1:] for call in calls if "exec" in call]
        dynamodb_script = (
            "const { DynamoDBClient, ListTablesCommand } = require('@aws-sdk/client-dynamodb'); "
            "const client = new DynamoDBClient({ endpoint: process.env.DYNAMO_ENDPOINT, "
            "region: process.env.AWS_REGION, credentials: { accessKeyId: "
            "process.env.AWS_ACCESS_KEY_ID, secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY } }); "
            "client.send(new ListTablesCommand({ Limit: 1 })).then(() => client.destroy())"
            ".catch(error => { console.error(error); client.destroy(); process.exit(1); });"
        )
        self.assertEqual(
            [
                ["dynamodb-admin", "node", "-e", dynamodb_script],
            ],
            exec_calls,
        )

    def test_health_proves_bounded_inference_requests_gpu_residency_and_device_requests(self):
        result = self.run_script("health.sh", "inference")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([
            {
                "model": "gemma4:e4b", "prompt": "healthcheck", "stream": False,
                "options": {"num_predict": 1}, "keep_alive": "5m",
            },
            {
                "model": "embeddinggemma:300m", "input": "healthcheck",
                "keep_alive": "5m",
            },
        ], self.http_bodies())
        calls = self.docker_calls()
        expected_post_prefix = [
            "docker", "--fail", "--silent", "--show-error", "--max-time", "120",
            "--header", "Content-Type: application/json", "--data-binary", "@-",
        ]
        self.assertIn(
            expected_post_prefix + ["http://127.0.0.1:11440/api/generate"], calls
        )
        self.assertIn(
            expected_post_prefix + ["http://127.0.0.1:11441/api/embed"], calls
        )
        for endpoint in (
            "http://127.0.0.1:11440/api/ps",
            "http://127.0.0.1:11441/api/ps",
        ):
            self.assertIn(endpoint, [call[-1] for call in calls])
        self.assertIn(["docker", "inspect", "--format", "{{json .HostConfig.DeviceRequests}}", "container-ollama-llm"], calls)
        self.assertIn(["docker", "inspect", "--format", "{{json .HostConfig.DeviceRequests}}", "container-ollama-embedding"], calls)
        self.assertTrue(any("ps" in call and "--quiet" in call and "ollama-llm" in call for call in calls))
        self.assertTrue(any("ps" in call and "--quiet" in call and "ollama-embedding" in call for call in calls))
        self.assertEqual([
            [
                "nvidia-smi", "--query-compute-apps=used_gpu_memory",
                "--format=csv,noheader,nounits",
            ]
        ], self.nvidia_calls())
        captured = result.stdout + result.stderr + self.log.read_text(encoding="utf-8")
        self.assertNotIn("generated-health-secret", captured)
        self.assertNotIn("0.125", captured)

    def test_health_rejects_each_inference_execution_failure(self):
        cases = (
            ("chat-failure", {"STACK_FAKE_FAIL_OLLAMA_REQUEST": "generate"}),
            ("embedding-failure", {"STACK_FAKE_FAIL_OLLAMA_REQUEST": "embed"}),
            ("invalid-json", {"STACK_FAKE_GENERATE_RESPONSE": "not-json"}),
            ("missing-model", {"STACK_FAKE_LLM_PS_RESPONSE": '{"models":[]}'}),
            ("zero-vram", {"STACK_FAKE_EMBEDDING_PS_RESPONSE": '{"models":[{"name":"embeddinggemma:300m","size_vram":0}]}'}),
            ("llm-device-request", {"STACK_FAKE_DEVICE_REQUEST_OLLAMA_LLM": "[]"}),
            ("embedding-device-request", {"STACK_FAKE_DEVICE_REQUEST_OLLAMA_EMBEDDING": "[]"}),
            ("zero-host-memory", {"STACK_FAKE_COMPUTE_MEMORY": "0"}),
        )
        for label, env in cases:
            with self.subTest(label=label):
                self.log.unlink(missing_ok=True)
                self.nvidia_log.unlink(missing_ok=True)
                self.http_body_log.unlink(missing_ok=True)

                result = self.run_script("health.sh", "inference", **env)

                self.assertNotEqual(0, result.returncode)
                captured = result.stdout + result.stderr
                self.assertNotIn("generated-health-secret", captured)
                self.assertNotIn("0.125", captured)

    def test_health_loads_strict_model_pins_without_evaluating_versions_env(self):
        marker = self.root / "health-parser-must-not-run"
        valid = (
            "OLLAMA_LLM_MODEL=gemma4:e4b\n"
            "OLLAMA_EMBEDDING_MODEL=embeddinggemma:300m\n"
        )
        cases = {
            "missing": "OLLAMA_LLM_MODEL=gemma4:e4b\n",
            "quoted": 'OLLAMA_LLM_MODEL="gemma4:e4b"\nOLLAMA_EMBEDDING_MODEL=embeddinggemma:300m\n',
            "duplicate": valid + "OLLAMA_LLM_MODEL=gemma4:e4b\n",
            "shell-syntax": f"OLLAMA_LLM_MODEL=$(touch {shell_path(marker)})\nOLLAMA_EMBEDDING_MODEL=embeddinggemma:300m\n",
        }
        for label, content in cases.items():
            with self.subTest(label=label):
                self.log.unlink(missing_ok=True)
                versions = self.root / f"versions-{label}.env"
                versions.write_text(content, encoding="utf-8")

                result = self.run_script(
                    "health.sh", "inference",
                    STACK_VERSIONS_ENV_FILE=shell_path(versions),
                )

                self.assertNotEqual(0, result.returncode)
                self.assertIn("OLLAMA_", result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse(any("/api/generate" in call for call in self.docker_calls()))

    def test_health_propagates_each_new_endpoint_and_protocol_failure(self):
        profile_by_url = {
            "http://127.0.0.1:18000/api/v2/heartbeat": "vector",
            "http://127.0.0.1:18001": "vector",
            "http://127.0.0.1:18003": "dynamodb",
        }
        for endpoint, profile in profile_by_url.items():
            with self.subTest(endpoint=endpoint):
                if self.log.exists():
                    self.log.unlink()
                result = self.run_script(
                    "health.sh", profile, STACK_FAKE_FAIL_HTTP_URL=endpoint
                )
                self.assertNotEqual(0, result.returncode)
                self.assertTrue(self.log.exists())
                self.assertIn(endpoint, [call[-1] for call in self.docker_calls()])

        for failure_env, profile, service in (
            ({"STACK_FAKE_FAIL_DYNAMODB": "1"}, "dynamodb", "dynamodb-admin"),
        ):
            with self.subTest(failure=failure_env):
                self.log.unlink(missing_ok=True)
                result = self.run_script("health.sh", profile, **failure_env)
                self.assertNotEqual(0, result.returncode)
                self.assertTrue(self.log.exists())
                self.assertIn(
                    service,
                    [call[call.index("-T") + 1] for call in self.docker_calls() if "exec" in call],
                )

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

    def test_preflight_checks_docker_root_for_inference(self):
        docker_root = "/srv/docker data"
        result = self.run_script(
            "preflight.sh", "inference", **self.capacity_env(),
            STACK_FAKE_DOCKER_ROOT_DIR=docker_root,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [["sysctl", "-n", "net.ipv4.ip_forward"]],
            self.sysctl_calls(),
        )
        self.assertEqual(
            ["docker", "info", "--format", "{{.DockerRootDir}}"],
            next(call for call in self.docker_calls() if "info" in call),
        )
        self.assertEqual(
            [
                ["df", "--output=avail", "-B1", shell_path(self.root)],
                ["df", "--output=avail", "-B1", docker_root],
            ],
            self.df_calls(),
        )

    def test_preflight_rejects_disabled_or_unreadable_ipv4_forwarding_before_other_probes(self):
        cases = (
            ({"STACK_FAKE_IP_FORWARD": "0"}, "found 0"),
            ({"STACK_FAKE_IP_FORWARD": "2"}, "found 2"),
            ({"STACK_FAKE_IP_FORWARD": ""}, "found empty"),
            ({"STACK_FAKE_SYSCTL_ERROR": "1"}, "could not read"),
        )
        for env, message in cases:
            with self.subTest(env=env):
                self.log.unlink(missing_ok=True)
                self.df_log.unlink(missing_ok=True)
                self.sysctl_log.unlink(missing_ok=True)

                result = self.run_script(
                    "preflight.sh", "core", **self.capacity_env(), **env
                )

                self.assertNotEqual(0, result.returncode)
                self.assertIn("net.ipv4.ip_forward must equal 1", result.stderr)
                self.assertIn(message, result.stderr)
                self.assertEqual(
                    [["sysctl", "-n", "net.ipv4.ip_forward"]],
                    self.sysctl_calls(),
                )
                self.assertFalse(self.df_log.exists())
                self.assertFalse(self.log.exists())

    def test_preflight_fails_below_twenty_gib_of_docker_storage(self):
        result = self.run_script(
            "preflight.sh", "inference", **self.capacity_env(),
            STACK_FAKE_DOCKER_BYTES=str(20 * 1024**3 - 1),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "less than 20 GiB is available on the Docker storage filesystem for inference",
            result.stderr,
        )

        self.log.unlink()
        self.df_log.unlink()
        boundary = self.run_script(
            "preflight.sh", "inference", **self.capacity_env(),
            STACK_FAKE_DOCKER_BYTES=str(20 * 1024**3),
        )
        self.assertEqual(0, boundary.returncode, boundary.stderr)

    def test_preflight_preserves_release_storage_boundaries_and_probe_failures(self):
        ten_gib = self.run_script(
            "preflight.sh", "core", **self.capacity_env(),
            STACK_FAKE_RELEASE_BYTES=str(10 * 1024**3),
        )
        self.assertEqual(0, ten_gib.returncode, ten_gib.stderr)
        self.assertIn("less than 20 GiB of disk space", ten_gib.stderr)

        self.log.unlink()
        self.df_log.unlink()
        twenty_gib = self.run_script(
            "preflight.sh", "core", **self.capacity_env(),
            STACK_FAKE_RELEASE_BYTES=str(20 * 1024**3),
        )
        self.assertEqual(0, twenty_gib.returncode, twenty_gib.stderr)
        self.assertNotIn("disk space", twenty_gib.stderr)

        for probe_env in (
            {"STACK_FAKE_RELEASE_BYTES": "not-a-number"},
            {"STACK_FAKE_DF_FAIL_TARGET": shell_path(self.root)},
        ):
            with self.subTest(probe=probe_env):
                self.log.unlink(missing_ok=True)
                self.df_log.unlink(missing_ok=True)
                result = self.run_script(
                    "preflight.sh", "core", **self.capacity_env(), **probe_env
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn(
                    f"could not determine free disk space for {shell_path(self.root)}",
                    result.stderr,
                )

    def test_preflight_warns_but_does_not_fail_memory_overcommit(self):
        tiny_meminfo = self.root / "tiny-inference-meminfo"
        tiny_meminfo.write_text("MemTotal: 1048576 kB\n", encoding="ascii")
        capacity = self.capacity_env()
        capacity["MEMINFO_FILE"] = shell_path(tiny_meminfo)
        result = self.run_script(
            "preflight.sh", "inference", **capacity,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(
            "selected service memory limits plus 2 GiB host overhead exceed host memory",
            result.stderr,
        )

    def test_preflight_rejects_docker_root_and_storage_probe_errors(self):
        cases = (
            ({"STACK_FAKE_DOCKER_INFO_ERROR": "1"}, "Docker storage root"),
            ({"STACK_FAKE_DOCKER_ROOT_DIR": ""}, "Docker storage root"),
            ({"STACK_FAKE_DOCKER_BYTES": "not-a-number"}, "Docker storage"),
            ({"STACK_FAKE_DF_FAIL_TARGET": "/var/lib/docker"}, "Docker storage"),
        )
        for env, message in cases:
            with self.subTest(env=env):
                if self.log.exists():
                    self.log.unlink()
                if self.df_log.exists():
                    self.df_log.unlink()
                result = self.run_script(
                    "preflight.sh", "inference", **self.capacity_env(), **env
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)

    def test_preflight_skips_docker_and_host_gpu_checks_without_inference(self):
        result = self.run_script("preflight.sh", "core", **self.capacity_env())
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("info", [argument for call in self.docker_calls() for argument in call])
        self.assertNotIn("run", [argument for call in self.docker_calls() for argument in call])
        self.assertEqual([], self.nvidia_calls())
        self.assertEqual(1, len(self.df_calls()))

    def test_preflight_requires_exact_host_and_pinned_container_t4_before_compose(self):
        result = self.run_script("preflight.sh", "inference", **self.capacity_env())

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]],
            self.nvidia_calls(),
        )
        calls = self.docker_calls()
        container_validation = [
            "docker", "run", "--rm", "--gpus", "all",
            "docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df",
            "nvidia-smi", "--query-gpu=name", "--format=csv,noheader",
        ]
        self.assertIn(container_validation, calls)
        self.assertLess(calls.index(container_validation), next(
            index for index, call in enumerate(calls) if "config" in call
        ))

    def test_preflight_rejects_missing_or_malformed_cuda_pin_before_compose(self):
        marker = self.root / "gpu-parser-must-not-run"
        valid_pin = (
            "NVIDIA_CUDA_IMAGE=docker.io/nvidia/cuda:12.9.1-base-ubuntu24.04@sha256:"
            + "5d2e53778e2180e01676aa8bac1aada242e95230ec97e21ecfb33de4e27cd1df"
        )
        cases = {
            "missing": "OLLAMA_LLM_MODEL=gemma4:e4b\n",
            "quoted": f'NVIDIA_CUDA_IMAGE="{valid_pin.split("=", 1)[1]}"\n',
            "duplicate": f"{valid_pin}\n{valid_pin}\n",
            "shell-syntax": f"NVIDIA_CUDA_IMAGE=$(touch {shell_path(marker)})\n",
        }
        for label, content in cases.items():
            with self.subTest(label=label):
                self.log.unlink(missing_ok=True)
                self.nvidia_log.unlink(missing_ok=True)
                release = self.root / f"release-{label}"
                release.mkdir()
                (release / "versions.env").write_text(content, encoding="utf-8")

                result = self.run_script(
                    "preflight.sh", "inference", **self.capacity_env(),
                    STACK_RELEASE_DIR=shell_path(release),
                )

                self.assertNotEqual(0, result.returncode)
                self.assertIn("NVIDIA_CUDA_IMAGE", result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse(any("config" in call for call in self.docker_calls()))

    def test_preflight_rejects_wrong_or_failed_host_and_container_gpu_before_compose(self):
        cases = (
            ({"STACK_FAKE_HOST_GPU_NAMES": ""}, "host"),
            ({"STACK_FAKE_HOST_GPU_NAMES": "NVIDIA A100"}, "host"),
            ({"STACK_FAKE_HOST_GPU_NAMES": "NVIDIA T4\nNVIDIA T4"}, "host"),
            ({"STACK_FAKE_NVIDIA_STATUS": "7"}, "host"),
            ({"STACK_FAKE_CONTAINER_GPU_NAMES": ""}, "container"),
            ({"STACK_FAKE_CONTAINER_GPU_NAMES": "NVIDIA A100"}, "container"),
            ({"STACK_FAKE_CONTAINER_GPU_NAMES": "NVIDIA T4\nNVIDIA T4"}, "container"),
            ({"STACK_FAKE_CONTAINER_GPU_STATUS": "8"}, "container"),
        )
        for env, boundary in cases:
            with self.subTest(env=env):
                self.log.unlink(missing_ok=True)
                self.nvidia_log.unlink(missing_ok=True)

                result = self.run_script(
                    "preflight.sh", "inference", **self.capacity_env(), **env
                )

                self.assertNotEqual(0, result.returncode)
                self.assertIn(boundary, result.stderr.lower())
                self.assertFalse(any("config" in call for call in self.docker_calls()))

    def test_preflight_honors_compose_override_for_held_release_inputs(self):
        compose_log = self.root / "compose-override.log"
        compose_override = self.root / "compose-override.sh"
        compose_override.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >>"$STACK_FAKE_COMPOSE_OVERRIDE_LOG"
printf '%s\\n' '{"services":{"app-postgres":{"mem_limit":1073741824},"app-redis":{"mem_limit":536870912}}}'
""",
            encoding="utf-8",
        )
        compose_override.chmod(0o755)
        result = self.run_script(
            "preflight.sh", "core", **self.capacity_env(),
            STACK_COMPOSE_SCRIPT=shell_path(compose_override),
            STACK_FAKE_COMPOSE_OVERRIDE_LOG=shell_path(compose_log),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "core -- config --format json",
            compose_log.read_text(encoding="utf-8").strip(),
        )
        self.assertFalse(self.log.exists())

    def test_preflight_uses_exact_memory_sum_filter(self):
        jq_log = self.root / "jq.log"
        result = self.run_script(
            "preflight.sh", "core", **self.capacity_env(),
            STACK_FAKE_JQ_LOG=shell_path(jq_log),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(jq_log.exists(), "memory-summing jq invocation was not logged")
        self.assertEqual(
            [
                "--args",
                "[.services[$ARGS.positional[]].mem_limit // 0 | tonumber] | add // 0",
                "app-postgres",
                "app-redis",
            ],
            json.loads(jq_log.read_text(encoding="utf-8")),
        )

    def test_stack_honors_compose_override_and_runs_preflight_once_before_exact_up(self):
        result = self.run_script(
            "stack.sh", "up", "inference", **self.capacity_env(),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        calls = self.docker_calls()
        self.assertEqual(1, sum(call[1:3] == ["info", "--format"] for call in calls))
        self.assertEqual(1, sum("config" in call for call in calls))
        self.assertEqual(["up", "-d", "--wait", "--build"], calls[-1][-4:])

        override_log = self.root / "stack-compose-override.log"
        override = self.root / "stack-compose-override.sh"
        override.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >>"$STACK_FAKE_COMPOSE_OVERRIDE_LOG"
if [[ " $* " == *" config "* ]]; then
  printf '%s\\n' '{"services":{"app-postgres":{"mem_limit":1073741824},"app-redis":{"mem_limit":536870912}}}'
fi
""",
            encoding="utf-8",
        )
        override.chmod(0o755)
        self.log.unlink()
        result = self.run_script(
            "stack.sh", "up", "core", **self.capacity_env(),
            STACK_COMPOSE_SCRIPT=shell_path(override),
            STACK_FAKE_COMPOSE_OVERRIDE_LOG=shell_path(override_log),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ["core -- config --format json", "core -- up -d --wait --build"],
            override_log.read_text(encoding="utf-8").splitlines(),
        )
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
