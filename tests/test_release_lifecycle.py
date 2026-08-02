import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import repo_path
from tests.test_remote_runtime import BASH_BIN, shell_path, usable_bash, write_test_env


@unittest.skipUnless(usable_bash(), "requires a usable Bash")
class ReleaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "stack root;safe"
        for child in ("incoming", "runtime", "releases"):
            (self.root / child).mkdir(parents=True, exist_ok=True)
        write_test_env(self.root / "runtime/.env")
        self.log = self.root / "docker.log"
        self.env = {
            **os.environ,
            "DOCKER_BIN": shell_path(repo_path("tests/fakes/docker")),
            "CURL_BIN": shell_path(repo_path("tests/fakes/docker")),
            "STACK_DOCKER_LOG": shell_path(self.log),
        }

    def tearDown(self):
        self.temp.cleanup()

    def make_archive(self, name="20260802T120000Z-a1b2c3d", unsafe_link=False):
        archive = self.root / "incoming" / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for relative in (
                "compose.yaml", "versions.env", "scripts/remote/compose.sh",
                "scripts/remote/health.sh", "scripts/remote/stack.sh",
            ):
                output.add(repo_path(relative), arcname=relative)
            if unsafe_link:
                info = tarfile.TarInfo("escape-link")
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                output.addfile(info)
        checksum = self.root / "incoming" / f"{name}.sha256"
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        return archive, checksum, name

    def deploy(self, archive, checksum, profiles="core", **env):
        return subprocess.run([
            BASH_BIN, shell_path(repo_path("scripts/remote/deploy-release.sh")),
            "--root", shell_path(self.root), "--archive", shell_path(archive),
            "--checksum", shell_path(checksum), "--profiles", profiles,
        ], env={**self.env, **env}, capture_output=True, text=True)

    def set_current(self, name):
        release = self.root / "releases" / name
        release.mkdir()
        (release / ".successful").touch()
        os.symlink(f"releases/{name}", self.root / "current")

    def test_checksum_mismatch_fails_before_extraction_or_docker(self):
        archive, checksum, name = self.make_archive()
        self.set_current("20260802T010000Z-current")
        checksum.write_text(f"{'0' * 64}  {archive.name}\n", encoding="ascii")
        result = self.deploy(archive, checksum)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "releases" / name).exists())
        self.assertFalse(self.log.exists())
        self.assertEqual("releases/20260802T010000Z-current", os.readlink(self.root / "current"))

    def test_success_activates_atomically_and_prunes_only_old_successes(self):
        self.set_current("20260802T090000Z-oldcurrent")
        for name in ("20260802T100000Z-old", "20260802T110000Z-keep"):
            release = self.root / "releases" / name
            release.mkdir()
            (release / ".successful").touch()
        failed = self.root / "releases/20260802T080000Z-failed"
        failed.mkdir()
        archive, checksum, name = self.make_archive()
        result = self.deploy(archive, checksum, "core,vector")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(f"releases/{name}", os.readlink(self.root / "current"))
        self.assertTrue((self.root / "releases" / name / ".successful").exists())
        self.assertFalse((self.root / "releases/20260802T090000Z-oldcurrent").exists())
        self.assertTrue((self.root / "releases/20260802T100000Z-old").exists())
        self.assertTrue((self.root / "releases/20260802T110000Z-keep").exists())
        self.assertTrue(failed.exists())
        calls = self.log.read_text(encoding="utf-8")
        for operation in ("config", "pull", "up", "ps"):
            self.assertIn(operation, calls)
        self.assertEqual(0o600, (self.root / "runtime/.env").stat().st_mode & 0o777)

    def test_config_pull_up_and_health_failures_preserve_current_and_do_not_prune(self):
        for failure, env in (
            ("config", {"STACK_FAKE_FAIL_COMMAND": "config"}),
            ("pull", {"STACK_FAKE_FAIL_COMMAND": "pull"}),
            ("up", {"STACK_FAKE_FAIL_COMMAND": "up"}),
            ("health", {"STACK_FAKE_PS_JSON": '[{"Service":"app-postgres","State":"running","Health":"unhealthy"},{"Service":"app-redis","State":"running","Health":"healthy"}]'}),
        ):
            with self.subTest(failure=failure):
                case_root = self.root / failure
                for child in ("incoming", "runtime", "releases"):
                    (case_root / child).mkdir(parents=True, exist_ok=True)
                write_test_env(case_root / "runtime/.env")
                old = case_root / "releases/20260802T010000Z-current"
                old.mkdir()
                (old / ".successful").touch()
                prune_candidate = case_root / "releases/20260802T000000Z-prune-me"
                prune_candidate.mkdir()
                (prune_candidate / ".successful").touch()
                os.symlink("releases/20260802T010000Z-current", case_root / "current")
                original_root, self.root = self.root, case_root
                try:
                    archive, checksum, name = self.make_archive(f"20260802T12{failure[:2]}00Z-new")
                    result = self.deploy(archive, checksum, **env)
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual("releases/20260802T010000Z-current", os.readlink(case_root / "current"))
                    self.assertTrue(prune_candidate.exists())
                    self.assertTrue((case_root / "releases" / name).exists())
                finally:
                    self.root = original_root

    def test_rejects_paths_outside_root_and_traversing_checksum_record(self):
        archive, checksum, name = self.make_archive()
        outside = Path(self.temp.name) / "outside.tar.gz"
        shutil.copyfile(archive, outside)
        result = self.deploy(outside, checksum)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "releases" / name).exists())
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksum.write_text(f"{digest}  ../{archive.name}\n", encoding="ascii")
        result = self.deploy(archive, checksum)
        self.assertNotEqual(0, result.returncode)

    def test_rejects_archive_symlink_that_resolves_outside_release(self):
        archive, checksum, name = self.make_archive("20260802T130000Z-symlink", unsafe_link=True)
        result = self.deploy(archive, checksum)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((Path(self.temp.name) / "outside").exists())
        self.assertFalse((self.root / "current").exists())

    def test_deployment_lock_rejects_a_concurrent_receiver(self):
        archive, checksum, _ = self.make_archive()
        first = subprocess.Popen([
            BASH_BIN, shell_path(repo_path("scripts/remote/deploy-release.sh")),
            "--root", shell_path(self.root), "--archive", shell_path(archive),
            "--checksum", shell_path(checksum), "--profiles", "core",
        ], env={**self.env, "STACK_FAKE_HOLD_SECONDS": "3"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.5)
            second = self.deploy(archive, checksum)
            self.assertNotEqual(0, second.returncode)
            self.assertIn("deployment", second.stderr.lower())
        finally:
            first.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
