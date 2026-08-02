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
        self.env_counter = 0
        self.log = self.root / "docker.log"
        self.env = {
            **os.environ,
            "PATH": f"{shell_path(repo_path('tests/fakes'))}{os.pathsep}{os.environ.get('PATH', '')}",
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
                "config/opensearch/opensearch.yml",
                "config/opensearch/docker-entrypoint.sh",
            ):
                output.add(repo_path(relative), arcname=relative)
            for info, content in extra_members:
                output.addfile(info, io.BytesIO(content) if content is not None else None)
        checksum = self.root / "incoming" / f"{name}.sha256"
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        return archive, checksum, name

    def make_incoming_env(self, content=None):
        self.env_counter += 1
        incoming_env = self.root / "incoming" / f"runtime-env-{self.env_counter:04d}"
        if content is None:
            content = (self.root / "runtime/.env").read_bytes()
        if isinstance(content, str):
            content = content.encode("utf-8")
        incoming_env.write_bytes(content)
        return incoming_env

    def deploy(self, archive, checksum, profiles="core", runtime_env=None, **env):
        if runtime_env is None:
            runtime_env = self.make_incoming_env()
        return subprocess.run([
            BASH_BIN, shell_path(repo_path("scripts/remote/deploy-release.sh")),
            "--root", shell_path(self.root), "--archive", shell_path(archive),
            "--checksum", shell_path(checksum), "--env", shell_path(runtime_env),
            "--profiles", profiles,
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

    def make_env_swap_copy(self, mode):
        wrapper = self.root / f"swap-{mode}-cp"
        real_copy = shutil.which("cp")
        self.assertIsNotNone(real_copy)
        if mode == "leaf":
            mutation = (
                'mv -- "$STACK_RUNTIME_ENV" "$STACK_RUNTIME_ENV.original"\n'
                'ln -s -- "$STACK_REDIRECT_ENV" "$STACK_RUNTIME_ENV"\n'
            )
        else:
            mutation = (
                'mv -- "$STACK_RUNTIME_DIR" "$STACK_RUNTIME_DIR.original"\n'
                'mkdir -- "$STACK_RUNTIME_DIR"\n'
                'ln -s -- "$STACK_REDIRECT_ENV" "$STACK_RUNTIME_ENV"\n'
            )
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ ! -e "$STACK_SWAP_MARKER" ]]; then\n'
            f"{mutation}"
            '  : >"$STACK_SWAP_MARKER"\n'
            "fi\n"
            f"exec {shlex.quote(real_copy)} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def assert_private_runtime_env_used(self):
        calls = [
            shlex.split(line)
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if " --env-file " in line
        ]
        runtime_envs = []
        for call in calls:
            indices = [index for index, value in enumerate(call) if value == "--env-file"]
            self.assertEqual(2, len(indices))
            runtime_envs.append(call[indices[1] + 1])
        self.assertEqual(1, len(set(runtime_envs)))
        snapshot = runtime_envs[0]
        self.assertIn(".deploy-", snapshot)
        self.assertTrue(snapshot.endswith("/runtime.env"), snapshot)
        self.assertNotEqual(shell_path(self.root / "runtime/.env"), snapshot)

    def make_release_leaf_swap_move(self, relative, replacement):
        wrapper = self.root / f"swap-{relative.replace('/', '-')}-mv"
        real_move = shutil.which("mv")
        self.assertIsNotNone(real_move)
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'source_path=${@: -2:1}\n'
            'destination=${@: -1}\n'
            f"{shlex.quote(real_move)} \"$@\"\n"
            'if [[ "$source_path" == */extracted ]]; then\n'
            f"  leaf=\"$destination/{relative}\"\n"
            '  mv -- "$leaf" "$leaf.verified"\n'
            f"  printf %s {shlex.quote(replacement)} >\"$leaf\"\n"
            "fi\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def test_checksum_mismatch_fails_before_extraction_or_docker(self):
        archive, checksum, name = self.make_archive()
        self.set_current("20260802T010000Z-current")
        previous_env = (self.root / "runtime/.env").read_bytes()
        incoming_env = self.make_incoming_env(previous_env + b"PAIR_MARKER=new\n")
        checksum.write_text(f"{'0' * 64}  {archive.name}\n", encoding="ascii")
        result = self.deploy(archive, checksum, runtime_env=incoming_env)
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.root / "releases" / name).exists())
        self.assertFalse(self.log.exists())
        self.assertEqual("releases/20260802T010000Z-current", os.readlink(self.root / "current"))
        self.assertEqual(previous_env, (self.root / "runtime/.env").read_bytes())
        self.assertFalse(incoming_env.exists())

    def test_requires_direct_regular_incoming_environment(self):
        archive, checksum, _ = self.make_archive("20260802T120100Z-env-input")
        command = [
            BASH_BIN, shell_path(repo_path("scripts/remote/deploy-release.sh")),
            "--root", shell_path(self.root), "--archive", shell_path(archive),
            "--checksum", shell_path(checksum), "--profiles", "core",
        ]
        missing = subprocess.run(
            command, env=self.env, capture_output=True, text=True
        )
        self.assertNotEqual(0, missing.returncode)
        self.assertIn("--env", missing.stderr)

        outside = Path(self.temp.name) / "outside.env"
        outside.write_bytes((self.root / "runtime/.env").read_bytes())
        rejected = self.deploy(archive, checksum, runtime_env=outside)
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("incoming", rejected.stderr.lower())
        self.assertTrue(outside.exists())

        target = self.make_incoming_env()
        link = self.root / "incoming/runtime-env-link"
        os.symlink(target.name, link)
        rejected = self.deploy(archive, checksum, runtime_env=link)
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("non-symlink", rejected.stderr.lower())
        self.assertTrue(target.exists())

    def test_success_installs_exact_incoming_environment_atomically(self):
        archive, checksum, _ = self.make_archive("20260802T120200Z-env-install")
        incoming = self.make_incoming_env(
            (self.root / "runtime/.env").read_bytes() + b"PAIR_MARKER=exact\n"
        )
        expected = incoming.read_bytes()
        result = self.deploy(archive, checksum, runtime_env=incoming)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, (self.root / "runtime/.env").read_bytes())
        self.assertEqual(0o600, (self.root / "runtime/.env").stat().st_mode & 0o777)
        self.assertFalse(incoming.exists())
        self.assert_private_runtime_env_used()

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

    def test_compose_project_directory_uses_canonical_host_release_path(self):
        archive, checksum, name = self.make_archive("20260802T121000Z-canonical")
        render_log = self.root / "rendered-paths.log"
        result = self.deploy(
            archive, checksum, STACK_FAKE_RENDER_LOG=shell_path(render_log)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        release = str((self.root / "releases" / name).resolve())
        paths = render_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual([release], paths)
        self.assertNotIn("/proc/self/fd", "\n".join(paths))

    def test_post_install_leaf_swaps_cannot_change_verified_inputs(self):
        expected_hashes = {
            relative: hashlib.sha256(repo_path(relative).read_bytes()).hexdigest()
            for relative in (
                "compose.yaml", "versions.env",
                "config/opensearch/opensearch.yml",
                "config/opensearch/docker-entrypoint.sh",
            )
        }
        leaves = (
            "scripts/remote/compose.sh",
            "scripts/remote/health.sh",
            "compose.yaml",
            "versions.env",
            "config/opensearch/opensearch.yml",
            "config/opensearch/docker-entrypoint.sh",
        )
        for index, relative in enumerate(leaves):
            with self.subTest(relative=relative):
                marker = self.root / f"executed-{index}"
                if relative.endswith(".sh"):
                    replacement = (
                        "#!/usr/bin/env bash\n"
                        f": >{shlex.quote(shell_path(marker))}\n"
                        "exit 91\n"
                    )
                else:
                    replacement = f"tampered {relative}\n"
                move = self.make_release_leaf_swap_move(relative, replacement)
                archive, checksum, _ = self.make_archive(
                    f"20260802T124{index}00Z-leaf-{index}"
                )
                content_log = self.root / f"content-{index}.log"
                result = self.deploy(
                    archive, checksum, "search", MV_BIN=shell_path(move),
                    STACK_FAKE_CONTENT_LOG=shell_path(content_log),
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertFalse(marker.exists())
                actual = dict(
                    line.split(" ", 1)
                    for line in content_log.read_text(encoding="ascii").splitlines()
                )
                self.assertEqual(expected_hashes, actual)

    def test_runtime_env_leaf_swap_uses_held_private_snapshot(self):
        redirect_env = self.root / "redirect.env"
        write_test_env(redirect_env)
        wrapper = self.make_env_swap_copy("leaf")
        archive, checksum, _ = self.make_archive("20260802T122000Z-env-leaf")
        incoming_env = self.make_incoming_env(
            (self.root / "runtime/.env").read_bytes() + b"PAIR_MARKER=held-leaf\n"
        )
        expected = incoming_env.read_bytes()
        result = self.deploy(
            archive, checksum, "search", runtime_env=incoming_env,
            CP_BIN=shell_path(wrapper),
            STACK_RUNTIME_DIR=shell_path(self.root / "incoming"),
            STACK_RUNTIME_ENV=shell_path(incoming_env),
            STACK_REDIRECT_ENV=shell_path(redirect_env),
            STACK_SWAP_MARKER=shell_path(self.root / "leaf-swapped"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, (self.root / "runtime/.env").read_bytes())
        self.assertFalse((self.root / "runtime/.env").is_symlink())
        self.assert_private_runtime_env_used()

    def test_runtime_directory_swap_uses_held_private_snapshot(self):
        redirect_env = self.root / "redirect.env"
        write_test_env(redirect_env)
        wrapper = self.make_env_swap_copy("directory")
        archive, checksum, _ = self.make_archive("20260802T123000Z-env-directory")
        incoming_env = self.make_incoming_env(
            (self.root / "runtime/.env").read_bytes() + b"PAIR_MARKER=held-directory\n"
        )
        expected = incoming_env.read_bytes()
        result = self.deploy(
            archive, checksum, "search", runtime_env=incoming_env,
            CP_BIN=shell_path(wrapper),
            STACK_RUNTIME_DIR=shell_path(self.root / "incoming"),
            STACK_RUNTIME_ENV=shell_path(incoming_env),
            STACK_REDIRECT_ENV=shell_path(redirect_env),
            STACK_SWAP_MARKER=shell_path(self.root / "directory-swapped"),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, (self.root / "runtime/.env").read_bytes())
        self.assertFalse((self.root / "runtime/.env").is_symlink())
        self.assertTrue((self.root / "incoming.original").is_dir())
        self.assert_private_runtime_env_used()

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

        tampered_marker = self.root / "tampered-script-executed"
        (release / "scripts/remote/compose.sh").write_text(
            "#!/usr/bin/env bash\n"
            f": >{shlex.quote(shell_path(tampered_marker))}\n"
            "exit 91\n",
            encoding="utf-8",
        )

        second = self.deploy(archive, checksum)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(f"releases/{name}", os.readlink(self.root / "current"))
        self.assertFalse(tampered_marker.exists())
        self.assertEqual(
            repo_path("scripts/remote/compose.sh").read_bytes(),
            (release / "scripts/remote/compose.sh").read_bytes(),
        )

        successful_content = (release / "compose.yaml").read_bytes()
        calls_before_successful_retry = self.log.read_text(encoding="utf-8").count("\n")
        successful_retry = self.deploy(archive, checksum)
        self.assertNotEqual(0, successful_retry.returncode)
        self.assertIn("successful", successful_retry.stderr.lower())
        self.assertEqual(successful_content, (release / "compose.yaml").read_bytes())
        self.assertEqual(
            calls_before_successful_retry,
            self.log.read_text(encoding="utf-8").count("\n"),
        )

        collision_archive, collision_checksum, _ = self.make_archive(
            "20260802T170000Z-retry", (self.regular_member("different"),)
        )
        calls_before = self.log.read_text(encoding="utf-8").count("\n")
        collision = self.deploy(collision_archive, collision_checksum)
        self.assertNotEqual(0, collision.returncode)
        self.assertIn("collision", collision.stderr.lower())
        self.assertEqual(calls_before, self.log.read_text(encoding="utf-8").count("\n"))

    def test_failed_atomic_exchange_preserves_retained_inode_and_content(self):
        archive, checksum, name = self.make_archive("20260802T170500Z-exchange-fails")
        first = self.deploy(archive, checksum, STACK_FAKE_FAIL_COMMAND="config")
        self.assertNotEqual(0, first.returncode)
        release = self.root / "releases" / name
        retained_script = release / "scripts/remote/compose.sh"
        retained_script.write_text("retained-content\n", encoding="ascii")
        before = (release.stat().st_dev, release.stat().st_ino, retained_script.read_bytes())
        python_failure = self.root / "python-exchange-failure"
        python_failure.write_text("#!/usr/bin/env bash\nexit 88\n", encoding="ascii")
        python_failure.chmod(0o755)
        calls_before = self.log.read_bytes()

        retry = self.deploy(
            archive, checksum, PYTHON_BIN=shell_path(python_failure)
        )

        self.assertNotEqual(0, retry.returncode)
        after = (release.stat().st_dev, release.stat().st_ino, retained_script.read_bytes())
        self.assertEqual(before, after)
        self.assertEqual(calls_before, self.log.read_bytes())
        self.assertFalse((self.root / "current").exists())
        self.assert_no_private_staging()

    def test_successful_retry_never_makes_release_path_absent(self):
        archive, checksum, name = self.make_archive("20260802T170600Z-exchange-present")
        first = self.deploy(archive, checksum, STACK_FAKE_FAIL_COMMAND="config")
        self.assertNotEqual(0, first.returncode)
        release = self.root / "releases" / name
        (release / "retained-sentinel").write_text("old\n", encoding="ascii")
        real_move = shutil.which("mv")
        self.assertIsNotNone(real_move)
        delayed_move = self.root / "delayed-retained-mv"
        delayed_move.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"{shlex.quote(real_move)} \"$@\"\n"
            'destination=${@: -1}\n'
            'if [[ "$destination" == */retained-release ]]; then sleep 1; fi\n',
            encoding="ascii",
        )
        delayed_move.chmod(0o755)
        absence = self.root / "release-was-absent"
        stop = self.root / "stop-watcher"
        watcher = subprocess.Popen([
            BASH_BIN, "-c",
            'while [[ ! -e "$1" ]]; do [[ -e "$2" ]] || : >"$3"; done',
            "watch-release", shell_path(stop), shell_path(release), shell_path(absence),
        ])
        try:
            retry = self.deploy(archive, checksum, MV_BIN=shell_path(delayed_move))
        finally:
            stop.touch()
            watcher.wait(timeout=5)

        self.assertEqual(0, retry.returncode, retry.stderr)
        self.assertFalse(absence.exists())
        self.assertFalse((release / "retained-sentinel").exists())
        self.assert_no_private_staging()

    def test_digest_marker_is_installed_atomically_with_fresh_release(self):
        archive, checksum, name = self.make_archive("20260802T171000Z-atomic-marker")
        real_move = shutil.which("mv")
        self.assertIsNotNone(real_move)
        wrapper = self.root / "interrupting-mv"
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"{shlex.quote(real_move)} \"$@\"\n"
            "for argument in \"$@\"; do\n"
            "  if [[ \"$argument\" == */extracted ]]; then exit 86; fi\n"
            "done\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        result = self.deploy(archive, checksum, MV_BIN=shell_path(wrapper))
        self.assertNotEqual(0, result.returncode)
        release = self.root / "releases" / name
        self.assertTrue(release.is_dir())
        expected_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(
            f"{expected_digest}\n",
            (release / ".release-digest").read_text(encoding="ascii"),
        )
        self.assertFalse((release / ".successful").exists())
        self.assertFalse((self.root / "current").exists())
        self.assert_no_private_staging()

    def test_installed_release_swap_never_executes_replacement_content(self):
        archive, checksum, name = self.make_archive("20260802T172000Z-held-release")
        release = self.root / "releases" / name
        swap_marker = self.root / "release-swapped"
        tamper_marker = self.root / "replacement-executed"
        content_log = self.root / "held-content.log"
        result = self.deploy(
            archive, checksum,
            STACK_FAKE_SWAP_RELEASE=shell_path(release),
            STACK_FAKE_SWAP_MARKER=shell_path(swap_marker),
            STACK_FAKE_TAMPER_MARKER=shell_path(tamper_marker),
            STACK_FAKE_CONTENT_LOG=shell_path(content_log),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertTrue(swap_marker.exists())
        self.assertFalse(tamper_marker.exists())
        self.assertFalse((self.root / "current").exists())
        first_call = shlex.split(self.log.read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("--file", first_call)
        compose_file = first_call[first_call.index("--file") + 1]
        self.assertRegex(compose_file, r"^/proc/self/fd/[0-9]+$")
        expected_hashes = {
            relative: hashlib.sha256(repo_path(relative).read_bytes()).hexdigest()
            for relative in (
                "compose.yaml", "versions.env",
                "config/opensearch/opensearch.yml",
                "config/opensearch/docker-entrypoint.sh",
            )
        }
        actual_hashes = dict(
            line.split(" ", 1)
            for line in content_log.read_text(encoding="ascii").splitlines()
        )
        self.assertEqual(expected_hashes, actual_hashes)

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
        original = (self.root / "runtime/.env").read_bytes()
        first_env = self.make_incoming_env(original + b"PAIR_MARKER=first\n")
        second_env = self.make_incoming_env(original + b"PAIR_MARKER=second\n")
        first_expected = first_env.read_bytes()
        first = subprocess.Popen([
            BASH_BIN, shell_path(repo_path("scripts/remote/deploy-release.sh")),
            "--root", shell_path(self.root), "--archive", shell_path(archive),
            "--checksum", shell_path(checksum), "--env", shell_path(first_env),
            "--profiles", "core",
        ], env={**self.env, "STACK_FAKE_HOLD_SECONDS": "3"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.5)
            second = self.deploy(archive, checksum, runtime_env=second_env)
            self.assertNotEqual(0, second.returncode)
            self.assertIn("deployment", second.stderr.lower())
        finally:
            first_stdout, first_stderr = first.communicate(timeout=10)
        self.assertEqual(0, first.returncode, first_stderr or first_stdout)
        self.assertEqual(first_expected, (self.root / "runtime/.env").read_bytes())
        self.assertFalse(first_env.exists())
        self.assertTrue(second_env.exists())


if __name__ == "__main__":
    unittest.main()
