import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def read_document(self, relative_path: str) -> str:
        path = REPOSITORY_ROOT / relative_path
        self.assertTrue(path.is_file(), f"missing documentation file: {relative_path}")
        return path.read_text(encoding="utf-8")

    def test_readme_documents_profiles_and_both_operator_surfaces(self):
        readme = self.read_document("README.md")

        for profile in ("core", "vector", "search", "observability", "tools"):
            with self.subTest(profile=profile):
                self.assertIn(f"`{profile}`", readme)

        for script in ("init-env", "check", "bootstrap", "deploy", "tunnel", "stack"):
            with self.subTest(script=script, shell="bash"):
                self.assertIn(f"./scripts/{script}.sh", readme)
            with self.subTest(script=script, shell="powershell"):
                self.assertIn(f".\\scripts\\{script}.ps1", readme)

    def test_readme_documents_hosts_and_capability_gated_future_lts_support(self):
        readme = self.read_document("README.md")

        for expected in (
            "Ubuntu 22.04",
            "Ubuntu 24.04",
            "Ubuntu 26.04",
            "future Ubuntu LTS",
            "Docker apt repository",
            "required packages",
            "AWS",
            "GCP",
            "existing SSH-accessible Ubuntu VM",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, readme)

    def test_readme_lists_openssl_before_the_bash_quick_start(self):
        readme = self.read_document("README.md")
        requirements = readme[: readme.index("## Quick start")]

        self.assertIn("OpenSSL", requirements)

    def test_readme_lists_every_local_tunnel_endpoint(self):
        readme = self.read_document("README.md")

        endpoints = (
            "127.0.0.1:5432",
            "127.0.0.1:6379",
            "http://127.0.0.1:18000",
            "https://127.0.0.1:9200",
            "http://127.0.0.1:5601",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5050",
            "http://127.0.0.1:5540",
            "http://127.0.0.1:9090",
            "http://127.0.0.1:9091",
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, readme)

        self.assertIn("OpenSearch Dashboards", readme)
        self.assertIn("ELK-style", readme)
        self.assertIn("CHROMA_PORT=18000", readme)

    def test_readme_contains_local_application_environment_examples(self):
        readme = self.read_document("README.md")

        examples = (
            "DATABASE_URL=postgresql://app:<password>@127.0.0.1:5432/app",
            "REDIS_URL=redis://:<password>@127.0.0.1:6379/0",
            "CHROMA_HOST=127.0.0.1",
            "CHROMA_PORT=18000",
            "OPENSEARCH_URL=https://127.0.0.1:9200",
            "LANGFUSE_BASE_URL=http://127.0.0.1:3000",
            "PGADMIN_DEFAULT_EMAIL=admin@example.com",
        )
        for example in examples:
            with self.subTest(example=example):
                self.assertIn(example, readme)

    def test_readme_explains_security_and_disposable_data_boundaries(self):
        readme = self.read_document("README.md")

        for expected in (
            "Langfuse API keys",
            "Langfuse UI",
            "development TLS certificate",
            "Chroma has no built-in authentication",
            "SSH tunnel and loopback binding",
            "named Docker volumes",
            "No backup/export automation is included",
            "disposable",
            "down preserves named volumes",
            "destroy permanently and irreversibly removes all project named volumes",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, readme)

    def test_operations_guide_orders_the_complete_workflow_for_each_shell(self):
        operations = self.read_document("docs/operations.md")
        bash_start = operations.index("## macOS/Linux (Bash)")
        powershell_start = operations.index("## Windows (PowerShell)")
        bash_section = operations[bash_start:powershell_start]
        powershell_section = operations[powershell_start:]

        bash_steps = (
            "git clone https://github.com/somanshusingla/remote-infra-stack.git",
            "./scripts/init-env.sh",
            "cp remote.env.example remote.env",
            "./scripts/check.sh core vector search observability tools",
            "./scripts/bootstrap.sh",
            "./scripts/deploy.sh core vector search observability tools",
            "./scripts/tunnel.sh core vector search observability tools",
            "./scripts/stack.sh status",
            "./scripts/stack.sh logs",
            "./scripts/stack.sh stop",
            "./scripts/stack.sh down",
            "./scripts/stack.sh destroy",
        )
        powershell_steps = (
            "git clone https://github.com/somanshusingla/remote-infra-stack.git",
            ".\\scripts\\init-env.ps1",
            "Copy-Item .\\remote.env.example .\\remote.env",
            ".\\scripts\\check.ps1 core vector search observability tools",
            ".\\scripts\\bootstrap.ps1",
            ".\\scripts\\deploy.ps1 core vector search observability tools",
            ".\\scripts\\tunnel.ps1 core vector search observability tools",
            ".\\scripts\\stack.ps1 status",
            ".\\scripts\\stack.ps1 logs",
            ".\\scripts\\stack.ps1 stop",
            ".\\scripts\\stack.ps1 down",
            ".\\scripts\\stack.ps1 destroy",
        )

        for section, steps in ((bash_section, bash_steps), (powershell_section, powershell_steps)):
            positions = []
            for step in steps:
                with self.subTest(step=step):
                    positions.append(section.index(step))
            self.assertEqual(positions, sorted(positions), "workflow steps are out of order")

    def test_operations_guide_makes_normal_and_destructive_lifecycle_distinct(self):
        operations = self.read_document("docs/operations.md")

        self.assertRegex(
            operations,
            re.compile(r"down.+preserves.+named volumes", re.IGNORECASE | re.DOTALL),
        )
        self.assertRegex(
            operations,
            re.compile(r"destroy.+irreversible.+data loss", re.IGNORECASE | re.DOTALL),
        )
        self.assertIn("Do not run `destroy` as a routine cleanup command", operations)

    def test_operations_guide_uses_smoke_tested_capacity_guidance(self):
        operations = self.read_document("docs/operations.md")

        self.assertIn("32 GiB is recommended when running all profiles", operations)
        self.assertNotIn("48 GiB is the practical minimum", operations)
        self.assertNotIn("64 GiB is preferred", operations)


if __name__ == "__main__":
    unittest.main()
