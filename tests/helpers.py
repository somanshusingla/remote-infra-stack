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
