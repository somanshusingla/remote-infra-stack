import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

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
TEXT_PAYLOAD = b"first line\nsecond line\n"


def run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def archive_members(attributes: str) -> tuple[Path, dict[str, bytes], tempfile.TemporaryDirectory]:
    temporary_directory = tempfile.TemporaryDirectory()
    root = Path(temporary_directory.name)
    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Archive Contract Tests")
    run_git(root, "config", "user.email", "archive@example.invalid")
    run_git(root, "config", "core.autocrlf", "true")

    (root / ".gitattributes").write_text(attributes, encoding="utf-8", newline="\n")
    for member in TEXT_MEMBERS:
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

    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "archive fixture")

    archive = root / "release.tar"
    run_git(root, "archive", "--format=tar", f"--output={archive}", "HEAD")
    members = {}
    with tarfile.open(archive, "r") as release:
        for member in (*TEXT_MEMBERS, *BINARY_MEMBERS, "outside/page.tsx"):
            extracted = release.extractfile(member)
            if extracted is None:
                raise AssertionError(f"archive member is not a regular file: {member}")
            members[member] = extracted.read()
    return root, members, temporary_directory


class GitArchiveEolTests(unittest.TestCase):
    maxDiff = None

    def assert_chroma_admin_archive_behavior(self, attributes: str) -> None:
        root, members, temporary_directory = archive_members(attributes)
        try:
            for member in TEXT_MEMBERS:
                effective = run_git(
                    root, "check-attr", "text", "eol", "--", member
                ).stdout.splitlines()
                self.assertEqual(
                    [f"{member}: text: auto", f"{member}: eol: lf"],
                    effective,
                    member,
                )
                self.assertEqual(TEXT_PAYLOAD, members[member], member)
                self.assertNotIn(b"\r\n", members[member], member)

            self.assertEqual(
                TEXT_PAYLOAD.replace(b"\n", b"\r\n"),
                members["outside/page.tsx"],
                "the LF override must remain scoped to the vendored Chroma Admin tree",
            )

            for member, payload in BINARY_MEMBERS.items():
                effective = run_git(
                    root, "check-attr", "text", "eol", "--", member
                ).stdout.splitlines()
                self.assertEqual(
                    [f"{member}: text: auto", f"{member}: eol: lf"],
                    effective,
                    member,
                )
                classification = run_git(
                    root, "ls-files", "--eol", "--", member
                ).stdout
                self.assertRegex(
                    classification,
                    r"^i/-text\s+w/-text\s+attr/text=auto eol=lf\s+",
                    member,
                )
                self.assertEqual(payload, members[member], member)
        finally:
            temporary_directory.cleanup()

    def test_checked_in_attributes_keep_chroma_admin_archives_portable(self):
        attributes = repo_path(".gitattributes").read_text(encoding="utf-8")
        self.assertEqual(1, attributes.splitlines().count(VENDOR_LF_RULE))
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
        }
        for mutation, mutated_attributes in mutations.items():
            with self.subTest(mutation=mutation):
                with self.assertRaises(AssertionError):
                    self.assert_chroma_admin_archive_behavior(mutated_attributes)


if __name__ == "__main__":
    unittest.main()
