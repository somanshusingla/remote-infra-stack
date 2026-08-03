from copy import deepcopy
import unittest

from tests.helpers import read_env, render_compose, repo_path


class ComposeInvariantTests(unittest.TestCase):
    profiles = ("core", "vector", "search", "observability", "tools", "dynamodb")

    @classmethod
    def setUpClass(cls):
        cls.model = render_compose(*cls.profiles)
        cls.approved_images = read_env(repo_path("versions.env"))

    def test_all_published_ports_are_loopback_and_unique(self):
        published = set()
        for name, service in self.model["services"].items():
            for port in service.get("ports", []):
                self.assertEqual("127.0.0.1", port["host_ip"], name)
                self.assertNotIn(int(port["published"]), published, name)
                published.add(int(port["published"]))

    def assert_images_network_and_project_names_are_stable(self, model):
        expected_images = {
            "app-postgres": "APP_POSTGRES_IMAGE",
            "app-redis": "APP_REDIS_IMAGE",
            "chroma": "CHROMA_IMAGE",
            "chroma-admin": "remote-infra-stack/chromadb-admin:efe867c86c78",
            "dynamodb-local": "DYNAMODB_LOCAL_IMAGE",
            "dynamodb-admin": "DYNAMODB_ADMIN_IMAGE",
            "opensearch": "OPENSEARCH_IMAGE",
            "opensearch-dashboards": "OPENSEARCH_DASHBOARDS_IMAGE",
            "langfuse-postgres": "LANGFUSE_POSTGRES_IMAGE",
            "langfuse-redis": "LANGFUSE_REDIS_IMAGE",
            "clickhouse": "CLICKHOUSE_IMAGE",
            "minio": "MINIO_IMAGE",
            "langfuse-worker": "LANGFUSE_WORKER_IMAGE",
            "langfuse-web": "LANGFUSE_WEB_IMAGE",
            "pgadmin": "PGADMIN_IMAGE",
            "redisinsight": "REDISINSIGHT_IMAGE",
        }
        self.assertEqual("remote-infra-stack", model["name"])
        self.assertEqual(16, len(expected_images))
        self.assertEqual(set(expected_images), set(model["services"]))
        for name, expected_image in expected_images.items():
            self.assertEqual(
                self.approved_images.get(expected_image, expected_image),
                model["services"][name]["image"],
                name,
            )

        self.assertEqual(
            "remote-infra-stack-infra",
            model["networks"]["infra"]["name"],
        )

    def test_images_network_and_project_names_are_stable(self):
        self.assert_images_network_and_project_names_are_stable(self.model)

    def test_image_contract_rejects_mutations_accepted_by_the_previous_check(self):
        wrong_project = deepcopy(self.model)
        wrong_project["name"] = "wrong-project"
        untagged_image = deepcopy(self.model)
        untagged_image["services"]["pgadmin"]["image"] = "docker.io/dpage/pgadmin4"
        nonapproved_fixed_image = deepcopy(self.model)
        nonapproved_fixed_image["services"]["pgadmin"]["image"] = "docker.io/dpage/pgadmin4:9.17"

        for name, mutated in (
            ("wrong project", wrong_project),
            ("untagged image", untagged_image),
            ("nonapproved fixed image", nonapproved_fixed_image),
        ):
            with self.subTest(mutation=name):
                self.assertTrue(
                    all(":latest" not in service["image"] for service in mutated["services"].values()),
                    "mutation must bypass the previous latest-only check",
                )
                with self.assertRaises(AssertionError):
                    self.assert_images_network_and_project_names_are_stable(mutated)

    def assert_stateful_services_use_named_volumes_and_enabled_healthchecks(self, model):
        stateful = {
            "app-postgres", "app-redis", "chroma", "dynamodb-local", "opensearch",
            "langfuse-postgres", "langfuse-redis", "clickhouse", "minio",
            "pgadmin", "redisinsight",
        }
        self.assertTrue(stateful.issubset(model["services"]), "all stateful services must exist")
        for name in stateful:
            service = model["services"][name]
            self.assertTrue(
                any(
                    mount.get("type") == "volume" and mount.get("source") in model["volumes"]
                    for mount in service.get("volumes", [])
                ),
                name,
            )
            healthcheck = service.get("healthcheck", {})
            self.assertFalse(healthcheck.get("disable", False), name)
            self.assertTrue(healthcheck.get("test"), name)

        for name, volume in model["volumes"].items():
            self.assertEqual(f"remote-infra-stack-{name.replace('_', '-')}", volume["name"])

    def test_all_stateful_services_use_named_volumes_and_healthchecks(self):
        self.assert_stateful_services_use_named_volumes_and_enabled_healthchecks(self.model)

    def test_stateful_contract_rejects_mutations_accepted_by_the_previous_check(self):
        bind_mount = deepcopy(self.model)
        bind_mount["services"]["app-postgres"]["volumes"] = [{
            "type": "bind",
            "source": "/invalid",
            "target": "/var/lib/postgresql/data",
        }]
        disabled_healthcheck = deepcopy(self.model)
        disabled_healthcheck["services"]["app-postgres"]["healthcheck"] = {"disable": True}

        for name, mutated in (
            ("bind mount", bind_mount),
            ("disabled health check", disabled_healthcheck),
        ):
            with self.subTest(mutation=name):
                service = mutated["services"]["app-postgres"]
                self.assertTrue(
                    bool(service.get("volumes")) and bool(service.get("healthcheck")),
                    "mutation must bypass the previous truthiness check",
                )
                with self.assertRaises(AssertionError):
                    self.assert_stateful_services_use_named_volumes_and_enabled_healthchecks(mutated)

    def assert_services_have_expected_profiles_and_dependencies(self, model):
        expected_profiles = {
            "app-postgres": "core",
            "app-redis": "core",
            "chroma": "vector",
            "chroma-admin": "vector",
            "dynamodb-local": "dynamodb",
            "dynamodb-admin": "dynamodb",
            "opensearch": "search",
            "opensearch-dashboards": "search",
            "langfuse-postgres": "observability",
            "langfuse-redis": "observability",
            "clickhouse": "observability",
            "minio": "observability",
            "langfuse-worker": "observability",
            "langfuse-web": "observability",
            "pgadmin": "tools",
            "redisinsight": "tools",
        }
        expected_dependencies = {
            "app-postgres": set(),
            "app-redis": set(),
            "chroma": set(),
            "chroma-admin": {"chroma"},
            "dynamodb-local": set(),
            "dynamodb-admin": {"dynamodb-local"},
            "opensearch": set(),
            "opensearch-dashboards": {"opensearch"},
            "langfuse-postgres": set(),
            "langfuse-redis": set(),
            "clickhouse": set(),
            "minio": set(),
            "langfuse-worker": {"langfuse-postgres", "langfuse-redis", "clickhouse", "minio"},
            "langfuse-web": {"langfuse-postgres", "langfuse-redis", "clickhouse", "minio"},
            "pgadmin": {"app-postgres"},
            "redisinsight": {"app-redis"},
        }
        self.assertEqual(set(expected_profiles), set(model["services"]))
        self.assertEqual(set(expected_dependencies), set(model["services"]))
        for name, profile in expected_profiles.items():
            self.assertEqual([profile], model["services"][name]["profiles"], name)

        for name, dependencies in expected_dependencies.items():
            self.assertEqual(dependencies, set(model["services"][name].get("depends_on", {})), name)

    def test_services_have_expected_profiles_and_dependencies(self):
        self.assert_services_have_expected_profiles_and_dependencies(self.model)

    def test_dependency_contract_rejects_unwanted_same_profile_edge(self):
        mutated = deepcopy(self.model)
        mutated["services"]["chroma-admin"]["depends_on"]["chroma-admin"] = {
            "condition": "service_healthy",
        }

        with self.assertRaises(AssertionError):
            self.assert_services_have_expected_profiles_and_dependencies(mutated)


if __name__ == "__main__":
    unittest.main()
