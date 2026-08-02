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

    def test_bash_generator_refuses_a_destination_created_during_generation(self):
        shell = shutil.which("bash")
        if not shell:
            self.skipTest("bash is not installed")
        if subprocess.run([shell, "--version"], capture_output=True).returncode != 0:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".env"
            complete = root / "collision-complete"
            bin_directory = root / "bin"
            bin_directory.mkdir()
            openssl = bin_directory / "openssl"
            openssl.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ ! -e \"$INIT_ENV_TEST_COLLISION_DONE\" ]]; then\n"
                "  : > \"$INIT_ENV_TEST_COLLISION_DONE\"\n"
                "  printf '%s\\n' 'created-during-generation' > \"$INIT_ENV_TEST_COLLISION_OUTPUT\"\n"
                "fi\n"
                "printf '%0*d\\n' \"$(( $3 * 2 ))\" 0\n",
                encoding="utf-8",
            )
            openssl.chmod(openssl.stat().st_mode | stat.S_IXUSR)
            environment = os.environ | {
                "PATH": f"{bin_directory}{os.pathsep}{os.environ['PATH']}",
                "INIT_ENV_TEST_COLLISION_DONE": str(complete),
                "INIT_ENV_TEST_COLLISION_OUTPUT": str(output),
            }

            result = subprocess.run(
                [shell, str(repo_path("scripts/init-env.sh")), "--output", str(output)],
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertEqual("created-during-generation\n", output.read_text(encoding="utf-8"))

    def test_bash_generator_refuses_a_directory_created_during_generation(self):
        shell = shutil.which("bash")
        if not shell:
            self.skipTest("bash is not installed")
        if subprocess.run([shell, "--version"], capture_output=True).returncode != 0:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".env"
            complete = root / "collision-complete"
            bin_directory = root / "bin"
            bin_directory.mkdir()
            openssl = bin_directory / "openssl"
            openssl.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ ! -e \"$INIT_ENV_TEST_COLLISION_DONE\" ]]; then\n"
                "  : > \"$INIT_ENV_TEST_COLLISION_DONE\"\n"
                "  mkdir \"$INIT_ENV_TEST_COLLISION_OUTPUT\"\n"
                "fi\n"
                "printf '%0*d\\n' \"$(( $3 * 2 ))\" 0\n",
                encoding="utf-8",
            )
            openssl.chmod(openssl.stat().st_mode | stat.S_IXUSR)
            environment = os.environ | {
                "PATH": f"{bin_directory}{os.pathsep}{os.environ['PATH']}",
                "INIT_ENV_TEST_COLLISION_DONE": str(complete),
                "INIT_ENV_TEST_COLLISION_OUTPUT": str(output),
            }

            result = subprocess.run(
                [shell, str(repo_path("scripts/init-env.sh")), "--output", str(output)],
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertTrue(output.is_dir())
            self.assertEqual([], list(output.iterdir()))

    def test_bash_generator_cleans_up_a_directory_race_during_publication(self):
        shell = shutil.which("bash")
        real_ln = shutil.which("ln")
        if not shell:
            self.skipTest("bash is not installed")
        if not real_ln:
            self.skipTest("ln is not installed")
        if subprocess.run([shell, "--version"], capture_output=True).returncode != 0:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / ".env"
            complete = root / "collision-complete"
            bin_directory = root / "bin"
            bin_directory.mkdir()
            openssl = bin_directory / "openssl"
            openssl.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%0*d\\n' \"$(( $3 * 2 ))\" 0\n",
                encoding="utf-8",
            )
            ln = bin_directory / "ln"
            ln.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ ! -e \"$INIT_ENV_TEST_COLLISION_DONE\" ]]; then\n"
                "  : > \"$INIT_ENV_TEST_COLLISION_DONE\"\n"
                "  mkdir \"$2\"\n"
                "fi\n"
                "exec \"$INIT_ENV_REAL_LN\" \"$@\"\n",
                encoding="utf-8",
            )
            for executable in (openssl, ln):
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            environment = os.environ | {
                "PATH": f"{bin_directory}{os.pathsep}{os.environ['PATH']}",
                "INIT_ENV_REAL_LN": real_ln,
                "INIT_ENV_TEST_COLLISION_DONE": str(complete),
            }

            result = subprocess.run(
                [shell, str(repo_path("scripts/init-env.sh")), "--output", str(output)],
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(0, result.returncode)
            self.assertTrue(output.is_dir())
            self.assertEqual([], list(output.iterdir()))

    def test_bash_generator_writes_a_leading_dash_filename_in_a_temporary_directory(self):
        shell = shutil.which("bash")
        if not shell:
            self.skipTest("bash is not installed")
        if subprocess.run([shell, "--version"], capture_output=True).returncode != 0:
            self.skipTest("bash is not available")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "-generated.env"
            result = subprocess.run(
                [shell, str(repo_path("scripts/init-env.sh")), "--output", output.name],
                capture_output=True,
                text=True,
                cwd=directory,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assert_contract(output)

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
