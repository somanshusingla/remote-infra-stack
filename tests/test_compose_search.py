import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import render_compose, repo_path


class SearchComposeTests(unittest.TestCase):
    def test_search_keeps_security_and_host_limits(self):
        model = render_compose("search")
        search = model["services"]["opensearch"]
        dashboards = model["services"]["opensearch-dashboards"]
        self.assertNotIn("DISABLE_SECURITY_PLUGIN", search["environment"])
        self.assertEqual("single-node", search["environment"]["discovery.type"])
        self.assertEqual("127.0.0.1", search["ports"][0]["host_ip"])
        self.assertEqual(9200, int(search["ports"][0]["published"]))
        self.assertEqual(5601, int(dashboards["ports"][0]["published"]))
        self.assertEqual(["opensearch"], list(dashboards["depends_on"]))
        self.assertEqual(-1, search["ulimits"]["memlock"]["soft"])

    def test_search_mounts_sources_without_shadowing_runtime_config(self):
        model = render_compose("search")
        search = model["services"]["opensearch"]
        mounts = {mount["target"]: mount for mount in search["volumes"]}

        self.assertEqual(
            ["/usr/local/bin/remote-infra-stack-opensearch-entrypoint"], search["entrypoint"]
        )
        self.assertTrue(
            mounts["/usr/share/opensearch/config-template/opensearch.yml"]["read_only"]
        )
        self.assertTrue(
            mounts["/usr/local/bin/remote-infra-stack-opensearch-entrypoint"]["read_only"]
        )
        self.assertNotIn("/usr/share/opensearch/config/opensearch.yml", mounts)


class OpenSearchEntrypointTests(unittest.TestCase):
    @staticmethod
    def bash() -> str | None:
        candidates = [shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                if subprocess.run([candidate, "--version"], capture_output=True).returncode == 0:
                    return candidate
        return None

    def run_wrapper(self, delegate_exit_code: int) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        wrapper = repo_path("config/opensearch/docker-entrypoint.sh")
        self.assertTrue(wrapper.is_file(), "OpenSearch wrapper must exist")
        shell = self.bash()
        if not shell:
            self.skipTest("Bash is not available")

        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        root = Path(temporary_directory.name)
        template = root / "opensearch.yml"
        runtime_config = root / "runtime" / "opensearch.yml"
        delegated_config = root / "delegated-config.yml"
        delegated_arguments = root / "delegated-arguments.txt"
        delegate = root / "delegate.sh"
        template.write_text("cluster.name: copied-template\n", encoding="utf-8")
        runtime_config.parent.mkdir()
        delegate.write_text(
            "#!/usr/bin/env bash\n"
            "cp \"$OPENSEARCH_CONFIG_PATH\" \"$DELEGATED_CONFIG\"\n"
            "printf '<%s>' \"$@\" > \"$DELEGATED_ARGUMENTS\"\n"
            "exit \"$DELEGATE_EXIT_CODE\"\n",
            encoding="utf-8",
        )
        delegate.chmod(delegate.stat().st_mode | 0o100)
        environment = os.environ | {
            "OPENSEARCH_CONFIG_TEMPLATE": str(template),
            "OPENSEARCH_CONFIG_PATH": str(runtime_config),
            "OPENSEARCH_DOCKER_ENTRYPOINT": str(delegate),
            "DELEGATED_CONFIG": str(delegated_config),
            "DELEGATED_ARGUMENTS": str(delegated_arguments),
            "DELEGATE_EXIT_CODE": str(delegate_exit_code),
        }
        result = subprocess.run(
            [shell, str(wrapper), "-Ecluster.name=runtime", "--flag=two words"],
            env=environment,
            capture_output=True,
            text=True,
        )
        return result, delegated_config, delegated_arguments

    def test_wrapper_copies_template_and_delegates_original_arguments(self):
        result, delegated_config, delegated_arguments = self.run_wrapper(0)

        self.assertEqual(0, result.returncode, f"stdout: {result.stdout}\nstderr: {result.stderr}")
        self.assertEqual("cluster.name: copied-template\n", delegated_config.read_text(encoding="utf-8"))
        self.assertEqual(
            "<-Ecluster.name=runtime><--flag=two words>",
            delegated_arguments.read_text(encoding="utf-8"),
        )

    def test_wrapper_propagates_delegate_failure(self):
        result, _, _ = self.run_wrapper(23)

        self.assertEqual(23, result.returncode, f"stdout: {result.stdout}\nstderr: {result.stderr}")
