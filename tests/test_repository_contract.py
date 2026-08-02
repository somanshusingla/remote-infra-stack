import json
import re
import tempfile
import unittest
from pathlib import Path

from tests.helpers import read_env, repo_path, validate_fixture_contracts


class RepositoryContractTests(unittest.TestCase):
    def test_required_root_files_exist(self):
        for name in ("compose.yaml", "versions.env", ".env.example", "remote.env.example"):
            self.assertTrue(repo_path(name).is_file(), name)

    def test_versions_match_verified_manifest_inventory(self):
        versions = read_env(repo_path("versions.env"))
        inventory_path = repo_path("tests/fixtures/verified-manifests.json")
        self.assertTrue(inventory_path.is_file(), "verified manifest inventory is required")
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        verified_images = inventory["images"]
        images = {key: value for key, value in versions.items() if key.endswith("_IMAGE")}

        self.assertEqual(17, len(images))
        self.assertEqual("gemma4:e4b", versions["OLLAMA_LLM_MODEL"])
        self.assertEqual("embeddinggemma:300m", versions["OLLAMA_EMBEDDING_MODEL"])
        self.assertEqual(17, len(verified_images))
        self.assertEqual(
            {name: record["reference"] for name, record in verified_images.items()},
            images,
        )
        for name, record in verified_images.items():
            self.assertRegex(name, r"^[A-Z][A-Z0-9_]*_IMAGE$")
            self.assertRegex(
                record["reference"],
                r"^[a-z0-9.-]+(?:/[a-z0-9._-]+)+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$",
            )
            self.assertEqual("manifest-list", record["kind"], name)
            self.assertEqual(["linux/amd64"], record["verified_platforms"], name)

    def test_chroma_admin_is_vendored_and_built_from_pinned_inputs(self):
        upstream = repo_path("vendor/chromadb-admin/UPSTREAM.md").read_text(encoding="utf-8")
        package = json.loads(repo_path("vendor/chromadb-admin/package.json").read_text())
        dockerfile = repo_path("images/chromadb-admin/Dockerfile").read_text()

        self.assertIn("efe867c86c78683d90b0eb74b88b351fc08f0b5f", upstream)
        self.assertEqual("^2.0.1", package["dependencies"]["chromadb"])
        self.assertTrue(repo_path("vendor/chromadb-admin/package-lock.json").is_file())
        self.assertTrue(repo_path("vendor/chromadb-admin/LICENSE.txt").is_file())
        self.assertIn("npm ci", dockerfile)
        self.assertIn("USER node", dockerfile)
        self.assertIn("EXPOSE 3001", dockerfile)
        self.assertNotRegex(dockerfile, r"(?m)^FROM\s+node:")

    def test_secret_files_are_ignored(self):
        ignored = repo_path(".gitignore").read_text(encoding="utf-8")
        self.assertRegex(ignored, r"(?m)^\.env$")
        self.assertRegex(ignored, r"(?m)^remote\.env$")
        self.assertIn(".artifacts/", ignored)

    def test_remote_scripts_are_forced_to_lf(self):
        attributes = repo_path(".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.sh text eol=lf", attributes)

    def test_committed_fixtures_match_their_example_contracts(self):
        validate_fixture_contracts(
            repo_path("tests/fixtures/stack.env"),
            repo_path("tests/fixtures/remote.env"),
        )

    def test_rejects_stack_fixture_with_a_missing_contract_key(self):
        with tempfile.TemporaryDirectory() as directory:
            stack_fixture = Path(directory) / "stack.env"
            stack_fixture.write_text(
                repo_path("tests/fixtures/stack.env")
                .read_text(encoding="utf-8")
                .replace("APP_REDIS_MEMORY=512m\n", ""),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "stack fixture key set"):
                validate_fixture_contracts(stack_fixture, repo_path("tests/fixtures/remote.env"))

    def test_rejects_remote_fixture_with_a_missing_contract_key(self):
        with tempfile.TemporaryDirectory() as directory:
            remote_fixture = Path(directory) / "remote.env"
            remote_fixture.write_text(
                repo_path("tests/fixtures/remote.env")
                .read_text(encoding="utf-8")
                .replace("LOCAL_REDIS_PORT=6379\n", ""),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "remote fixture key set"):
                validate_fixture_contracts(repo_path("tests/fixtures/stack.env"), remote_fixture)

    def test_rejects_opensearch_fixture_password_without_mixed_case(self):
        with tempfile.TemporaryDirectory() as directory:
            stack_fixture = Path(directory) / "stack.env"
            stack_fixture.write_text(
                repo_path("tests/fixtures/stack.env")
                .read_text(encoding="utf-8")
                .replace(
                    "OPENSEARCH_INITIAL_ADMIN_PASSWORD=AbCdEfGhIjKlMnOpQrStUvWxYz123456\n",
                    "OPENSEARCH_INITIAL_ADMIN_PASSWORD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "32 characters with mixed case"):
                validate_fixture_contracts(stack_fixture, repo_path("tests/fixtures/remote.env"))

    def test_rejects_langfuse_fixture_encryption_key_that_is_not_hexadecimal(self):
        with tempfile.TemporaryDirectory() as directory:
            stack_fixture = Path(directory) / "stack.env"
            stack_fixture.write_text(
                repo_path("tests/fixtures/stack.env")
                .read_text(encoding="utf-8")
                .replace(
                    "LANGFUSE_ENCRYPTION_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\n",
                    "LANGFUSE_ENCRYPTION_KEY=gggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggg\n",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "64 lowercase hexadecimal characters"):
                validate_fixture_contracts(stack_fixture, repo_path("tests/fixtures/remote.env"))


if __name__ == "__main__":
    unittest.main()
