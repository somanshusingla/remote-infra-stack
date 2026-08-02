import unittest
from urllib.parse import urlparse

from tests.helpers import read_env, render_compose, repo_path


class ObservabilityComposeTests(unittest.TestCase):
    expected = {
        "langfuse-web",
        "langfuse-worker",
        "langfuse-postgres",
        "langfuse-redis",
        "clickhouse",
        "minio",
    }

    @classmethod
    def setUpClass(cls):
        cls.model = render_compose("observability")
        cls.fixture = read_env(repo_path("tests/fixtures/stack.env"))

    def test_observability_topology_is_complete_and_isolated(self):
        self.assertEqual(self.expected, set(self.model["services"]))
        for name, service in self.model["services"].items():
            self.assertEqual(["observability"], service["profiles"], name)
            self.assertEqual({"infra"}, set(service["networks"]), name)

        for private in (
            "langfuse-worker",
            "langfuse-postgres",
            "langfuse-redis",
            "clickhouse",
        ):
            self.assertFalse(self.model["services"][private].get("ports"), private)

        web_port = self.model["services"]["langfuse-web"]["ports"]
        self.assertEqual(
            [("127.0.0.1", 3000, 3000)],
            [(item["host_ip"], int(item["published"]), item["target"]) for item in web_port],
        )
        minio_ports = self.model["services"]["minio"]["ports"]
        self.assertEqual(
            {("127.0.0.1", 9090, 9000), ("127.0.0.1", 9091, 9001)},
            {
                (item["host_ip"], int(item["published"]), item["target"])
                for item in minio_ports
            },
        )

    def test_web_and_worker_wait_for_healthy_dependencies(self):
        for service in ("langfuse-web", "langfuse-worker"):
            dependencies = self.model["services"][service]["depends_on"]
            self.assertEqual(
                {"langfuse-postgres", "langfuse-redis", "clickhouse", "minio"},
                set(dependencies),
            )
            self.assertTrue(
                all(value["condition"] == "service_healthy" for value in dependencies.values())
            )

    def test_langfuse_uses_only_private_dependency_urls(self):
        worker = self.model["services"]["langfuse-worker"]["environment"]
        web = self.model["services"]["langfuse-web"]["environment"]
        database_url = urlparse(worker["DATABASE_URL"])

        self.assertEqual("postgresql", database_url.scheme)
        self.assertEqual("langfuse-postgres", database_url.hostname)
        self.assertEqual(5432, database_url.port)
        self.assertEqual(f"/{self.fixture['LANGFUSE_POSTGRES_DB']}", database_url.path)
        self.assertTrue(database_url.username == self.fixture["LANGFUSE_POSTGRES_USER"])
        self.assertTrue(database_url.password == self.fixture["LANGFUSE_POSTGRES_PASSWORD"])
        self.assertEqual("http://clickhouse:8123", worker["CLICKHOUSE_URL"])
        self.assertEqual("clickhouse://clickhouse:9000", worker["CLICKHOUSE_MIGRATION_URL"])
        self.assertEqual("langfuse-redis", worker["REDIS_HOST"])
        self.assertEqual("6379", worker["REDIS_PORT"])
        self.assertEqual("http://minio:9000", worker["LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT"])
        self.assertEqual("http://minio:9000", worker["LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT"])
        self.assertEqual("http://localhost:9090", web["LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT"])
        self.assertEqual("http://localhost:3000", web["NEXTAUTH_URL"])
        self.assertNotIn("NEXTAUTH_SECRET", worker)

    def test_all_langfuse_credentials_reach_their_intended_services(self):
        services = self.model["services"]
        postgres = services["langfuse-postgres"]["environment"]
        redis = services["langfuse-redis"]["environment"]
        clickhouse = services["clickhouse"]["environment"]
        minio = services["minio"]["environment"]
        web = services["langfuse-web"]["environment"]
        worker = services["langfuse-worker"]["environment"]

        for actual, fixture_key in (
            (postgres["POSTGRES_USER"], "LANGFUSE_POSTGRES_USER"),
            (postgres["POSTGRES_DB"], "LANGFUSE_POSTGRES_DB"),
            (postgres["POSTGRES_PASSWORD"], "LANGFUSE_POSTGRES_PASSWORD"),
            (redis["LANGFUSE_REDIS_PASSWORD"], "LANGFUSE_REDIS_PASSWORD"),
            (clickhouse["CLICKHOUSE_USER"], "LANGFUSE_CLICKHOUSE_USER"),
            (clickhouse["CLICKHOUSE_PASSWORD"], "LANGFUSE_CLICKHOUSE_PASSWORD"),
            (minio["MINIO_ROOT_USER"], "LANGFUSE_MINIO_ROOT_USER"),
            (minio["MINIO_ROOT_PASSWORD"], "LANGFUSE_MINIO_ROOT_PASSWORD"),
            (web["SALT"], "LANGFUSE_SALT"),
            (web["ENCRYPTION_KEY"], "LANGFUSE_ENCRYPTION_KEY"),
            (web["NEXTAUTH_SECRET"], "LANGFUSE_NEXTAUTH_SECRET"),
        ):
            self.assertTrue(actual == self.fixture[fixture_key], fixture_key)

        self.assertTrue(worker["REDIS_AUTH"] == self.fixture["LANGFUSE_REDIS_PASSWORD"])
        self.assertTrue(
            worker["LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY"]
            == self.fixture["LANGFUSE_MINIO_ROOT_PASSWORD"]
        )
        self.assertEqual("false", worker["TELEMETRY_ENABLED"])

    def test_datastores_are_persistent_authenticated_and_healthy(self):
        services = self.model["services"]
        expected_volumes = {
            "langfuse-postgres": {
                "/var/lib/postgresql/data": "langfuse_postgres_data",
            },
            "langfuse-redis": {"/data": "langfuse_redis_data"},
            "clickhouse": {
                "/var/lib/clickhouse": "clickhouse_data",
                "/var/log/clickhouse-server": "clickhouse_logs",
            },
            "minio": {"/data": "minio_data"},
        }
        expected_names = {
            "langfuse_postgres_data": "remote-infra-stack-langfuse-postgres-data",
            "langfuse_redis_data": "remote-infra-stack-langfuse-redis-data",
            "clickhouse_data": "remote-infra-stack-clickhouse-data",
            "clickhouse_logs": "remote-infra-stack-clickhouse-logs",
            "minio_data": "remote-infra-stack-minio-data",
        }

        for service_name, expected_mounts in expected_volumes.items():
            mounts = {
                mount["target"]: mount["source"]
                for mount in services[service_name]["volumes"]
            }
            self.assertEqual(expected_mounts, mounts, service_name)
            self.assertIn("healthcheck", services[service_name], service_name)

        for volume, expected_name in expected_names.items():
            self.assertEqual(expected_name, self.model["volumes"][volume]["name"])

        redis_command = " ".join(services["langfuse-redis"]["command"])
        self.assertIn("--maxmemory-policy noeviction", redis_command)
        self.assertIn("--requirepass", redis_command)
        self.assertEqual(
            ["server", "--address", ":9000", "--console-address", ":9001", "/data"],
            services["minio"]["command"],
        )

    def test_images_restarts_health_checks_and_memory_limits_are_explicit(self):
        services = self.model["services"]
        expected_images = {
            "langfuse-web": "docker.io/langfuse/langfuse:3.176.0",
            "langfuse-worker": "docker.io/langfuse/langfuse-worker:3.176.0",
            "langfuse-postgres": "docker.io/postgres:17.10-bookworm",
            "langfuse-redis": "docker.io/redis:7.4.3-bookworm",
            "clickhouse": "docker.io/clickhouse/clickhouse-server:25.12",
            "minio": "docker.io/minio/minio:RELEASE.2025-06-13T11-33-47Z",
        }
        expected_memory = {
            "langfuse-web": 2 * 1024**3,
            "langfuse-worker": 2 * 1024**3,
            "langfuse-postgres": 2 * 1024**3,
            "langfuse-redis": 512 * 1024**2,
            "clickhouse": 6 * 1024**3,
            "minio": 1024**3,
        }

        for service_name in self.expected:
            service = services[service_name]
            self.assertEqual(expected_images[service_name], service["image"], service_name)
            self.assertEqual("unless-stopped", service["restart"], service_name)
            self.assertEqual(expected_memory[service_name], int(service["mem_limit"]), service_name)
            self.assertIn("healthcheck", service, service_name)

        self.assertEqual(
            [
                "CMD",
                "node",
                "-e",
                "fetch('http://127.0.0.1:3000/api/public/ready').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))",
            ],
            services["langfuse-web"]["healthcheck"]["test"],
        )
        self.assertEqual(
            [
                "CMD",
                "node",
                "-e",
                "fetch('http://127.0.0.1:3030/api/health').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))",
            ],
            services["langfuse-worker"]["healthcheck"]["test"],
        )


if __name__ == "__main__":
    unittest.main()
