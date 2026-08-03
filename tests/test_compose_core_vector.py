from copy import deepcopy
import unittest
from pathlib import Path

from tests.helpers import render_compose, repo_path


class CoreVectorComposeTests(unittest.TestCase):
    def test_core_services_are_isolated_and_persistent(self):
        model = render_compose("core")
        self.assertEqual({"app-postgres", "app-redis"}, set(model["services"]))
        postgres = model["services"]["app-postgres"]
        redis = model["services"]["app-redis"]
        self.assertEqual("127.0.0.1", postgres["ports"][0]["host_ip"])
        self.assertEqual(15432, int(postgres["ports"][0]["published"]))
        self.assertEqual(16379, int(redis["ports"][0]["published"]))
        self.assertIn("healthcheck", postgres)
        self.assertIn("healthcheck", redis)
        self.assertEqual("app_postgres_data", postgres["volumes"][0]["source"])
        self.assertEqual("/var/lib/postgresql", postgres["volumes"][0]["target"])
        self.assertEqual("app_redis_data", redis["volumes"][0]["source"])
        self.assertEqual(
            "remote-infra-stack-app-postgres-data", model["volumes"]["app_postgres_data"]["name"]
        )
        self.assertEqual(
            "remote-infra-stack-app-redis-data", model["volumes"]["app_redis_data"]["name"]
        )

    def test_vector_uses_nonstandard_host_port(self):
        model = render_compose("vector")
        self.assertEqual({"chroma", "chroma-admin"}, set(model["services"]))
        chroma = model["services"]["chroma"]
        self.assertEqual(18000, int(chroma["ports"][0]["published"]))
        self.assertEqual(8000, int(chroma["ports"][0]["target"]))
        self.assertEqual("/data", chroma["environment"]["CHROMA_PERSIST_PATH"])
        self.assertIn("healthcheck", chroma)
        health_command = chroma["healthcheck"]["test"][1]
        self.assertIn("/dev/tcp/127.0.0.1/8000", health_command)
        self.assertNotIn("wget", health_command)
        self.assertEqual("chroma_data", chroma["volumes"][0]["source"])
        self.assertEqual("remote-infra-stack-chroma-data", model["volumes"]["chroma_data"]["name"])

    def assert_chroma_admin_build_contract(self, admin):
        self.assertEqual("remote-infra-stack/chromadb-admin:efe867c86c78", admin["image"])
        self.assertEqual(repo_path(".").resolve(), Path(admin["build"]["context"]).resolve())
        self.assertEqual(
            "docker.io/library/node:20.19.2-bookworm-slim@sha256:7cd3fbc830c75c92256fe1122002add9a1c025831af8770cd0bf8e45688ef661",
            admin["build"]["args"]["NODE_IMAGE"],
        )
        dockerfile = Path(admin["build"]["dockerfile"])
        if not dockerfile.is_absolute():
            dockerfile = Path(admin["build"]["context"]) / dockerfile
        self.assertTrue(
            dockerfile.resolve().as_posix().endswith("/images/chromadb-admin/Dockerfile")
        )

    def test_vector_chroma_admin_is_loopback_stateless_and_built_locally(self):
        model = render_compose("vector")
        admin = model["services"]["chroma-admin"]

        self.assert_chroma_admin_build_contract(admin)
        self.assertEqual({"chroma"}, set(admin["depends_on"]))
        self.assertEqual("service_healthy", admin["depends_on"]["chroma"]["condition"])
        self.assertEqual("127.0.0.1", admin["ports"][0]["host_ip"])
        self.assertEqual(18001, int(admin["ports"][0]["published"]))
        self.assertEqual(3001, int(admin["ports"][0]["target"]))
        self.assertNotIn("volumes", admin)

    def test_vector_chroma_admin_build_contract_rejects_wrong_node_image(self):
        admin = deepcopy(render_compose("vector")["services"]["chroma-admin"])
        admin["build"]["args"]["NODE_IMAGE"] = "docker.io/library/node:wrong"

        with self.assertRaises(AssertionError):
            self.assert_chroma_admin_build_contract(admin)

    def test_vector_chroma_admin_has_health_memory_and_network_contract(self):
        admin = render_compose("vector")["services"]["chroma-admin"]

        self.assertIn("healthcheck", admin)
        self.assertEqual(
            [
                "CMD",
                "node",
                "-e",
                "fetch('http://127.0.0.1:3001').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))",
            ],
            admin["healthcheck"]["test"],
        )
        self.assertEqual("10s", admin["healthcheck"]["interval"])
        self.assertEqual("5s", admin["healthcheck"]["timeout"])
        self.assertEqual(18, admin["healthcheck"]["retries"])
        self.assertEqual("30s", admin["healthcheck"]["start_period"])
        self.assertEqual("536870912", admin["mem_limit"])
        self.assertEqual({"infra": None}, admin["networks"])
