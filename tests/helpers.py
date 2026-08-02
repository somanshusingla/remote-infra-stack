import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def repo_path(relative: str) -> Path:
    return REPO_ROOT / relative


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def read_env_keys(path: Path) -> list[str]:
    return list(read_env(path))


def validate_fixture_contracts(stack_fixture: Path, remote_fixture: Path) -> None:
    stack = read_env(stack_fixture)
    remote = read_env(remote_fixture)

    if set(stack) != set(read_env(repo_path(".env.example"))):
        raise ValueError("stack fixture key set must match .env.example")
    if set(remote) != set(read_env(repo_path("remote.env.example"))):
        raise ValueError("remote fixture key set must match remote.env.example")

    opensearch_password = stack["OPENSEARCH_INITIAL_ADMIN_PASSWORD"]
    if (
        len(opensearch_password) != 32
        or not any(character.islower() for character in opensearch_password)
        or not any(character.isupper() for character in opensearch_password)
    ):
        raise ValueError(
            "OPENSEARCH_INITIAL_ADMIN_PASSWORD must contain exactly 32 characters with mixed case"
        )

    if not re.fullmatch(r"[0-9a-f]{64}", stack["LANGFUSE_ENCRYPTION_KEY"]):
        raise ValueError(
            "LANGFUSE_ENCRYPTION_KEY must contain exactly 64 lowercase hexadecimal characters"
        )
