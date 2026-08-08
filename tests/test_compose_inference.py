from copy import deepcopy
import unittest

from tests.helpers import render_compose, repo_path


class InferenceComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = render_compose("inference")

    def assert_inference_service_contract(
        self,
        service,
        *,
        model,
        published_port,
        volume,
        memory,
    ):
        self.assertEqual(["inference"], service["profiles"])
        self.assertEqual(
            "docker.io/ollama/ollama:0.32.5@sha256:4dea9fb511947e24a84237bb636b0203abcb2ff0d3fbc7b4ff865deb91362131",
            service["image"],
        )
        self.assertEqual("unless-stopped", service["restart"])
        self.assertEqual(
            ["/bin/sh", "/opt/remote-infra/bootstrap.sh"],
            service["entrypoint"],
        )
        self.assertEqual(
            {
                "OLLAMA_CONTEXT_LENGTH": "8192",
                "OLLAMA_KEEP_ALIVE": "5m",
                "OLLAMA_MAX_LOADED_MODELS": "1",
                "OLLAMA_MODEL": model,
                "OLLAMA_NUM_PARALLEL": "1",
            },
            service["environment"],
        )

        self.assertEqual(1, len(service["ports"]))
        port = service["ports"][0]
        self.assertEqual("127.0.0.1", port["host_ip"])
        self.assertEqual(published_port, int(port["published"]))
        self.assertEqual(11434, int(port["target"]))

        self.assertEqual(2, len(service["volumes"]))
        cache_mount, bootstrap_mount = service["volumes"]
        self.assertEqual("volume", cache_mount["type"])
        self.assertEqual(volume, cache_mount["source"])
        self.assertEqual("/root/.ollama", cache_mount["target"])
        self.assertFalse(cache_mount.get("read_only", False))
        self.assertEqual("bind", bootstrap_mount["type"])
        self.assertEqual(
            str(repo_path("config/ollama/bootstrap.sh")),
            bootstrap_mount["source"],
        )
        self.assertEqual(
            "/opt/remote-infra/bootstrap.sh",
            bootstrap_mount["target"],
        )
        self.assertTrue(bootstrap_mount["read_only"])

        self.assertEqual(
            [
                "CMD-SHELL",
                'test -f /tmp/remote-infra-model-ready && OLLAMA_HOST=127.0.0.1:11434 /bin/ollama show "$$OLLAMA_MODEL" >/dev/null',
            ],
            service["healthcheck"]["test"],
        )
        self.assertEqual("10s", service["healthcheck"]["interval"])
        self.assertEqual("10s", service["healthcheck"]["timeout"])
        self.assertEqual(12, service["healthcheck"]["retries"])
        self.assertEqual("1h30m0s", service["healthcheck"]["start_period"])
        self.assertEqual(memory, int(service["mem_limit"]))
        self.assertEqual({"infra": None}, service["networks"])
        self.assertEqual(
            [{"capabilities": ["gpu"], "count": -1, "driver": "nvidia"}],
            service["deploy"]["resources"]["reservations"]["devices"],
        )
        self.assertNotIn("depends_on", service)
        self.assertNotIn("user", service)

    def test_inference_profile_has_only_two_independent_ollama_services(self):
        self.assertEqual(
            {"ollama-llm", "ollama-embedding"},
            set(self.model["services"]),
        )

        llm = self.model["services"]["ollama-llm"]
        embedding = self.model["services"]["ollama-embedding"]
        self.assertEqual(llm["image"], embedding["image"])
        self.assertNotIn("depends_on", llm)
        self.assertNotIn("depends_on", embedding)

    def test_llm_uses_its_model_port_cache_and_memory_limit(self):
        self.assert_inference_service_contract(
            self.model["services"]["ollama-llm"],
            model="gemma4:e4b",
            published_port=11440,
            volume="ollama_llm_data",
            memory=15032385536,
        )

    def test_embedding_uses_its_model_port_cache_and_memory_limit(self):
        self.assert_inference_service_contract(
            self.model["services"]["ollama-embedding"],
            model="embeddinggemma:300m",
            published_port=11441,
            volume="ollama_embedding_data",
            memory=2147483648,
        )

    def test_inference_contract_rejects_crossed_or_weakened_service_configuration(self):
        llm = self.model["services"]["ollama-llm"]
        cases = []

        swapped_model = deepcopy(llm)
        swapped_model["environment"]["OLLAMA_MODEL"] = "embeddinggemma:300m"
        cases.append(("swapped model", swapped_model, AssertionError))

        swapped_port = deepcopy(llm)
        swapped_port["ports"][0]["published"] = "11441"
        cases.append(("swapped port", swapped_port, AssertionError))

        swapped_volume = deepcopy(llm)
        swapped_volume["volumes"][0]["source"] = "ollama_embedding_data"
        cases.append(("swapped cache", swapped_volume, AssertionError))

        writable_bootstrap = deepcopy(llm)
        writable_bootstrap["volumes"][1]["read_only"] = False
        cases.append(("writable bootstrap", writable_bootstrap, AssertionError))

        wrong_bind_source = deepcopy(llm)
        wrong_bind_source["volumes"][1]["source"] = str(
            repo_path("config/ollama/other.sh")
        )
        cases.append(("wrong bootstrap source", wrong_bind_source, AssertionError))

        list_healthcheck = deepcopy(llm)
        list_healthcheck["healthcheck"]["test"][1] = (
            "test -f /tmp/remote-infra-model-ready && "
            "OLLAMA_HOST=127.0.0.1:11434 /bin/ollama list >/dev/null"
        )
        cases.append(("list healthcheck", list_healthcheck, AssertionError))

        cross_dependency = deepcopy(llm)
        cross_dependency["depends_on"] = {
            "ollama-embedding": {"condition": "service_healthy"}
        }
        cases.append(("cross dependency", cross_dependency, AssertionError))

        no_gpu_reservation = deepcopy(llm)
        del no_gpu_reservation["deploy"]["resources"]["reservations"]["devices"]
        cases.append(("missing GPU reservation", no_gpu_reservation, KeyError))

        for name, mutated, expected_exception in cases:
            with self.subTest(mutation=name):
                with self.assertRaises(expected_exception):
                    self.assert_inference_service_contract(
                        mutated,
                        model="gemma4:e4b",
                        published_port=11440,
                        volume="ollama_llm_data",
                        memory=15032385536,
                    )

    def test_inference_volumes_have_stable_project_owned_names(self):
        self.assertEqual(
            "remote-infra-stack-ollama-llm-data",
            self.model["volumes"]["ollama_llm_data"]["name"],
        )
        self.assertEqual(
            "remote-infra-stack-ollama-embedding-data",
            self.model["volumes"]["ollama_embedding_data"]["name"],
        )


if __name__ == "__main__":
    unittest.main()
