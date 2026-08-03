import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from tests.helpers import repo_path


class OllamaBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates = (shutil.which("bash"), r"C:\\Program Files\\Git\\bin\\bash.exe")
        cls.shell = next(
            (
                candidate for candidate in candidates
                if candidate and subprocess.run([candidate, "--version"], capture_output=True).returncode == 0
            ),
            None,
        )
        if not cls.shell:
            raise unittest.SkipTest("Git Bash is not available for bootstrap process tests")

    @staticmethod
    def shell_path(path: Path) -> str:
        return path.as_posix()

    def write_fake_ollama(self, directory: Path) -> Path:
        fake = directory / "ollama"
        fake.write_text(
            """#!/bin/sh
set -u
record() {
  line=$1
  count=$#
  shift
  line=$line$(printf '\\037%s' "$count")
  line=$line$(printf '\\037%s' "${OLLAMA_HOST:-}")
  for argument in "$@"; do line=$line$(printf '\\037%s' "$argument"); done
  printf '%s\\n' "$line" >>"$OLLAMA_CALL_LOG"
}
next_result() {
  file=$1
  result=$2
  if [ -s "$file" ]; then
    IFS= read -r result <"$file"
    result=$(printf '%s' "$result" | tr -d '\\r')
    tail -n +2 "$file" >"$file.next"
    mv "$file.next" "$file"
  fi
}
case "${1:-}" in
  serve)
    record serve
    printf '%s\\n' "$$" >"$OLLAMA_SERVER_PID_FILE"
    printf 'started\\n' >"$OLLAMA_SERVER_STARTED"
    trap 'printf "TERM\\n" >>"$OLLAMA_SIGNAL_LOG"; exit 0' TERM
    trap 'printf "INT\\n" >>"$OLLAMA_SIGNAL_LOG"; exit 0' INT
    trap 'printf "HUP\\n" >>"$OLLAMA_SIGNAL_LOG"; exit 0' HUP
    if [ "${OLLAMA_FAKE_SERVE_MODE:-run}" = exit ]; then
      : >"$OLLAMA_SERVER_EXITED_FILE"
      exit 9
    fi
    while :; do
      [ "${OLLAMA_FAKE_EXIT_ON_READY:-1}" = 1 ] && [ -e "$OLLAMA_READY_FILE" ] && exit 0
      sleep 1
    done
    ;;
  list)
    record list
    printf 'called\\n' >"$OLLAMA_LIST_STARTED"
    while [ -n "${OLLAMA_LIST_WAIT_FOR_SERVER_EXIT:-}" ] && [ ! -e "$OLLAMA_LIST_WAIT_FOR_SERVER_EXIT" ]; do sleep 1; done
    while [ -e "${OLLAMA_LIST_BLOCK_FILE:-/nonexistent}" ]; do sleep 1; done
    next_result "$OLLAMA_LIST_RESULTS" 0
    exit "$result"
    ;;
  show)
    record "$@"
    next_result "$OLLAMA_SHOW_RESULTS" 0
    exit "$result"
    ;;
  pull)
    record "$@"
    next_result "$OLLAMA_PULL_RESULTS" 0
    exit "$result"
    ;;
  *)
    exit 64
    ;;
esac
""",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        return fake

    def write_fake_sleep(self, directory: Path) -> Path:
        fake = directory / "sleep"
        fake.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$1\" >>\"$OLLAMA_SLEEP_LOG\"\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        return fake

    def wait_for(self, path: Path, description: str) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if path.exists():
                return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {description}")

    def wait_for_content(self, path: Path, expected: str) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if expected in path.read_text(encoding="utf-8").splitlines():
                return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {expected} in {path.name}")

    def make_environment(
        self,
        directory: Path,
        *,
        list_results: tuple[int, ...] = (0,),
        show_results: tuple[int, ...] = (0,),
        pull_results: tuple[int, ...] = (0,),
        serve_mode: str = "run",
        model: str = "gemma4:e4b",
        block_list: bool = False,
        exit_after_ready: bool = True,
        startup_attempts: int = 3,
        wait_for_server_exit: bool = False,
    ) -> tuple[dict[str, str], Path, Path, Path, Path]:
        def results_file(name: str, results: tuple[int, ...]) -> Path:
            path = directory / name
            path.write_text("".join(f"{result}\n" for result in results), encoding="utf-8")
            return path

        calls = directory / "calls.log"
        signals = directory / "signals.log"
        sleeps = directory / "sleeps.log"
        ready = directory / "model-ready"
        calls.write_text("", encoding="utf-8")
        signals.write_text("", encoding="utf-8")
        sleeps.write_text("", encoding="utf-8")
        blocker = directory / "block-list"
        if block_list:
            blocker.touch()
        env = os.environ | {
            "OLLAMA_BIN": self.shell_path(self.write_fake_ollama(directory)),
            "OLLAMA_MODEL": model,
            "OLLAMA_READY_FILE": self.shell_path(ready),
            "OLLAMA_STARTUP_ATTEMPTS": str(startup_attempts),
            "OLLAMA_PULL_ATTEMPTS": "3",
            "OLLAMA_RETRY_SECONDS": "0",
            "SLEEP_BIN": self.shell_path(self.write_fake_sleep(directory)),
            "OLLAMA_CALL_LOG": self.shell_path(calls),
            "OLLAMA_SIGNAL_LOG": self.shell_path(signals),
            "OLLAMA_SLEEP_LOG": self.shell_path(sleeps),
            "OLLAMA_SERVER_STARTED": self.shell_path(directory / "server-started"),
            "OLLAMA_SERVER_PID_FILE": self.shell_path(directory / "server.pid"),
            "OLLAMA_SERVER_EXITED_FILE": self.shell_path(directory / "server-exited"),
            "OLLAMA_LIST_STARTED": self.shell_path(directory / "list-started"),
            "OLLAMA_LIST_BLOCK_FILE": self.shell_path(blocker),
            "OLLAMA_LIST_WAIT_FOR_SERVER_EXIT": self.shell_path(directory / "server-exited") if wait_for_server_exit else "",
            "OLLAMA_LIST_RESULTS": self.shell_path(results_file("list-results", list_results)),
            "OLLAMA_SHOW_RESULTS": self.shell_path(results_file("show-results", show_results)),
            "OLLAMA_PULL_RESULTS": self.shell_path(results_file("pull-results", pull_results)),
            "OLLAMA_FAKE_SERVE_MODE": serve_mode,
            "OLLAMA_FAKE_EXIT_ON_READY": "1" if exit_after_ready else "0",
        }
        return env, calls, signals, ready, sleeps

    def start_bootstrap(self, directory: Path, env: dict[str, str]) -> tuple[subprocess.Popen[str], Path]:
        pid_file = directory / "bootstrap.pid"
        wrapper = directory / "start-bootstrap"
        wrapper.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$$\" >\"$OLLAMA_BOOTSTRAP_PID_FILE\"\nexec \"$OLLAMA_BOOTSTRAP_SCRIPT\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        wrapped_env = env | {
            "OLLAMA_BOOTSTRAP_PID_FILE": self.shell_path(pid_file),
            "OLLAMA_BOOTSTRAP_SCRIPT": self.shell_path(repo_path("config/ollama/bootstrap.sh")),
        }
        return subprocess.Popen(
            [self.shell, self.shell_path(wrapper)],
            env=wrapped_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ), pid_file

    def send_bootstrap_signal(self, pid_file: Path, sent_signal: int) -> None:
        pid = pid_file.read_text(encoding="utf-8").strip()
        self.assertTrue(pid.isdecimal(), pid)
        subprocess.run(
            [self.shell, "-lc", f"kill -{sent_signal} {pid}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def finish_process(self, process: subprocess.Popen[str], pid_file: Path, directory: Path) -> tuple[str, str]:
        if process.poll() is None and pid_file.exists():
            try:
                self.send_bootstrap_signal(pid_file, 15)
            except (subprocess.SubprocessError, AssertionError):
                pass
        try:
            return process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server_pid_file = directory / "server.pid"
            if server_pid_file.exists():
                try:
                    self.send_bootstrap_signal(server_pid_file, 15)
                except (subprocess.SubprocessError, AssertionError):
                    pass
            process.kill()
            return process.communicate(timeout=5)

    def run_bootstrap(
        self,
        *,
        list_failures: int = 0,
        show_results: tuple[int, ...] = (0,),
        pull_results: tuple[int, ...] = (0,),
        serve_mode: str = "run",
        model: str = "gemma4:e4b",
        startup_attempts: int = 3,
        wait_for_server_exit: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], list[str], bool]:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            env, calls_file, _, ready, sleeps_file = self.make_environment(
                directory,
                list_results=(1,) * list_failures + (0,),
                show_results=show_results,
                pull_results=pull_results,
                serve_mode=serve_mode,
                model=model,
                startup_attempts=startup_attempts,
                wait_for_server_exit=wait_for_server_exit,
            )
            process, pid_file = self.start_bootstrap(directory, env)
            try:
                stdout, stderr = process.communicate(timeout=8)
            finally:
                if process.poll() is None:
                    self.finish_process(process, pid_file, directory)
            result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
            records = self.read_call_records(calls_file)
            argv = [arguments for arguments, _ in records]
            self.last_call_argv = argv
            self.last_call_hosts = [(" ".join(arguments), host) for arguments, host in records]
            self.last_sleep_calls = sleeps_file.read_text(encoding="utf-8").splitlines()
            calls = [" ".join(arguments) for arguments in argv]
            return result, calls, ready.exists()

    def read_call_argv(self, calls_file: Path) -> list[list[str]]:
        return [arguments for arguments, _ in self.read_call_records(calls_file)]

    def read_call_records(self, calls_file: Path) -> list[tuple[list[str], str]]:
        calls = []
        for line in calls_file.read_text(encoding="utf-8").splitlines():
            fields = line.split("\x1f")
            self.assertEqual(int(fields[1]), len(fields) - 2, fields)
            calls.append(([fields[0], *fields[3:]], fields[2]))
        return calls

    def test_first_start_waits_pulls_verifies_and_marks_ready(self):
        result, calls, ready = self.run_bootstrap(
            list_failures=2, show_results=(1, 0), pull_results=(0,)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, calls.count("pull gemma4:e4b"), calls)
        self.assertEqual(["0", "0"], self.last_sleep_calls)
        self.assertEqual(
            ["serve", "list", "list", "list", "show gemma4:e4b", "pull gemma4:e4b", "show gemma4:e4b"],
            calls,
        )
        self.assertTrue(ready)

    def test_cached_model_skips_pull(self):
        result, calls, ready = self.run_bootstrap(show_results=(0,))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("pull gemma4:e4b", calls)
        self.assertTrue(ready)

    def test_transient_pull_failure_retries(self):
        result, calls, ready = self.run_bootstrap(show_results=(1, 0), pull_results=(1, 0))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, calls.count("pull gemma4:e4b"))
        self.assertEqual(["0"], self.last_sleep_calls)
        self.assertTrue(ready)

    def test_exhausted_pull_failures_never_mark_ready(self):
        result, calls, ready = self.run_bootstrap(show_results=(1,), pull_results=(1, 1, 1))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(3, calls.count("pull gemma4:e4b"))
        self.assertFalse(ready)

    def test_final_verification_failure_never_marks_ready(self):
        result, calls, ready = self.run_bootstrap(show_results=(1, 1), pull_results=(0,))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(["show gemma4:e4b", "pull gemma4:e4b", "show gemma4:e4b"], calls[2:])
        self.assertFalse(ready)

    def test_server_death_during_startup_fails_without_readiness(self):
        result, calls, ready = self.run_bootstrap(
            list_failures=1,
            serve_mode="exit",
            startup_attempts=100,
            wait_for_server_exit=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertCountEqual(["serve", "list"], calls)
        self.assertEqual([], self.last_sleep_calls)
        self.assertFalse(ready)

    def test_ollama_hosts_are_scoped_to_server_and_client_calls(self):
        result, calls, ready = self.run_bootstrap(show_results=(1, 0), pull_results=(0,))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [
                ("serve", "0.0.0.0:11434"),
                ("list", "127.0.0.1:11434"),
                ("show gemma4:e4b", "127.0.0.1:11434"),
                ("pull gemma4:e4b", "127.0.0.1:11434"),
                ("show gemma4:e4b", "127.0.0.1:11434"),
            ],
            self.last_call_hosts,
        )
        self.assertTrue(ready)

    def test_missing_model_fails_before_starting_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            env, calls_file, _, ready, _ = self.make_environment(directory)
            env.pop("OLLAMA_MODEL")
            process, _ = self.start_bootstrap(directory, env)
            stdout, stderr = process.communicate(timeout=5)
            self.assertNotEqual(0, process.returncode, stdout)
            self.assertIn("OLLAMA_MODEL", stderr)
            self.assertEqual([], self.read_call_argv(calls_file))
            self.assertFalse(ready.exists())

    def test_model_argument_is_preserved_as_one_exact_argument(self):
        model = "gemma 4;$(not-a-command)&*"
        result, calls, ready = self.run_bootstrap(show_results=(1, 0), pull_results=(0,), model=model)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"show {model}", calls)
        self.assertIn(f"pull {model}", calls)
        self.assertIn(["show", model], self.last_call_argv)
        self.assertIn(["pull", model], self.last_call_argv)
        self.assertTrue(ready)

    def test_ready_file_is_removed_then_created_only_after_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            env, calls_file, _, ready, _ = self.make_environment(directory, block_list=True)
            ready.write_text("stale", encoding="utf-8")
            process, pid_file = self.start_bootstrap(directory, env)
            try:
                self.wait_for(directory / "list-started", "initial readiness probe")
                self.assertFalse(ready.exists())
                (directory / "block-list").unlink()
                stdout, stderr = process.communicate(timeout=12)
            finally:
                if process.poll() is None:
                    self.finish_process(process, pid_file, directory)
            self.assertEqual(0, process.returncode, f"{stdout}\n{stderr}")
            self.assertEqual(
                ["serve", "list", "show gemma4:e4b", "show gemma4:e4b"],
                [" ".join(arguments) for arguments in self.read_call_argv(calls_file)],
            )
            self.assertTrue(ready.exists())

    def test_startup_attempt_bound_is_enforced(self):
        result, calls, ready = self.run_bootstrap(list_failures=3)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(3, calls.count("list"))
        self.assertFalse(ready)

    def test_pull_attempt_bound_is_enforced(self):
        result, calls, ready = self.run_bootstrap(show_results=(1,), pull_results=(1, 1, 1, 0))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual(3, calls.count("pull gemma4:e4b"))
        self.assertFalse(ready)

    def test_term_is_forwarded_to_server_and_returns_nonzero(self):
        self.assert_signal_forwarding(signal.SIGTERM, "TERM", 143)

    @unittest.skipIf(
        os.name == "nt",
        "Git Bash on Windows cannot deliver SIGINT with kill -INT to this detached process",
    )
    def test_int_is_forwarded_to_server_and_returns_nonzero(self):
        self.assert_signal_forwarding(signal.SIGINT, "INT", 130)

    def test_hup_is_forwarded_to_server_and_returns_nonzero(self):
        # Python on Windows omits SIGHUP, but Git Bash's kill accepts POSIX signal 1.
        self.assert_signal_forwarding(1, "HUP", 129)

    def assert_signal_forwarding(
        self, sent_signal: int | signal.Signals, expected_signal: str, expected_status: int
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            env, _, signals, ready, _ = self.make_environment(directory, exit_after_ready=False)
            process, pid_file = self.start_bootstrap(directory, env)
            self.wait_for(ready, "bootstrap readiness")
            self.wait_for(pid_file, "Git Bash bootstrap PID")
            try:
                self.send_bootstrap_signal(pid_file, int(sent_signal))
                stdout, stderr = process.communicate(timeout=12)
            finally:
                if process.poll() is None:
                    self.finish_process(process, pid_file, directory)
            self.assertEqual(expected_status, process.returncode, f"{stdout}\n{stderr}")
            self.wait_for_content(signals, expected_signal)
            self.assertIn(expected_signal, signals.read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    unittest.main()
