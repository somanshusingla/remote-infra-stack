import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import read_env, read_env_keys, repo_path


class EnvGenerationTests(unittest.TestCase):
    def assert_contract(self, output: Path) -> None:
        generated = read_env(output)
        expected = read_env(repo_path(".env.example"))

        self.assertEqual(set(expected), set(generated))
        self.assertEqual(read_env_keys(repo_path(".env.example")), read_env_keys(output))
        self.assertNotIn("GENERATED_BY_INIT_ENV", generated.values())
        self.assertRegex(generated["LANGFUSE_ENCRYPTION_KEY"], r"^[0-9a-f]{64}$")
        self.assertEqual("app", generated["APP_POSTGRES_USER"])
        self.assertEqual("admin@example.local", generated["PGADMIN_DEFAULT_EMAIL"])

        opensearch_password = generated["OPENSEARCH_INITIAL_ADMIN_PASSWORD"]
        self.assertEqual(32, len(opensearch_password))
        self.assertTrue(any(character.islower() for character in opensearch_password))
        self.assertTrue(any(character.isupper() for character in opensearch_password))
        self.assertTrue(any(character.isdigit() for character in opensearch_password))
        self.assertIn("!", opensearch_password)

        if os.name != "nt":
            self.assertEqual(0, stat.S_IMODE(output.stat().st_mode) & 0o077)

    def assert_refuses_then_forces(
        self, command: list[str], output: Path, force_argument: str
    ) -> None:
        original = output.read_text(encoding="utf-8")
        refusal = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(0, refusal.returncode)
        self.assertIn("Refusing to overwrite", refusal.stderr)
        self.assertEqual(original, output.read_text(encoding="utf-8"))

        forced = subprocess.run(
            command + [force_argument], capture_output=True, text=True
        )
        self.assertEqual(0, forced.returncode, forced.stderr)
        self.assertNotEqual(original, output.read_text(encoding="utf-8"))
        self.assert_contract(output)

    def test_bash_generator(self):
        shell = shutil.which("bash")
        if not shell:
            self.skipTest("bash is not installed")
        if subprocess.run([shell, "--version"], capture_output=True).returncode != 0:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / ".env"
            command = [
                shell,
                str(repo_path("scripts/init-env.sh")),
                "--output",
                str(output),
            ]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, first.returncode, first.stderr)
            self.assert_contract(output)
            self.assert_refuses_then_forces(command, output, "--force")

    def test_powershell_generator(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            self.skipTest("PowerShell is not installed")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / ".env"
            command = [
                shell,
                "-NoProfile",
                "-File",
                str(repo_path("scripts/init-env.ps1")),
                "-OutputPath",
                str(output),
            ]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, first.returncode, first.stderr)
            self.assert_contract(output)
            self.assert_refuses_then_forces(command, output, "-Force")


if __name__ == "__main__":
    unittest.main()
