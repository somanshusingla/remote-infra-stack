from copy import deepcopy
import unittest

from tests.helpers import render_compose


class DynamoDbComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = render_compose("dynamodb")

    def test_dynamodb_profile_is_isolated_and_uses_approved_images(self):
        self.assertEqual({"dynamodb-local", "dynamodb-admin"}, set(self.model["services"]))

        local = self.model["services"]["dynamodb-local"]
        admin = self.model["services"]["dynamodb-admin"]
        self.assertEqual(["dynamodb"], local["profiles"])
        self.assertEqual(["dynamodb"], admin["profiles"])
        self.assertEqual(
            "docker.io/amazon/dynamodb-local:3.3.0@sha256:d89f8fcc6b1a39cb35976c248ed42a28c66ae00dc043099210f5571e42648ab4",
            local["image"],
        )
        self.assertEqual(
            "docker.io/aaronshaf/dynamodb-admin:5.3.4@sha256:ac41724cd99706256d405a14a5fb96f51f18c41a630c84fa3357f900cbd16d2e",
            admin["image"],
        )

    def assert_dynamodb_local_can_write_its_fresh_named_volume(self, local):
        self.assertEqual("root", local.get("user"))

    def test_dynamodb_local_is_loopback_persistent_and_healthy(self):
        services = self.model["services"]
        self.assertIn("dynamodb-local", services)
        local = services["dynamodb-local"]

        self.assertEqual(["-jar", "DynamoDBLocal.jar", "-sharedDb", "-dbPath", "./data"], local["command"])
        self.assertEqual("/home/dynamodblocal", local["working_dir"])
        self.assert_dynamodb_local_can_write_its_fresh_named_volume(local)
        self.assertEqual("unless-stopped", local["restart"])
        self.assertEqual("127.0.0.1", local["ports"][0]["host_ip"])
        self.assertEqual(18002, int(local["ports"][0]["published"]))
        self.assertEqual(8000, int(local["ports"][0]["target"]))
        self.assertEqual("dynamodb_data", local["volumes"][0]["source"])
        self.assertEqual("/home/dynamodblocal/data", local["volumes"][0]["target"])
        self.assertEqual(
            ["CMD-SHELL", "exec bash -ec 'exec 3<>/dev/tcp/127.0.0.1/8000'"],
            local["healthcheck"]["test"],
        )
        self.assertEqual("5s", local["healthcheck"]["interval"])
        self.assertEqual("5s", local["healthcheck"]["timeout"])
        self.assertEqual(24, local["healthcheck"]["retries"])
        self.assertEqual(1073741824, int(local["mem_limit"]))
        self.assertEqual({"infra": None}, local["networks"])
        self.assertEqual(
            "remote-infra-stack-dynamodb-data",
            self.model["volumes"]["dynamodb_data"]["name"],
        )

    def test_dynamodb_local_write_compatibility_rejects_nonroot_user(self):
        local = deepcopy(self.model["services"]["dynamodb-local"])
        local["user"] = "dynamodblocal"

        with self.assertRaises(AssertionError):
            self.assert_dynamodb_local_can_write_its_fresh_named_volume(local)

    def assert_dynamodb_admin_contract(self, admin):
        self.assertEqual("unless-stopped", admin["restart"])
        self.assertEqual(
            {
                "HOST": "0.0.0.0",
                "PORT": "8001",
                "DYNAMO_ENDPOINT": "http://dynamodb-local:8000",
                "AWS_REGION": "us-east-1",
                "AWS_ACCESS_KEY_ID": "local",
                "AWS_SECRET_ACCESS_KEY": "local",
            },
            admin["environment"],
        )
        self.assertEqual("127.0.0.1", admin["ports"][0]["host_ip"])
        self.assertEqual(18003, int(admin["ports"][0]["published"]))
        self.assertEqual(8001, int(admin["ports"][0]["target"]))
        self.assertEqual({"dynamodb-local"}, set(admin["depends_on"]))
        self.assertEqual("service_healthy", admin["depends_on"]["dynamodb-local"]["condition"])
        self.assertEqual(
            [
                "CMD",
                "node",
                "-e",
                "fetch('http://127.0.0.1:8001').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))",
            ],
            admin["healthcheck"]["test"],
        )
        self.assertEqual("10s", admin["healthcheck"]["interval"])
        self.assertEqual("5s", admin["healthcheck"]["timeout"])
        self.assertEqual(18, admin["healthcheck"]["retries"])
        self.assertEqual("20s", admin["healthcheck"]["start_period"])
        self.assertEqual(536870912, int(admin["mem_limit"]))
        self.assertEqual({"infra": None}, admin["networks"])
        self.assertNotIn("volumes", admin)

    def test_dynamodb_admin_uses_only_local_dynamo_and_health_gated_startup(self):
        services = self.model["services"]
        self.assertIn("dynamodb-admin", services)
        self.assert_dynamodb_admin_contract(services["dynamodb-admin"])

    def test_dynamodb_admin_contract_rejects_wrong_endpoint_and_extra_dependency(self):
        services = self.model["services"]
        self.assertIn("dynamodb-admin", services)

        wrong_endpoint = deepcopy(services["dynamodb-admin"])
        wrong_endpoint["environment"]["DYNAMO_ENDPOINT"] = "http://wrong-host:8000"
        extra_dependency = deepcopy(services["dynamodb-admin"])
        extra_dependency["depends_on"]["unrelated"] = {"condition": "service_healthy"}

        for name, mutated in (
            ("wrong endpoint", wrong_endpoint),
            ("extra dependency", extra_dependency),
        ):
            with self.subTest(mutation=name):
                with self.assertRaises(AssertionError):
                    self.assert_dynamodb_admin_contract(mutated)


if __name__ == "__main__":
    unittest.main()
