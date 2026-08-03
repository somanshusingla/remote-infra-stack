import contextlib
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.helpers import repo_path


VENDOR_LF_RULE = "vendor/chromadb-admin/** text=auto eol=lf"
TEXT_MEMBERS = (
    "vendor/chromadb-admin/src/app/page.tsx",
    "vendor/chromadb-admin/src/lib/types.ts",
    "vendor/chromadb-admin/next.config.js",
    "vendor/chromadb-admin/package.json",
    "vendor/chromadb-admin/src/app/globals.css",
    "vendor/chromadb-admin/Dockerfile",
)
BINARY_MEMBERS = {
    "vendor/chromadb-admin/src/app/favicon.ico": b"ICO\x00\xff\r\nraw\nbytes",
    "vendor/chromadb-admin/bun.lockb": b"BUN\x00\xfe\r\nraw\nbytes",
}
POWERSHELL_MEMBERS = (
    "scripts/deploy.ps1",
    "scripts/lib/Common.psm1",
)
TEXT_PAYLOAD = b"first line\nsecond line\n"
POWERSHELL_PAYLOAD = TEXT_PAYLOAD.replace(b"\n", b"\r\n")


def isolated_git_environment(controls: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": str(controls / "empty-global.gitconfig"),
            "GIT_CONFIG_SYSTEM": str(controls / "empty-system.gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def run_git(root: Path, controls: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgSign=false",
            "-c",
            f"core.hooksPath={(controls / 'empty-hooks').as_posix()}",
            "-c",
            f"core.attributesFile={(controls / 'empty-attributes').as_posix()}",
            *arguments,
        ],
        cwd=root,
        env=isolated_git_environment(controls),
        check=True,
        capture_output=True,
        text=True,
    )


@contextlib.contextmanager
def git_fixture(attributes: str):
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        root = base / "repository"
        controls = base / "git-controls"
        checkout = base / "fresh-checkout"
        root.mkdir()
        controls.mkdir()
        (controls / "empty-hooks").mkdir()
        for name in (
            "empty-global.gitconfig",
            "empty-system.gitconfig",
            "empty-attributes",
        ):
            (controls / name).write_text("", encoding="utf-8", newline="\n")

        run_git(root, controls, "init", "-b", "main")
        run_git(root, controls, "config", "user.name", "Archive Contract Tests")
        run_git(
            root,
            controls,
            "config",
            "user.email",
            "archive@example.invalid",
        )
        run_git(root, controls, "config", "core.autocrlf", "true")

        (root / ".gitattributes").write_text(
            attributes,
            encoding="utf-8",
            newline="\n",
        )
        for member in (*TEXT_MEMBERS, *POWERSHELL_MEMBERS):
            path = root / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(TEXT_PAYLOAD)
        outside = root / "outside/page.tsx"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(TEXT_PAYLOAD)
        for member, payload in BINARY_MEMBERS.items():
            path = root / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

        run_git(root, controls, "add", "-A")
        run_git(root, controls, "commit", "-m", "archive fixture")

        archive = base / "release.tar"
        run_git(
            root,
            controls,
            "archive",
            "--format=tar",
            f"--output={archive}",
            "HEAD",
        )
        archived_members = {}
        with tarfile.open(archive, "r") as release:
            for member in (
                *TEXT_MEMBERS,
                *POWERSHELL_MEMBERS,
                *BINARY_MEMBERS,
                "outside/page.tsx",
            ):
                archived = release.extractfile(member)
                if archived is None:
                    raise AssertionError(
                        f"archive member is not a regular file: {member}"
                    )
                archived_members[member] = archived.read()

        run_git(
            root,
            controls,
            "worktree",
            "add",
            "--detach",
            str(checkout),
            "HEAD",
        )
        yield root, controls, archived_members, checkout


