from copy import deepcopy
import unittest

from tests.helpers import read_env, render_compose, repo_path


class ToolComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = render_compose("core", "tools")
        cls.fixture = read_env(repo_path("tests/fixtures/stack.env"))

    def test_tools_join_core_databases(self):
        self.assertEqual(
            {"app-postgres", "app-redis", "pgadmin", "redisinsight"},
            set(self.model["services"]),
        )
        pgadmin_dependencies = self.model["services"]["pgadmin"]["depends_on"]
        redisinsight_dependencies = self.model["services"]["redisinsight"]["depends_on"]
        self.assertEqual({"app-postgres"}, set(pgadmin_dependencies))
        self.assertEqual({"app-redis"}, set(redisinsight_dependencies))
        self.assertEqual("service_healthy", pgadmin_dependencies["app-postgres"]["condition"])
        self.assertEqual("service_healthy", redisinsight_dependencies["app-redis"]["condition"])

    def test_tools_are_loopback_persistent_and_tunnel_ready(self):
        services = self.model["services"]
        expected = {
            "pgadmin": {
                "image": "docker.io/dpage/pgadmin4:9.16",
                "port": ("127.0.0.1", 5050, 5050),
                "volume": ("pgadmin_data", "/var/lib/pgadmin"),
                "memory": 512 * 1024**2,
            },
            "redisinsight": {
                "image": "docker.io/redis/redisinsight:3.4.2",
                "port": ("127.0.0.1", 5540, 5540),
                "volume": ("redisinsight_data", "/data"),
                "memory": 512 * 1024**2,
            },
        }

        for name, requirements in expected.items():
            service = services[name]
            self.assertEqual(["tools"], service["profiles"], name)
            self.assertEqual(requirements["image"], service["image"], name)
            self.assertEqual("unless-stopped", service["restart"], name)
            self.assertEqual(requirements["memory"], int(service["mem_limit"]), name)
            self.assertIn(requirements["port"], [
                (port["host_ip"], int(port["published"]), port["target"])
                for port in service["ports"]
            ], name)
            self.assertIn(requirements["volume"], [
                (volume["source"], volume["target"])
                for volume in service["volumes"]
            ], name)
            self.assertTrue(service.get("healthcheck"), name)
            self.assertEqual({"infra"}, set(service["networks"]), name)

        self.assertEqual(
            "remote-infra-stack-pgadmin-data",
            self.model["volumes"]["pgadmin_data"]["name"],
        )
        self.assertEqual(
            "remote-infra-stack-redisinsight-data",
            self.model["volumes"]["redisinsight_data"]["name"],
        )

    def assert_tools_get_only_required_credentials_and_redis_connection(self, model):
        pgadmin = model["services"]["pgadmin"]["environment"]
        redisinsight_service = model["services"]["redisinsight"]
        redisinsight = redisinsight_service["environment"]

        self.assertEqual("5050", pgadmin["PGADMIN_LISTEN_PORT"])
        self.assertEqual("admin@example.local", pgadmin["PGADMIN_DEFAULT_EMAIL"])
        self.assertIn("PGADMIN_DEFAULT_PASSWORD", pgadmin)
        self.assertNotIn("APP_POSTGRES_PASSWORD", pgadmin)

        self.assertEqual("5540", redisinsight["RI_APP_PORT"])
        self.assertEqual("app-redis", redisinsight["RI_REDIS_HOST"])
        self.assertEqual("6379", redisinsight["RI_REDIS_PORT"])
        self.assertTrue(
            redisinsight["RI_REDIS_PASSWORD"] == self.fixture["APP_REDIS_PASSWORD"],
            "RedisInsight must use the application Redis credential",
        )
        self.assertTrue(
            redisinsight["RI_ENCRYPTION_KEY"] == self.fixture["REDISINSIGHT_ENCRYPTION_KEY"],
            "RedisInsight must use its configured encryption key",
        )
        self.assertEqual(
            ["CMD-SHELL", "wget -qO- http://127.0.0.1:5540/api/health/ >/dev/null || exit 1"],
            redisinsight_service["healthcheck"]["test"],
        )

    def test_tools_get_only_required_credentials_and_redis_connection(self):
        self.assert_tools_get_only_required_credentials_and_redis_connection(self.model)

    def test_redisinsight_credential_contract_rejects_values_accepted_by_the_previous_check(self):
        wrong_password = deepcopy(self.model)
        wrong_password["services"]["redisinsight"]["environment"]["RI_REDIS_PASSWORD"] = "invalid-test-value"
        wrong_encryption_key = deepcopy(self.model)
        wrong_encryption_key["services"]["redisinsight"]["environment"]["RI_ENCRYPTION_KEY"] = "invalid-test-value"

        for name, mutated in (
            ("Redis credential", wrong_password),
            ("encryption key", wrong_encryption_key),
        ):
            with self.subTest(mutation=name):
                environment = mutated["services"]["redisinsight"]["environment"]
                self.assertTrue(
                    all(key in environment for key in ("RI_REDIS_PASSWORD", "RI_ENCRYPTION_KEY")),
                    "mutation must bypass the previous key-presence check",
                )
                with self.assertRaises(AssertionError):
                    self.assert_tools_get_only_required_credentials_and_redis_connection(mutated)


if __name__ == "__main__":
    unittest.main()
