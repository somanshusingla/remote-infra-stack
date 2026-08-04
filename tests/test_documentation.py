import ast
import json
import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def read_document(self, relative_path: str) -> str:
        path = REPOSITORY_ROOT / relative_path
        self.assertTrue(path.is_file(), f"missing documentation file: {relative_path}")
        return path.read_text(encoding="utf-8")

    def fenced_code_after(self, document: str, marker: str, language: str) -> str:
        marker_end = document.index(marker) + len(marker)
        remainder = document[marker_end:].lstrip()
        opening_fence = f"```{language}\n"
        self.assertTrue(
            remainder.startswith(opening_fence),
            f"expected an immediate {language} fence after {marker!r}",
        )
        code_start = len(opening_fence)
        code_end = remainder.index("\n```", code_start)
        return remainder[code_start:code_end]

    def client_example_fences(self, relative_path: str) -> tuple[str, str, str]:
        document = self.read_document(relative_path)
        markers = {
            "README.md": (
                "DynamoDB Local accepts dummy credentials. For example:",
                "The isolated Ollama endpoints use the normal HTTP API:",
                "PowerShell uses the same endpoints without relying on its `curl` alias:",
            ),
            "docs/operations.md": (
                "DynamoDB Local uses non-secret dummy credentials:",
                "Call the two isolated Ollama APIs independently:",
                "from PowerShell as follows:",
            ),
        }
        dynamodb_marker, bash_marker, powershell_marker = markers[relative_path]
        return (
            self.fenced_code_after(document, dynamodb_marker, "python"),
            self.fenced_code_after(document, bash_marker, "bash"),
            self.fenced_code_after(document, powershell_marker, "powershell"),
        )

    def test_readme_documents_profiles_and_both_operator_surfaces(self):
        readme = self.read_document("README.md")

        for profile in (
            "core",
            "vector",
            "search",
            "observability",
            "tools",
            "dynamodb",
            "inference",
        ):
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
            "existing SSH-accessible\nUbuntu VM",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, readme)

    def test_readme_lists_openssl_before_the_bash_quick_start(self):
        readme = self.read_document("README.md")
        requirements = readme[: readme.index("## Quick start")]

        self.assertIn("OpenSSL", requirements)

    def test_bootstrap_guides_disclose_host_global_forwarding_responsibility(self):
        readme = self.read_document("README.md")
        operations = self.read_document("docs/operations.md")
        readme_requirements = readme[: readme.index("## Quick start")]
        operation_sections = []
        position = 0
        marker = "### 3. Bootstrap the existing VM"
        while True:
            start = operations.find(marker, position)
            if start == -1:
                break
            end = operations.index("### 4. Deploy the selected profiles", start)
            operation_sections.append(operations[start:end])
            position = end

        self.assertEqual(2, len(operation_sections))
        for name, section in (
            ("README requirements", readme_requirements),
            ("Bash operations", operation_sections[0]),
            ("PowerShell operations", operation_sections[1]),
        ):
            with self.subTest(section=name):
                for disclosure in (
                    "`net.ipv4.ip_forward=1`",
                    "host-global IPv4 routing capability",
                    "loopback-only",
                    "SSH-only cloud firewall",
                    "multi-NIC",
                    "custom host firewall",
                    "operator responsibility",
                ):
                    self.assertIn(disclosure, section)

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
            "http://127.0.0.1:18001",
            "http://127.0.0.1:18002",
            "http://127.0.0.1:18003",
            "http://127.0.0.1:11440",
            "http://127.0.0.1:11441",
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, readme)

        self.assertIn("OpenSearch Dashboards", readme)
        self.assertIn("ELK-style", readme)
        self.assertIn("CHROMA_PORT=18000", readme)

    def test_both_guides_document_the_expanded_stack_contract(self):
        required_literals = (
            "`dynamodb`",
            "`inference`",
            "`chroma-admin`",
            "`dynamodb-admin`",
            "http://127.0.0.1:18001",
            "http://127.0.0.1:18002",
            "http://127.0.0.1:18003",
            "http://127.0.0.1:11440",
            "http://127.0.0.1:11441",
            "http://chroma:8000",
        )

        for relative_path in ("README.md", "docs/operations.md"):
            document = self.read_document(relative_path)
            with self.subTest(document=relative_path):
                for literal in required_literals:
                    self.assertIn(literal, document)
                self.assertRegex(
                    document,
                    re.compile(
                        r"(?:never|do not).{0,80}`?0\.0\.0\.0`?",
                        re.IGNORECASE | re.DOTALL,
                    ),
                )
                self.assertRegex(
                    document,
                    re.compile(
                        r"(?:never|do not).{0,120}init-env.{0,20}(?:--force|-Force)",
                        re.IGNORECASE | re.DOTALL,
                    ),
                )
                self.assertRegex(
                    document,
                    re.compile(
                        r"32 GiB.{0,100}(?:not|need not|cannot).{0,80}(?:peak|all profiles)|(?:peak|all profiles).{0,100}(?:not|need not|cannot).{0,80}32 GiB",
                        re.IGNORECASE | re.DOTALL,
                    ),
                )

    def test_both_guides_have_seven_profiles_and_fifteen_endpoint_rows(self):
        expected_profiles = {
            "core",
            "vector",
            "search",
            "observability",
            "tools",
            "dynamodb",
            "inference",
        }

        for relative_path in ("README.md", "docs/operations.md"):
            document = self.read_document(relative_path)
            profile_rows = re.findall(
                r"^\| `(core|vector|search|observability|tools|dynamodb|inference)` \|",
                document,
                re.MULTILINE,
            )
            endpoint_rows = [
                line
                for line in document.splitlines()
                if re.match(
                    r"^\| `(core|vector|search|observability|tools|dynamodb|inference)` \|",
                    line,
                )
                and "127.0.0.1" in line
            ]

            with self.subTest(document=relative_path):
                self.assertEqual(expected_profiles, set(profile_rows))
                self.assertEqual(15, len(endpoint_rows))

    def test_both_guides_document_ignored_env_upgrade_keys(self):
        stack_keys = (
            "CHROMA_ADMIN_MEMORY=512m",
            "DYNAMODB_MEMORY=1g",
            "DYNAMODB_ADMIN_MEMORY=512m",
            "OLLAMA_LLM_MEMORY=14g",
            "OLLAMA_EMBEDDING_MEMORY=2g",
            "OLLAMA_CONTEXT_LENGTH=8192",
            "OLLAMA_KEEP_ALIVE=5m",
        )
        tunnel_keys = (
            "LOCAL_CHROMA_ADMIN_PORT=18001",
            "LOCAL_DYNAMODB_PORT=18002",
            "LOCAL_DYNAMODB_ADMIN_PORT=18003",
            "LOCAL_OLLAMA_LLM_PORT=11440",
            "LOCAL_OLLAMA_EMBEDDING_PORT=11441",
        )

        for relative_path in ("README.md", "docs/operations.md"):
            document = self.read_document(relative_path)
            with self.subTest(document=relative_path):
                for setting in (*stack_keys, *tunnel_keys):
                    self.assertIn(setting, document)

    def test_both_guides_keep_bash_and_powershell_deploy_commands_equivalent(self):
        for relative_path in ("README.md", "docs/operations.md"):
            document = self.read_document(relative_path)
            with self.subTest(document=relative_path):
                self.assertIn(
                    "./scripts/deploy.sh core vector dynamodb",
                    document,
                )
                self.assertIn(
                    "./scripts/deploy.sh inference",
                    document,
                )
                self.assertIn(
                    ".\\scripts\\deploy.ps1 core vector dynamodb",
                    document,
                )
                self.assertIn(
                    ".\\scripts\\deploy.ps1 inference",
                    document,
                )

    def test_both_guides_pair_each_bash_ollama_endpoint_with_its_exact_payload(self):
        expected_calls = [
            (
                "http://127.0.0.1:11440/api/chat",
                {
                    "model": "gemma4:e4b",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": False,
                },
            ),
            (
                "http://127.0.0.1:11441/api/embed",
                {
                    "model": "embeddinggemma:300m",
                    "input": "hello from the remote stack",
                },
            ),
        ]
        curl_pattern = re.compile(
            r"^curl (?P<url>\S+) \\[ \t]*$\n[ \t]+-d '(?P<payload>[^'\n]+)'$",
            re.MULTILINE,
        )

        for relative_path in ("README.md", "docs/operations.md"):
            _, bash_example, _ = self.client_example_fences(relative_path)
            calls = [
                (match.group("url"), json.loads(match.group("payload")))
                for match in curl_pattern.finditer(bash_example)
            ]
            with self.subTest(document=relative_path):
                self.assertEqual(expected_calls, calls)

    def test_both_guides_pair_powershell_models_bodies_and_endpoints(self):
        expected_chat_body = """$chat = @{
  model = 'gemma4:e4b'
  messages = @(@{ role = 'user'; content = 'Hello' })
  stream = $false
} | ConvertTo-Json -Depth 4"""
        expected_embed_body = (
            "$embed = @{ model = 'embeddinggemma:300m'; "
            "input = 'hello from the remote stack' } | ConvertTo-Json"
        )
        call_pattern = re.compile(
            r"^Invoke-RestMethod -Method Post -Uri '([^']+)' "
            r"-ContentType 'application/json' -Body \$(chat|embed)$",
            re.MULTILINE,
        )
        expected_calls = [
            ("http://127.0.0.1:11440/api/chat", "chat"),
            ("http://127.0.0.1:11441/api/embed", "embed"),
        ]

        for relative_path in ("README.md", "docs/operations.md"):
            _, _, powershell_example = self.client_example_fences(relative_path)
            with self.subTest(document=relative_path):
                self.assertIn(expected_chat_body, powershell_example)
                self.assertIn(expected_embed_body, powershell_example)
                self.assertEqual(expected_calls, call_pattern.findall(powershell_example))

    def test_both_guides_use_dynamodb_api_and_dummy_credentials_in_boto3_example(self):
        expected_keywords = {
            "endpoint_url": "http://127.0.0.1:18002",
            "region_name": "us-east-1",
            "aws_access_key_id": "local",
            "aws_secret_access_key": "local",
        }

        for relative_path in ("README.md", "docs/operations.md"):
            dynamodb_example, _, _ = self.client_example_fences(relative_path)
            tree = ast.parse(dynamodb_example)
            client_calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "boto3"
                and node.func.attr == "client"
            ]

            with self.subTest(document=relative_path):
                self.assertEqual(1, len(client_calls))
                client_call = client_calls[0]
                self.assertEqual(["dynamodb"], [ast.literal_eval(arg) for arg in client_call.args])
                keywords = {
                    keyword.arg: ast.literal_eval(keyword.value)
                    for keyword in client_call.keywords
                }
                self.assertEqual(expected_keywords, keywords)

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
            "cp remote.env.example remote.data.env",
            "cp remote.env.example remote.gpu.env",
            "./scripts/check.sh core vector dynamodb",
            "./scripts/check.sh inference",
            "./scripts/bootstrap.sh",
            "./scripts/deploy.sh core vector dynamodb",
            "./scripts/deploy.sh inference",
            "./scripts/tunnel.sh core vector dynamodb",
            "./scripts/tunnel.sh inference",
            "./scripts/stack.sh status",
            "./scripts/stack.sh logs",
            "./scripts/stack.sh stop search",
            "./scripts/stack.sh down",
            "./scripts/stack.sh destroy",
        )
        powershell_steps = (
            "git clone https://github.com/somanshusingla/remote-infra-stack.git",
            ".\\scripts\\init-env.ps1",
            "Copy-Item .\\remote.env.example .\\remote.data.env",
            "Copy-Item .\\remote.env.example .\\remote.gpu.env",
            ".\\scripts\\check.ps1 core vector dynamodb",
            ".\\scripts\\check.ps1 inference",
            ".\\scripts\\bootstrap.ps1",
            ".\\scripts\\deploy.ps1 core vector dynamodb",
            ".\\scripts\\deploy.ps1 inference",
            ".\\scripts\\tunnel.ps1 core vector dynamodb",
            ".\\scripts\\tunnel.ps1 inference",
            ".\\scripts\\stack.ps1 status",
            ".\\scripts\\stack.ps1 logs",
            ".\\scripts\\stack.ps1 stop search",
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

        self.assertIn("32 GiB does not guarantee that all profiles fit at peak", operations)
        self.assertIn("20 GiB", operations)
        self.assertIn("GPU host runs Ollama", operations)
        self.assertNotIn("48 GiB is the practical minimum", operations)
        self.assertNotIn("64 GiB is preferred", operations)

    def test_readme_links_data_and_inference_gcp_evidence(self):
        readme = self.read_document("README.md")

        self.assertIn(
            "[docs/verification/data-and-inference-gcp-smoke.md]"
            "(docs/verification/data-and-inference-gcp-smoke.md)",
            readme,
        )

    def test_gcp_smoke_evidence_records_acceptance_contract(self):
        evidence = self.read_document(
            "docs/verification/data-and-inference-gcp-smoke.md"
        )

        required_literals = (
            "high-mem-64-gb-us-east-1",
            "us-east1-c",
            "e2-highmem-8",
            "Ubuntu 26.04 LTS",
            "core vector dynamodb inference",
            "c74a84896266b9978764eeb3b8f42c344767ad28",
            "20260803T073926Z-c74a84896266-b791db2d7f6c467c953392ee7e92a6a4",
            "app-postgres",
            "app-redis",
            "chroma",
            "chroma-admin",
            "dynamodb-local",
            "dynamodb-admin",
            "ollama-llm",
            "ollama-embedding",
            "127.0.0.1:5432",
            "127.0.0.1:6379",
            "http://127.0.0.1:18000",
            "http://127.0.0.1:18001",
            "http://127.0.0.1:18002",
            "http://127.0.0.1:18003",
            "http://127.0.0.1:11440",
            "http://127.0.0.1:11441",
            "gemma4:e4b",
            "embeddinggemma:300m",
            "768",
            "cache reuse",
            "cleaned",
            "No secret values",
            "No ephemeral IP",
            "linux/amd64 child manifest",
            "docker.io/library/node:20.19.2-bookworm-slim@sha256:7cd3fbc830c75c92256fe1122002add9a1c025831af8770cd0bf8e45688ef661",
            "docker.io/amazon/dynamodb-local:3.3.0@sha256:d89f8fcc6b1a39cb35976c248ed42a28c66ae00dc043099210f5571e42648ab4",
            "docker.io/aaronshaf/dynamodb-admin:5.3.4@sha256:ac41724cd99706256d405a14a5fb96f51f18c41a630c84fa3357f900cbd16d2e",
            "docker.io/ollama/ollama:0.32.5@sha256:4dea9fb511947e24a84237bb636b0203abcb2ff0d3fbc7b4ff865deb91362131",
        )
        for literal in required_literals:
            with self.subTest(literal=literal):
                self.assertIn(literal, evidence)

        self.assertRegex(evidence, re.compile(r"six\s+named volumes"))
        self.assertRegex(
            evidence,
            re.compile(r"browser\s+automation was unavailable"),
        )

        recorded_ipv4_addresses = set(
            re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", evidence)
        )
        self.assertEqual({"127.0.0.1"}, recorded_ipv4_addresses)

    def test_readme_links_all_profiles_gcp_evidence(self):
        readme = self.read_document("README.md")

        self.assertIn(
            "[docs/verification/all-profiles-gcp-smoke.md]"
            "(docs/verification/all-profiles-gcp-smoke.md)",
            readme,
        )

    def test_all_profiles_gcp_evidence_records_ui_and_service_contract(self):
        evidence = self.read_document("docs/verification/all-profiles-gcp-smoke.md")

        required_literals = (
            "high-mem-64-gb-us-east-1",
            "us-east1-c",
            "e2-highmem-8",
            "core vector search observability tools dynamodb inference",
            "1dce504c48501c92472939c281888f89b5ee610b",
            "20260803T081220Z-1dce504c4850-9ce1f20c776746209ea4a82e9bfec671",
            "app-postgres",
            "app-redis",
            "chroma",
            "chroma-admin",
            "opensearch",
            "opensearch-dashboards",
            "langfuse-postgres",
            "langfuse-redis",
            "clickhouse",
            "minio",
            "langfuse-worker",
            "langfuse-web",
            "pgadmin",
            "redisinsight",
            "dynamodb-local",
            "dynamodb-admin",
            "ollama-llm",
            "ollama-embedding",
            "127.0.0.1:5432",
            "127.0.0.1:6379",
            "http://127.0.0.1:18000",
            "http://127.0.0.1:18001",
            "https://127.0.0.1:9200",
            "http://127.0.0.1:5601",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:9090",
            "http://127.0.0.1:9091",
            "http://127.0.0.1:5050",
            "http://127.0.0.1:5540",
            "http://127.0.0.1:18002",
            "http://127.0.0.1:18003",
            "http://127.0.0.1:11440",
            "http://127.0.0.1:11441",
            "[data-and-inference-gcp-smoke.md]"
            "(data-and-inference-gcp-smoke.md)",
            "OpenSearch Dashboards",
            "ELK-style",
            "authenticated cluster health",
            "disposable index",
            "Langfuse",
            "all 18 services healthy",
            "No secret values",
            "No ephemeral IP",
        )
        for literal in required_literals:
            with self.subTest(literal=literal):
                self.assertIn(literal, evidence)

        recorded_ipv4_addresses = set(
            re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", evidence)
        )
        self.assertEqual({"127.0.0.1"}, recorded_ipv4_addresses)


if __name__ == "__main__":
    unittest.main()
