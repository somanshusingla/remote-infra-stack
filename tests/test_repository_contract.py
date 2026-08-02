import re
import unittest

from tests.helpers import read_env, repo_path


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


if __name__ == "__main__":
    unittest.main()
