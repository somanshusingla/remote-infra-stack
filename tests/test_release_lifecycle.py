import hashlib
import io
import os
import shlex
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

    def make_archive(self, name="20260802T120000Z-a1b2c3d", extra_members=()):
        archive = self.root / "incoming" / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for relative in (
                "compose.yaml", "versions.env", "scripts/remote/compose.sh",
                "scripts/remote/health.sh", "scripts/remote/stack.sh",
            ):
                output.add(repo_path(relative), arcname=relative)
            for info, content in extra_members:
                output.addfile(info, io.BytesIO(content) if content is not None else None)
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
        self.mark_success(release)
        os.symlink(f"releases/{name}", self.root / "current")

    @staticmethod
    def mark_success(release):
        (release / ".release-digest").write_text(f"{'a' * 64}\n", encoding="ascii")
        (release / ".successful").touch()

    @staticmethod
    def regular_member(name, content=b"fixture"):
        info = tarfile.TarInfo(name)
        info.size = len(content)
        info.mode = 0o644
        return info, content

    def assert_no_private_staging(self):
        self.assertEqual([], [
            path.name for path in (self.root / "runtime").iterdir()
            if path.name.startswith(".deploy-")
        ])

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
            self.mark_success(release)
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
                self.mark_success(old)
                prune_candidate = case_root / "releases/20260802T000000Z-prune-me"
                prune_candidate.mkdir()
                self.mark_success(prune_candidate)
                os.symlink("releases/20260802T010000Z-current", case_root / "current")
                original_root, self.root = self.root, case_root
                try:
                    archive, checksum, name = self.make_archive(f"20260802T12{failure[:2]}00Z-new")
                    result = self.deploy(archive, checksum, **env)
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual("releases/20260802T010000Z-current", os.readlink(case_root / "current"))
                    self.assertTrue(prune_candidate.exists())
                    self.assertTrue((case_root / "releases" / name).exists())
                    self.assertFalse((case_root / "releases" / name / ".successful").exists())
                    self.assertTrue((case_root / "releases" / name / ".release-digest").is_file())
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

    def test_rejects_all_link_and_special_member_types_before_extraction(self):
        cases = []
        for label, member_type in (
            ("symlink", tarfile.SYMTYPE),
            ("hardlink", tarfile.LNKTYPE),
            ("fifo", tarfile.FIFOTYPE),
            ("character", tarfile.CHRTYPE),
            ("block", tarfile.BLKTYPE),
            ("socket", b"s"),
        ):
            info = tarfile.TarInfo(f"unsafe-{label}")
            info.type = member_type
            if member_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = "compose.yaml"
            if member_type in (tarfile.CHRTYPE, tarfile.BLKTYPE):
                info.devmajor, info.devminor = 1, 3
            cases.append((label, (info, None)))
        for index, (label, member) in enumerate(cases):
            with self.subTest(member_type=label):
                archive, checksum, name = self.make_archive(
                    f"20260802T13{index:02d}00Z-{label}", (member,)
                )
                result = self.deploy(archive, checksum)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse((self.root / "releases" / name).exists())
                self.assert_no_private_staging()

    def test_rejects_unsafe_names_and_deployer_markers_before_extraction(self):
        names = (
            "../escape", "/absolute", "control\nname", "control\x01name",
            ".successful", ".release-digest", "nested/.successful",
            "nested/.release-digest",
        )
        for index, member_name in enumerate(names):
            with self.subTest(member_name=repr(member_name)):
                archive, checksum, name = self.make_archive(
                    f"20260802T14{index:02d}00Z-name", (self.regular_member(member_name),)
                )
                result = self.deploy(archive, checksum)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse((self.root / "releases" / name).exists())
                self.assert_no_private_staging()

    def test_runtime_releases_and_lock_cannot_redirect_deployment(self):
        for child in ("runtime", "releases"):
            with self.subTest(child=child):
                case_root = self.root / f"symlink-{child}"
                (case_root / "incoming").mkdir(parents=True)
                outside = self.root / f"outside-{child}"
                outside.mkdir()
                if child == "runtime":
                    write_test_env(outside / ".env")
                    (case_root / "releases").mkdir()
                else:
                    (case_root / "runtime").mkdir()
                    write_test_env(case_root / "runtime/.env")
                outside_before = {
                    path.name: path.read_bytes() for path in outside.iterdir() if path.is_file()
                }
                os.symlink(outside, case_root / child, target_is_directory=True)
                original_root, self.root = self.root, case_root
                try:
                    archive, checksum, _ = self.make_archive(f"20260802T150000Z-{child}")
                    result = self.deploy(archive, checksum)
                    self.assertNotEqual(0, result.returncode)
                    outside_after = {
                        path.name: path.read_bytes() for path in outside.iterdir() if path.is_file()
                    }
                    self.assertEqual(outside_before, outside_after)
                finally:
                    self.root = original_root

        sentinel = self.root / "outside-lock-sentinel"
        sentinel.write_text("must-not-change\n", encoding="ascii")
        os.symlink(sentinel, self.root / "runtime/deploy.lock")
        archive, checksum, _ = self.make_archive("20260802T151000Z-lock")
        result = self.deploy(archive, checksum)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("must-not-change\n", sentinel.read_text(encoding="ascii"))

    def test_source_replacement_after_snapshot_cannot_change_release(self):
        archive, checksum, name = self.make_archive("20260802T160000Z-snapshot")
        original_compose = repo_path("compose.yaml").read_bytes()
        sha_wrapper = self.root / "snapshot-sha256sum"
        real_sha = shutil.which("sha256sum")
        self.assertIsNotNone(real_sha)
        sha_wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf replaced >\"$STACK_MUTABLE_SOURCE\"\n"
            f"exec {shlex.quote(real_sha)} \"$@\"\n",
            encoding="utf-8",
        )
        sha_wrapper.chmod(0o755)
        result = self.deploy(
            archive, checksum,
            SHA256SUM_BIN=shell_path(sha_wrapper),
            STACK_MUTABLE_SOURCE=shell_path(archive),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"replaced", archive.read_bytes())
        self.assertEqual(original_compose, (self.root / "releases" / name / "compose.yaml").read_bytes())

    def test_identical_retry_resumes_retained_release_but_collision_is_rejected(self):
        archive, checksum, name = self.make_archive("20260802T170000Z-retry")
        first = self.deploy(archive, checksum, STACK_FAKE_FAIL_COMMAND="config")
        self.assertNotEqual(0, first.returncode)
        release = self.root / "releases" / name
        self.assertTrue((release / ".release-digest").is_file())
        self.assertFalse((release / ".successful").exists())

        second = self.deploy(archive, checksum)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(f"releases/{name}", os.readlink(self.root / "current"))

        collision_archive, collision_checksum, _ = self.make_archive(
            "20260802T170000Z-retry", (self.regular_member("different"),)
        )
        calls_before = self.log.read_text(encoding="utf-8").count("\n")
        collision = self.deploy(collision_archive, collision_checksum)
        self.assertNotEqual(0, collision.returncode)
        self.assertIn("collision", collision.stderr.lower())
        self.assertEqual(calls_before, self.log.read_text(encoding="utf-8").count("\n"))

    def test_pruning_keeps_older_named_active_and_ignores_unexpected_symlink(self):
        self.set_current("20260802T200000Z-previous")
        for name in (
            "20260802T210000Z-one", "20260802T220000Z-two", "20260802T230000Z-three"
        ):
            release = self.root / "releases" / name
            release.mkdir()
            self.mark_success(release)
        outside = self.root / "outside-release"
        outside.mkdir()
        (outside / ".successful").touch()
        os.symlink(outside, self.root / "releases/unexpected-link", target_is_directory=True)

        archive, checksum, active = self.make_archive("20260802T190000Z-active")
        result = self.deploy(archive, checksum)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(f"releases/{active}", os.readlink(self.root / "current"))
        kept = {
            path.name for path in (self.root / "releases").iterdir()
            if path.is_dir() and not path.is_symlink() and (path / ".successful").is_file()
        }
        self.assertEqual({active, "20260802T220000Z-two", "20260802T230000Z-three"}, kept)
        self.assertTrue((self.root / "releases/unexpected-link").is_symlink())
        self.assertTrue((outside / ".successful").exists())

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
