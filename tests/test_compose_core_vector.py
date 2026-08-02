import unittest

from tests.helpers import render_compose


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
        self.assertEqual("app_redis_data", redis["volumes"][0]["source"])
        self.assertEqual(
            "remote-infra-stack-app-postgres-data", model["volumes"]["app_postgres_data"]["name"]
        )
        self.assertEqual(
            "remote-infra-stack-app-redis-data", model["volumes"]["app_redis_data"]["name"]
        )

    def test_vector_uses_nonstandard_host_port(self):
        model = render_compose("vector")
        self.assertEqual({"chroma"}, set(model["services"]))
        chroma = model["services"]["chroma"]
        self.assertEqual(18000, int(chroma["ports"][0]["published"]))
        self.assertEqual(8000, int(chroma["ports"][0]["target"]))
        self.assertEqual("/data", chroma["environment"]["CHROMA_PERSIST_PATH"])
        self.assertIn("healthcheck", chroma)
        self.assertEqual("chroma_data", chroma["volumes"][0]["source"])
        self.assertEqual("remote-infra-stack-chroma-data", model["volumes"]["chroma_data"]["name"])
