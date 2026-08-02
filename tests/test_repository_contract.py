import re
import tempfile
import unittest
from pathlib import Path

from tests.helpers import read_env, repo_path, validate_fixture_contracts


class RepositoryContractTests(unittest.TestCase):
    def test_required_root_files_exist(self):
        for name in ("compose.yaml", "versions.env", ".env.example", "remote.env.example"):
            self.assertTrue(repo_path(name).is_file(), name)

    def test_versions_are_explicit_and_never_latest(self):
        versions = read_env(repo_path("versions.env"))
        self.assertGreaterEqual(len(versions), 12)
        for name, image in versions.items():
            self.assertRegex(name, r"_IMAGE$")
            self.assertNotRegex(image, r"(?::|@)latest(?:$|@)")
            self.assertRegex(image, r"[:@]")

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
