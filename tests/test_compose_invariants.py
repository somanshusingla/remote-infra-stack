import unittest

from tests.helpers import render_compose


class ComposeInvariantTests(unittest.TestCase):
    profiles = ("core", "vector", "search", "observability", "tools")

    @classmethod
    def setUpClass(cls):
        cls.model = render_compose(*cls.profiles)

    def test_all_published_ports_are_loopback_and_unique(self):
        published = set()
        for name, service in self.model["services"].items():
            for port in service.get("ports", []):
                self.assertEqual("127.0.0.1", port["host_ip"], name)
                self.assertNotIn(int(port["published"]), published, name)
                published.add(int(port["published"]))

    def test_images_network_and_project_names_are_stable(self):
        for name, service in self.model["services"].items():
            self.assertNotIn(":latest", service["image"], name)

        self.assertEqual(
            "remote-infra-stack-infra",
            self.model["networks"]["infra"]["name"],
        )

    def test_all_stateful_services_use_named_volumes_and_healthchecks(self):
        stateful = {
            "app-postgres", "app-redis", "chroma", "opensearch",
            "langfuse-postgres", "langfuse-redis", "clickhouse", "minio",
            "pgadmin", "redisinsight",
        }
        for name in stateful:
            self.assertTrue(self.model["services"][name].get("volumes"), name)
            self.assertTrue(self.model["services"][name].get("healthcheck"), name)

        for name, volume in self.model["volumes"].items():
            self.assertEqual(f"remote-infra-stack-{name.replace('_', '-')}", volume["name"])

    def test_services_belong_to_expected_profiles_without_cross_profile_dependencies(self):
        expected_profiles = {
            "app-postgres": "core",
            "app-redis": "core",
            "chroma": "vector",
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
        self.assertEqual(set(expected_profiles), set(self.model["services"]))
        for name, profile in expected_profiles.items():
            self.assertEqual([profile], self.model["services"][name]["profiles"], name)

        for name, service in self.model["services"].items():
            for dependency in service.get("depends_on", {}):
                dependency_profile = expected_profiles[dependency]
                service_profile = expected_profiles[name]
                self.assertTrue(
                    dependency_profile == service_profile
                    or (service_profile == "tools" and dependency_profile == "core"),
                    f"{name} unexpectedly depends on {dependency}",
                )


if __name__ == "__main__":
    unittest.main()
