import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import repo_path
from tests.test_remote_runtime import shell_path


INSTALLER = repo_path("images/chromadb-admin/install-dependencies.sh")


def find_strict_posix_shell():
    configured = os.environ.get("POSIX_SH_BIN")
    candidates = [configured] if configured else []
    if os.name != "nt":
        candidates.extend((shutil.which("dash"), "/bin/dash", "/bin/sh"))
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        posix_probe = subprocess.run(
            [candidate, "-c", "case x in x) exit 0;; esac"],
            capture_output=True,
        )
        bashism_probe = subprocess.run(
            [candidate, "-c", "[[ 1 -eq 1 ]]"],
            capture_output=True,
        )
        if posix_probe.returncode == 0 and bashism_probe.returncode != 0:
            return candidate
    return None


POSIX_SHELL = find_strict_posix_shell()


@unittest.skipUnless(POSIX_SHELL, "requires /bin/sh or dash that rejects Bashisms")
class ChromaAdminDependencyInstallTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(INSTALLER.is_file(), "dependency install helper is required")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "install root;safe"
        self.root.mkdir()
        self.plan = self.root / "plan"
        self.plan.mkdir()
        self.calls = self.root / "npm-calls.log"
        self.sleeps = self.root / "sleep-calls.log"
        self.fake_npm = self.root / "npm"
        self.fake_sleep = self.root / "sleep"
        self.fake_npm.write_text(
            """#!/bin/sh
set -eu
printf '%s\\0' "$#" "$@" >>"$FAKE_NPM_CALLS"
case "${1:-}" in
  --version)
    printf '%s\\n' "${FAKE_NPM_VERSION:-10.8.3}"
    exit "${FAKE_NPM_VERSION_STATUS:-0}"
    ;;
  ci)
    attempt=1
    if [ -f "$FAKE_NPM_PLAN/attempt" ]; then
      attempt=$(( $(cat "$FAKE_NPM_PLAN/attempt") + 1 ))
    fi
    printf '%s\\n' "$attempt" >"$FAKE_NPM_PLAN/attempt"
    if [ "$attempt" -gt 1 ] && [ -e node_modules/stale-from-failed-install ]; then
      printf '%s\\n' 'stale dependency tree was not cleaned' >&2
      exit 79
    fi
    mode=$(cat "$FAKE_NPM_PLAN/ci-$attempt-mode")
    status=$(cat "$FAKE_NPM_PLAN/ci-$attempt-status")
    if [ -f "$FAKE_NPM_PLAN/ci-$attempt-output" ]; then
      cat "$FAKE_NPM_PLAN/ci-$attempt-output"
    fi
    if [ -f "$FAKE_NPM_PLAN/ci-$attempt-stderr" ]; then
      cat "$FAKE_NPM_PLAN/ci-$attempt-stderr" >&2
    fi
    mkdir -p node_modules/.bin node_modules/@next/swc-linux-x64-gnu
    case "$mode" in
      complete|complete-stale)
        : >node_modules/.bin/next
        : >node_modules/.bin/tsc
        chmod +x node_modules/.bin/next node_modules/.bin/tsc
        printf '%s\\n' 'fake-swc' >node_modules/@next/swc-linux-x64-gnu/next-swc.linux-x64-gnu.node
        ;;
      missing-swc)
        : >node_modules/.bin/next
        : >node_modules/.bin/tsc
        chmod +x node_modules/.bin/next node_modules/.bin/tsc
        ;;
      missing-next)
        : >node_modules/.bin/tsc
        chmod +x node_modules/.bin/tsc
        printf '%s\\n' 'fake-swc' >node_modules/@next/swc-linux-x64-gnu/next-swc.linux-x64-gnu.node
        ;;
      missing-tsc)
        : >node_modules/.bin/next
        chmod +x node_modules/.bin/next
        printf '%s\\n' 'fake-swc' >node_modules/@next/swc-linux-x64-gnu/next-swc.linux-x64-gnu.node
        ;;
      empty-swc)
        : >node_modules/.bin/next
        : >node_modules/.bin/tsc
        chmod +x node_modules/.bin/next node_modules/.bin/tsc
        : >node_modules/@next/swc-linux-x64-gnu/next-swc.linux-x64-gnu.node
        ;;
      partial)
        : >node_modules/.bin/next
        chmod +x node_modules/.bin/next
        ;;
      *)
        printf '%s\\n' "unsupported fake mode: $mode" >&2
        exit 78
        ;;
    esac
    if [ "$mode" = complete-stale ]; then
      : >node_modules/stale-from-failed-install
    fi
    exit "$status"
    ;;
  ls)
    attempt=$(cat "$FAKE_NPM_PLAN/attempt")
    if [ -f "$FAKE_NPM_PLAN/ls-$attempt-status" ]; then
      exit "$(cat "$FAKE_NPM_PLAN/ls-$attempt-status")"
    fi
    exit 0
    ;;
  *)
    printf '%s\\n' "unexpected npm command: $*" >&2
    exit 77
    ;;
esac
""",
            encoding="utf-8",
        )
        self.fake_sleep.write_text(
            "#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" >>\"$FAKE_SLEEP_CALLS\"\n",
            encoding="utf-8",
        )
        self.fake_npm.chmod(0o755)
        self.fake_sleep.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def configure_attempt(
        self, attempt, *, mode, status, output="", stderr="", ls_status=0
    ):
        (self.plan / f"ci-{attempt}-mode").write_text(f"{mode}\n", encoding="ascii")
        (self.plan / f"ci-{attempt}-status").write_text(f"{status}\n", encoding="ascii")
        (self.plan / f"ci-{attempt}-output").write_text(output, encoding="utf-8")
        (self.plan / f"ci-{attempt}-stderr").write_text(stderr, encoding="utf-8")
        (self.plan / f"ls-{attempt}-status").write_text(
            f"{ls_status}\n", encoding="ascii"
        )

    def run_installer(self, installer=INSTALLER, **environment):
        return subprocess.run(
            [POSIX_SHELL, shell_path(installer)],
            cwd=self.root,
            env={
                **os.environ,
                "NPM_BIN": shell_path(self.fake_npm),
                "SLEEP_BIN": shell_path(self.fake_sleep),
                "EXPECTED_NPM_VERSION": "10.8.3",
                "NPM_INSTALL_ATTEMPTS": "3",
                "NPM_INSTALL_RETRY_DELAY_SECONDS": "0",
                "FAKE_NPM_PLAN": shell_path(self.plan),
                "FAKE_NPM_CALLS": shell_path(self.calls),
                "FAKE_SLEEP_CALLS": shell_path(self.sleeps),
                **environment,
            },
            capture_output=True,
            text=True,
        )

    def test_selected_posix_shell_rejects_a_bashism_mutation(self):
        mutated = self.root / "install-with-bashism.sh"
        mutated.write_text(
            INSTALLER.read_text(encoding="utf-8").replace(
                "set -eu\n", "set -eu\n[[ 1 -eq 1 ]]\n", 1
            ),
            encoding="utf-8",
        )

        result = self.run_installer(installer=mutated)

        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.plan / "attempt").exists())

    def npm_calls(self):
        fields = self.calls.read_bytes().split(b"\0")
        if fields and fields[-1] == b"":
            fields.pop()
        calls = []
        index = 0
        while index < len(fields):
            count = int(fields[index].decode("ascii"))
            index += 1
            calls.append(
                tuple(
                    field.decode("utf-8")
                    for field in fields[index:index + count]
                )
            )
            index += count
        return calls

    def sleep_calls(self):
        if not self.sleeps.exists():
            return []
        return self.sleeps.read_text(encoding="utf-8").splitlines()

    def test_false_zero_exit_signature_is_replayed_rejected_and_retried_cleanly(self):
        self.configure_attempt(
            1,
            mode="complete-stale",
            status=0,
            stderr="npm error Exit handler never called!\n",
        )
        self.configure_attempt(2, mode="complete", status=0, output="second install\n")

        result = self.run_installer()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("npm error Exit handler never called!", result.stdout)
        self.assertIn("second install", result.stdout)
        self.assertEqual(2, int((self.plan / "attempt").read_text()))
        self.assertFalse((self.root / "node_modules/stale-from-failed-install").exists())
        self.assertEqual(["0"], self.sleep_calls())

    def test_missing_swc_postcondition_retries_after_cleaning_partial_tree(self):
        self.configure_attempt(1, mode="missing-swc", status=0)
        self.configure_attempt(2, mode="complete", status=0)

        result = self.run_installer()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, int((self.plan / "attempt").read_text()))
        self.assertTrue(
            (self.root / "node_modules/@next/swc-linux-x64-gnu/next-swc.linux-x64-gnu.node").is_file()
        )

    def test_empty_swc_artifact_retries_instead_of_accepting_incomplete_tree(self):
        self.configure_attempt(1, mode="empty-swc", status=0)
        self.configure_attempt(2, mode="complete", status=0)

        result = self.run_installer()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, int((self.plan / "attempt").read_text()))
        swc = (
            self.root
            / "node_modules/@next/swc-linux-x64-gnu/next-swc.linux-x64-gnu.node"
        )
        self.assertGreater(
            swc.stat().st_size,
            0,
        )

    def test_missing_next_executable_retries_instead_of_accepting_incomplete_tree(self):
        self.configure_attempt(1, mode="missing-next", status=0)
        self.configure_attempt(2, mode="complete", status=0)

        result = self.run_installer()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, int((self.plan / "attempt").read_text()))

    def test_missing_typescript_executable_retries_instead_of_accepting_incomplete_tree(self):
        self.configure_attempt(1, mode="missing-tsc", status=0)
        self.configure_attempt(2, mode="complete", status=0)

        result = self.run_installer()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, int((self.plan / "attempt").read_text()))

    def test_failed_npm_ls_retries_even_when_files_exist(self):
        self.configure_attempt(1, mode="complete", status=0, ls_status=1)
        self.configure_attempt(2, mode="complete", status=0)

        result = self.run_installer()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, int((self.plan / "attempt").read_text()))

    def test_nonzero_npm_ci_retries_then_succeeds(self):
        self.configure_attempt(1, mode="partial", status=68, output="temporary DNS failure\n")
        self.configure_attempt(2, mode="complete", status=0)

        result = self.run_installer()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("temporary DNS failure", result.stdout)
        self.assertEqual(2, int((self.plan / "attempt").read_text()))

    def test_immediate_success_uses_exact_commands_without_sleeping(self):
        self.configure_attempt(1, mode="complete", status=0)

        result = self.run_installer()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [
                ("--version",),
                ("ci", "--no-audit", "--no-fund"),
                ("ls", "--all"),
            ],
            self.npm_calls(),
        )
        self.assertEqual([], self.sleep_calls())

    def test_three_failed_attempts_exit_nonzero_and_leave_no_partial_tree(self):
        for attempt in range(1, 4):
            self.configure_attempt(attempt, mode="partial", status=69)

        result = self.run_installer()

        self.assertNotEqual(0, result.returncode)
        self.assertEqual(3, int((self.plan / "attempt").read_text()))
        self.assertEqual(["0", "0"], self.sleep_calls())
        self.assertFalse((self.root / "node_modules").exists())

    def test_more_than_three_attempts_is_rejected_before_running_npm(self):
        result = self.run_installer(NPM_INSTALL_ATTEMPTS="4")

        self.assertNotEqual(0, result.returncode)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.plan / "attempt").exists())

    def test_wrong_npm_version_fails_before_dependency_install(self):
        result = self.run_installer(FAKE_NPM_VERSION="10.8.2")

        self.assertNotEqual(0, result.returncode)
        self.assertEqual([("--version",)], self.npm_calls())
        self.assertFalse((self.plan / "attempt").exists())


if __name__ == "__main__":
    unittest.main()