class GitArchiveEolTests(unittest.TestCase):
    maxDiff = None

    def assert_chroma_admin_archive_behavior(self, attributes: str) -> None:
        with git_fixture(attributes) as (root, controls, members, checkout):
            self.assertEqual(
                "true",
                run_git(
                    checkout,
                    controls,
                    "config",
                    "--get",
                    "core.autocrlf",
                ).stdout.strip(),
            )
            for member in TEXT_MEMBERS:
                effective = run_git(
                    root,
                    controls,
                    "check-attr",
                    "text",
                    "eol",
                    "--",
                    member,
                ).stdout.splitlines()
                self.assertEqual(
                    [f"{member}: text: auto", f"{member}: eol: lf"],
                    effective,
                    member,
                )
                self.assertEqual(TEXT_PAYLOAD, members[member], member)
                self.assertNotIn(b"\r\n", members[member], member)
                self.assertEqual(
                    TEXT_PAYLOAD,
                    (checkout / member).read_bytes(),
                    f"fresh checkout: {member}",
                )

            self.assertEqual(
                POWERSHELL_PAYLOAD,
                members["outside/page.tsx"],
                "the LF override must remain scoped to the vendored Chroma Admin tree",
            )
            self.assertEqual(
                POWERSHELL_PAYLOAD,
                (checkout / "outside/page.tsx").read_bytes(),
                "fresh checkout outside the vendored tree",
            )

            for member in POWERSHELL_MEMBERS:
                effective = run_git(
                    root,
                    controls,
                    "check-attr",
                    "text",
                    "eol",
                    "--",
                    member,
                ).stdout.splitlines()
                self.assertEqual(
                    [f"{member}: text: set", f"{member}: eol: crlf"],
                    effective,
                    member,
                )
                self.assertEqual(POWERSHELL_PAYLOAD, members[member], member)
                self.assertEqual(
                    POWERSHELL_PAYLOAD,
                    (checkout / member).read_bytes(),
                    f"fresh checkout: {member}",
                )

            for member, payload in BINARY_MEMBERS.items():
                effective = run_git(
                    root,
                    controls,
                    "check-attr",
                    "text",
                    "eol",
                    "--",
                    member,
                ).stdout.splitlines()
                self.assertEqual(
                    [f"{member}: text: auto", f"{member}: eol: lf"],
                    effective,
                    member,
                )
                classification = run_git(
                    root,
                    controls,
                    "ls-files",
                    "--eol",
                    "--",
                    member,
                ).stdout
                self.assertRegex(
                    classification,
                    r"^i/-text\s+w/-text\s+attr/text=auto eol=lf\s+",
                    member,
                )
                self.assertEqual(payload, members[member], member)
                self.assertEqual(
                    payload,
                    (checkout / member).read_bytes(),
                    f"fresh checkout: {member}",
                )

    def test_checked_in_attributes_keep_chroma_admin_archives_portable(self):
        attributes = repo_path(".gitattributes").read_text(encoding="utf-8")
        self.assertEqual(1, attributes.splitlines().count(VENDOR_LF_RULE))
        self.assert_chroma_admin_archive_behavior(attributes)

    def test_fixture_ignores_hostile_inherited_git_configuration(self):
        attributes = repo_path(".gitattributes").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            controls = Path(directory)
            hooks = controls / "hooks"
            hooks.mkdir()
            hook = hooks / "pre-commit"
            interpreter = Path(sys.executable).resolve().as_posix()
            hook.write_text(
                f"#!{interpreter}\nimport sys\nsys.exit(91)\n",
                encoding="utf-8",
                newline="\n",
            )
            hook.chmod(0o755)
            hostile_attributes = controls / "hostile-attributes"
            hostile_attributes.write_text(
                "outside/** eol=lf\n",
                encoding="utf-8",
                newline="\n",
            )
            hostile_config = controls / "hostile.gitconfig"
            hostile_config.write_text(
                "[commit]\n"
                "\tgpgSign = true\n"
                "[gpg]\n"
                f"\tprogram = {(controls / 'missing-gpg').as_posix()}\n"
                "[core]\n"
                f"\thooksPath = {hooks.as_posix()}\n"
                f"\tattributesFile = {hostile_attributes.as_posix()}\n",
                encoding="utf-8",
                newline="\n",
            )
            with patch.dict(
                os.environ,
                {"GIT_CONFIG_GLOBAL": str(hostile_config)},
                clear=False,
            ):
                self.assert_chroma_admin_archive_behavior(attributes)

    def test_contract_rejects_missing_wrong_and_too_narrow_rules(self):
        attributes = repo_path(".gitattributes").read_text(encoding="utf-8")
        mutations = {
            "missing": attributes.replace(f"{VENDOR_LF_RULE}\n", ""),
            "wrong EOL": attributes.replace("eol=lf", "eol=crlf"),
            "only source files": attributes.replace(
                VENDOR_LF_RULE,
                "vendor/chromadb-admin/src/** text=auto eol=lf",
            ),
            "PowerShell scripts forced to LF": attributes.replace(
                "*.ps1 text eol=crlf",
                "*.ps1 text eol=lf",
            ),
            "PowerShell modules forced to LF": attributes.replace(
                "*.psm1 text eol=crlf",
                "*.psm1 text eol=lf",
            ),
        }
        for mutation, mutated_attributes in mutations.items():
            with self.subTest(mutation=mutation):
                with self.assertRaises(AssertionError):
                    self.assert_chroma_admin_archive_behavior(mutated_attributes)


if __name__ == "__main__":
    unittest.main()
