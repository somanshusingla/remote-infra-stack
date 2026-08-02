# Remote Infra Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a profile-based, single-VM Docker Compose infrastructure repository that is deployed over SSH/SCP and consumed securely from local applications and browser UIs.

**Architecture:** One repository-owned `compose.yaml` defines isolated service profiles and a stable Compose project. Cross-platform Bash and PowerShell clients validate configuration, upload immutable Git archives and an ignored secret file, invoke shared remote Bash operations, and open loopback-only SSH tunnels. Named volumes persist until the operator explicitly destroys them or deletes the VM.

**Tech Stack:** Docker Engine, Docker Compose v2, Ubuntu LTS, Bash, Windows PowerShell 5.1+/PowerShell 7+, OpenSSH/SCP, Python 3 standard-library contract tests, PostgreSQL, Redis, Chroma, OpenSearch, Langfuse, ClickHouse, MinIO, pgAdmin, and RedisInsight.

## Global Constraints

- The repository is a personal single-VM development stack; production, HA, backups, public ingress, TLS termination, and cloud provisioning are out of scope.
- The Compose project name is exactly `remote-infra-stack`.
- Profiles are exactly `core`, `vector`, `search`, `observability`, and `tools`; `tools` requires `core`.
- No service starts unless a profile is explicitly selected.
- Every image uses a committed non-`latest` version from `versions.env`.
- Every published port binds to VM address `127.0.0.1`.
- Chroma publishes VM and local port `18000` while listening on container port `8000`.
- Application PostgreSQL/Redis are separate from Langfuse PostgreSQL/Redis.
- Persistent data uses named Docker volumes; no backup/export automation is added.
- Local control supports Bash on macOS/Linux and Windows PowerShell 5.1+/PowerShell 7+ with equivalent commands.
- Remote execution supports Ubuntu 22.04, 24.04, and 26.04 LTS on `amd64`, and capability-gates future Ubuntu LTS releases through Docker repository probing.
- Deployment targets an existing SSH-accessible VM and uploads a clean Git `HEAD` archive plus ignored `.env` separately.
- Normal operations never remove volumes; `destroy` requires explicit target and data-loss confirmation.
- Shell scripts are committed with LF endings and run in strict error mode.
- Tests use Python's standard `unittest`; operator scripts do not require Python on local or remote runtime hosts.
- No containers or image pulls run on the local machine. Local `docker compose config` is allowed because it does not contact a daemon; authoritative image, container, and health verification runs on the configured Ubuntu GCP VM.

---

## Planned File Map

| Path | Responsibility |
| --- | --- |
| `compose.yaml` | Complete repository-owned Compose model, profiles, health checks, limits, networks, and volumes |
| `versions.env` | Committed exact image tags |
| `.env.example` | Required secret/runtime variable contract |
| `remote.env.example` | SSH target and local tunnel override contract |
| `.gitignore` | Prevent secrets, runtime artifacts, and generated archives entering Git |
| `.gitattributes` | Force LF for remote scripts and deterministic text handling |
| `config/opensearch/opensearch.yml` | Single-node development OpenSearch configuration |
| `scripts/init-env.sh`, `scripts/init-env.ps1` | Secure cross-platform `.env` generation |
| `scripts/bootstrap.sh`, `scripts/bootstrap.ps1` | Upload/invoke the shared remote bootstrap |
| `scripts/deploy.sh`, `scripts/deploy.ps1` | Create/upload Git release archive and secret file |
| `scripts/stack.sh`, `scripts/stack.ps1` | Local entry points for up/stop/down/status/logs/check/destroy |
| `scripts/tunnel.sh`, `scripts/tunnel.ps1` | Profile-selective SSH port forwarding |
| `scripts/lib/common.sh`, `scripts/lib/Common.psm1` | Parse local config, validate profiles, build SSH/SCP arguments |
| `scripts/remote/bootstrap-host.sh` | Idempotent Ubuntu/Docker/OpenSearch host preparation |
| `scripts/remote/deploy-release.sh` | Locked checksum/extract/Compose/health/release activation lifecycle |
| `scripts/remote/compose.sh` | Canonical Compose command construction and profile expansion |
| `scripts/remote/stack.sh` | Remote runtime operations without upload |
| `scripts/remote/health.sh` | Service-specific readiness verification |
| `tests/fixtures/stack.env` | Non-secret deterministic Compose test values |
| `tests/fixtures/remote.env` | Deterministic SSH/operator test values |
| `tests/fixtures/os-release/*` | Ubuntu and unsupported-host bootstrap fixtures |
| `tests/helpers.py` | Repository, command, fixture, and Compose JSON helpers |
| `tests/test_*.py` | Contract tests for repository, Compose, bootstrap, releases, wrappers, and tunnels |
| `tests/fakes/*` | Fake Docker, SSH, and SCP commands that log invocations |
| `README.md` | End-user setup, profile, endpoint, and lifecycle documentation |

---

### Task 1: Establish the repository contract and pinned version catalog

**Files:**
- Create: `.gitignore`
- Create: `.gitattributes`
- Create: `versions.env`
- Create: `.env.example`
- Create: `remote.env.example`
- Create: `tests/__init__.py`
- Create: `tests/helpers.py`
- Create: `tests/test_repository_contract.py`
- Create: `tests/fixtures/stack.env`
- Create: `tests/fixtures/remote.env`

**Interfaces:**
- Produces: `helpers.repo_path(relative: str) -> pathlib.Path`
- Produces: `helpers.read_env(path: pathlib.Path) -> dict[str, str]`
- Produces: committed image variables consumed by every later Compose task
- Produces: exact `.env` and `remote.env` key contracts consumed by scripts

- [ ] **Step 1: Write the failing repository contract test**

```python
# tests/test_repository_contract.py
import re
import unittest

from tests.helpers import read_env, repo_path


class RepositoryContractTests(unittest.TestCase):
    def test_required_root_files_exist(self):
        for name in ("compose.yaml", "versions.env", ".env.example", "remote.env.example"):
            self.assertTrue(repo_path(name).is_file(), name)

    def test_versions_are_explicit_and_never_latest(self):
        versions = read_env(repo_path("versions.env"))
        self.assertGreaterEqual(len(versions), 12)
        for name, image in versions.items():
            self.assertRegex(name, r"_IMAGE$")
            self.assertNotRegex(image, r"(?::|@)latest(?:$|@)")
            self.assertRegex(image, r"[:@]")

    def test_secret_files_are_ignored(self):
        ignored = repo_path(".gitignore").read_text(encoding="utf-8")
        self.assertRegex(ignored, r"(?m)^\.env$")
        self.assertRegex(ignored, r"(?m)^remote\.env$")
        self.assertIn(".artifacts/", ignored)

    def test_remote_scripts_are_forced_to_lf(self):
        attributes = repo_path(".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.sh text eol=lf", attributes)


if __name__ == "__main__":
    unittest.main()
```

```python
# tests/helpers.py
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_repository_contract -v`

Expected: FAIL because root contract files and `compose.yaml` do not exist.

- [ ] **Step 3: Add the repository metadata and configuration contracts**

Create `.gitignore` with exactly these runtime exclusions:

```gitignore
.env
remote.env
.artifacts/
*.tar.gz
*.sha256
__pycache__/
*.pyc
```

Create `.gitattributes`:

```gitattributes
* text=auto
*.sh text eol=lf
*.env text eol=lf
*.yaml text eol=lf
*.yml text eol=lf
*.ps1 text eol=crlf
*.psm1 text eol=crlf
```

Create `versions.env` with this initial verified-version catalog:

```dotenv
APP_POSTGRES_IMAGE=docker.io/postgres:18.4-bookworm
APP_REDIS_IMAGE=docker.io/redis:8.8.0-bookworm
CHROMA_IMAGE=docker.io/chromadb/chroma:1.5.9
OPENSEARCH_IMAGE=docker.io/opensearchproject/opensearch:3.7.0
OPENSEARCH_DASHBOARDS_IMAGE=docker.io/opensearchproject/opensearch-dashboards:3.7.0
LANGFUSE_WEB_IMAGE=docker.io/langfuse/langfuse:3.176.0
LANGFUSE_WORKER_IMAGE=docker.io/langfuse/langfuse-worker:3.176.0
LANGFUSE_POSTGRES_IMAGE=docker.io/postgres:17.10-bookworm
LANGFUSE_REDIS_IMAGE=docker.io/redis:7.4.3-bookworm
CLICKHOUSE_IMAGE=docker.io/clickhouse/clickhouse-server:25.12
MINIO_IMAGE=docker.io/minio/minio:RELEASE.2025-06-13T11-33-47Z
PGADMIN_IMAGE=docker.io/dpage/pgadmin4:9.16
REDISINSIGHT_IMAGE=docker.io/redis/redisinsight:3.4.2
```

Create `.env.example` with the exact required keys from the approved design and optional memory overrides using these defaults:

```dotenv
APP_POSTGRES_USER=app
APP_POSTGRES_DB=app
APP_POSTGRES_PASSWORD=GENERATED_BY_INIT_ENV
APP_REDIS_PASSWORD=GENERATED_BY_INIT_ENV
OPENSEARCH_INITIAL_ADMIN_PASSWORD=GENERATED_BY_INIT_ENV
LANGFUSE_POSTGRES_USER=langfuse
LANGFUSE_POSTGRES_DB=langfuse
LANGFUSE_POSTGRES_PASSWORD=GENERATED_BY_INIT_ENV
LANGFUSE_REDIS_PASSWORD=GENERATED_BY_INIT_ENV
LANGFUSE_CLICKHOUSE_USER=clickhouse
LANGFUSE_CLICKHOUSE_PASSWORD=GENERATED_BY_INIT_ENV
LANGFUSE_MINIO_ROOT_USER=langfuse
LANGFUSE_MINIO_ROOT_PASSWORD=GENERATED_BY_INIT_ENV
LANGFUSE_SALT=GENERATED_BY_INIT_ENV
LANGFUSE_ENCRYPTION_KEY=GENERATED_BY_INIT_ENV
LANGFUSE_NEXTAUTH_SECRET=GENERATED_BY_INIT_ENV
PGADMIN_DEFAULT_EMAIL=admin@example.local
PGADMIN_DEFAULT_PASSWORD=GENERATED_BY_INIT_ENV
REDISINSIGHT_ENCRYPTION_KEY=GENERATED_BY_INIT_ENV
APP_POSTGRES_MEMORY=1g
APP_REDIS_MEMORY=512m
CHROMA_MEMORY=4g
OPENSEARCH_MEMORY=6g
OPENSEARCH_JAVA_OPTS=-Xms2g -Xmx2g
OPENSEARCH_DASHBOARDS_MEMORY=1g
LANGFUSE_WEB_MEMORY=2g
LANGFUSE_WORKER_MEMORY=2g
LANGFUSE_POSTGRES_MEMORY=2g
LANGFUSE_REDIS_MEMORY=512m
CLICKHOUSE_MEMORY=6g
MINIO_MEMORY=1g
PGADMIN_MEMORY=512m
REDISINSIGHT_MEMORY=512m
```

Create `remote.env.example`:

```dotenv
REMOTE_HOST=remote-infra-stack
REMOTE_USER=
REMOTE_PORT=22
REMOTE_IDENTITY_FILE=
REMOTE_ROOT=remote-infra-stack
LOCAL_POSTGRES_PORT=5432
LOCAL_REDIS_PORT=6379
LOCAL_CHROMA_PORT=18000
LOCAL_OPENSEARCH_PORT=9200
LOCAL_OPENSEARCH_DASHBOARDS_PORT=5601
LOCAL_LANGFUSE_PORT=3000
LOCAL_PGADMIN_PORT=5050
LOCAL_REDISINSIGHT_PORT=5540
LOCAL_MINIO_API_PORT=9090
LOCAL_MINIO_CONSOLE_PORT=9091
```

Add deterministic non-placeholder values to the two fixture env files. Use 32-character mixed-case values for OpenSearch and 64 hexadecimal characters for `LANGFUSE_ENCRYPTION_KEY`.

Add a minimal `compose.yaml` containing only `name: remote-infra-stack` so the contract can pass before profile tasks add services.

- [ ] **Step 4: Run the repository contract test**

Run: `python -m unittest tests.test_repository_contract -v`

Expected: PASS.

- [ ] **Step 5: Validate the image catalog contract without pulling locally**

Run:

```bash
python -m unittest tests.test_repository_contract -v
```

Expected: PASS with 13 non-`latest` image references. Registry manifest and `linux/amd64` verification is intentionally deferred to the remote Docker host in Task 7; this task must not pull or start anything locally.

- [ ] **Step 6: Commit the repository contract**

```bash
git add .gitignore .gitattributes versions.env .env.example remote.env.example compose.yaml tests
git commit -m "chore: establish stack configuration contract"
```

---

### Task 2: Generate and validate local secrets on Bash and PowerShell

**Files:**
- Create: `scripts/init-env.sh`
- Create: `scripts/init-env.ps1`
- Create: `tests/test_env_generation.py`
- Modify: `tests/helpers.py`

**Interfaces:**
- Produces: `scripts/init-env.sh [--output PATH] [--force]`
- Produces: `scripts/init-env.ps1 [-OutputPath PATH] [-Force]`
- Both write the same key set and never overwrite an existing file without force.
- `LANGFUSE_ENCRYPTION_KEY` is exactly 64 lowercase hexadecimal characters.

- [ ] **Step 1: Write failing cross-platform generation tests**

```python
# tests/test_env_generation.py
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import read_env, repo_path


class EnvGenerationTests(unittest.TestCase):
    def assert_contract(self, output: Path):
        generated = read_env(output)
        expected = read_env(repo_path(".env.example"))
        self.assertEqual(set(expected), set(generated))
        self.assertNotIn("GENERATED_BY_INIT_ENV", generated.values())
        self.assertRegex(generated["LANGFUSE_ENCRYPTION_KEY"], r"^[0-9a-f]{64}$")
        self.assertEqual("app", generated["APP_POSTGRES_USER"])
        self.assertEqual("admin@example.local", generated["PGADMIN_DEFAULT_EMAIL"])

    def test_bash_generator(self):
        if not shutil.which("bash"):
            self.skipTest("bash is not installed")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / ".env"
            subprocess.run(["bash", str(repo_path("scripts/init-env.sh")), "--output", str(output)], check=True)
            self.assert_contract(output)
            second = subprocess.run(["bash", str(repo_path("scripts/init-env.sh")), "--output", str(output)])
            self.assertNotEqual(0, second.returncode)

    def test_powershell_generator(self):
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            self.skipTest("PowerShell is not installed")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / ".env"
            subprocess.run([shell, "-NoProfile", "-File", str(repo_path("scripts/init-env.ps1")), "-OutputPath", str(output)], check=True)
            self.assert_contract(output)
```

- [ ] **Step 2: Run tests to verify both implementations are missing**

Run: `python -m unittest tests.test_env_generation -v`

Expected: FAIL for each locally available shell because its generator file is absent.

- [ ] **Step 3: Implement the Bash generator**

Use strict mode, `umask 077`, `openssl rand`, explicit argument parsing, and a temporary file renamed atomically:

```bash
#!/usr/bin/env bash
set -euo pipefail
umask 077

output=.env
force=false
while (($#)); do
  case "$1" in
    --output) output=$2; shift 2 ;;
    --force) force=true; shift ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if [[ -e "$output" && "$force" != true ]]; then
  printf 'Refusing to overwrite %s without --force\n' "$output" >&2
  exit 1
fi

secret() { openssl rand -hex "$1"; }
```

Write every key in `.env.example`, preserving non-secret defaults and replacing `GENERATED_BY_INIT_ENV` with independently generated secrets. Generate the OpenSearch password with upper case, lower case, digits, and `!` so its strength policy is satisfied.

- [ ] **Step 4: Implement the PowerShell generator**

Use `[System.Security.Cryptography.RandomNumberGenerator]`, `Set-StrictMode`, `$ErrorActionPreference = 'Stop'`, `New-TemporaryFile`, and `Move-Item`:

```powershell
[CmdletBinding()]
param(
    [string]$OutputPath = '.env',
    [switch]$Force
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-HexSecret([int]$ByteCount) {
    $bytes = New-Object byte[] $ByteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}
```

Match the Bash key order and overwrite semantics exactly.

- [ ] **Step 5: Run generation and repository tests**

Run: `python -m unittest tests.test_env_generation tests.test_repository_contract -v`

Expected: PASS; a missing Bash or PowerShell executable may produce an explicit SKIP only for that platform implementation.

- [ ] **Step 6: Commit secret generation**

```bash
git add scripts/init-env.sh scripts/init-env.ps1 tests/test_env_generation.py tests/helpers.py
git commit -m "feat: generate portable stack secrets"
```

---

### Task 3: Implement the `core` and `vector` Compose profiles

**Files:**
- Modify: `compose.yaml`
- Create: `tests/test_compose_core_vector.py`
- Modify: `tests/helpers.py`

**Interfaces:**
- Produces: `helpers.render_compose(*profiles: str) -> dict`
- Produces: Compose services `app-postgres`, `app-redis`, and `chroma`
- Produces: named volumes `app_postgres_data`, `app_redis_data`, and `chroma_data`

- [ ] **Step 1: Add the failing Compose contract tests**

```python
# additions to tests/helpers.py
import json
import os
import subprocess


def render_compose(*profiles: str) -> dict:
    command = [
        "docker", "compose",
        "--env-file", str(repo_path("versions.env")),
        "--env-file", str(repo_path("tests/fixtures/stack.env")),
    ]
    for profile in profiles:
        command.extend(["--profile", profile])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(command, cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)
```

```python
# tests/test_compose_core_vector.py
import unittest
from tests.helpers import render_compose


class CoreVectorComposeTests(unittest.TestCase):
    def test_core_services_are_isolated_and_persistent(self):
        model = render_compose("core")
        self.assertEqual({"app-postgres", "app-redis"}, set(model["services"]))
        self.assertEqual("127.0.0.1", model["services"]["app-postgres"]["ports"][0]["host_ip"])
        self.assertEqual(15432, model["services"]["app-postgres"]["ports"][0]["published"])
        self.assertEqual(16379, model["services"]["app-redis"]["ports"][0]["published"])
        self.assertIn("healthcheck", model["services"]["app-postgres"])
        self.assertIn("healthcheck", model["services"]["app-redis"])

    def test_vector_uses_nonstandard_host_port(self):
        model = render_compose("vector")
        chroma = model["services"]["chroma"]
        self.assertEqual(18000, chroma["ports"][0]["published"])
        self.assertEqual(8000, chroma["ports"][0]["target"])
        self.assertEqual("/data", chroma["environment"]["CHROMA_PERSIST_PATH"])
        self.assertIn("healthcheck", chroma)
```

- [ ] **Step 2: Run the tests to verify missing services fail**

Run: `python -m unittest tests.test_compose_core_vector -v`

Expected: FAIL because the services do not exist.

- [ ] **Step 3: Add the stable network and `core` services**

Implement `app-postgres` and `app-redis` with profile `core`, loopback port bindings, named volumes, `restart: unless-stopped`, the approved memory limits, and health checks:

```yaml
services:
  app-postgres:
    profiles: [core]
    image: ${APP_POSTGRES_IMAGE:?set in versions.env}
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${APP_POSTGRES_USER:?set in .env}
      POSTGRES_DB: ${APP_POSTGRES_DB:?set in .env}
      POSTGRES_PASSWORD: ${APP_POSTGRES_PASSWORD:?set in .env}
      TZ: UTC
      PGTZ: UTC
    ports:
      - "127.0.0.1:15432:5432"
    volumes:
      - app_postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 5s
      retries: 12
    mem_limit: ${APP_POSTGRES_MEMORY:-1g}
    networks: [infra]

  app-redis:
    profiles: [core]
    image: ${APP_REDIS_IMAGE:?set in versions.env}
    restart: unless-stopped
    environment:
      APP_REDIS_PASSWORD: ${APP_REDIS_PASSWORD:?set in .env}
    command: ["sh", "-c", "exec redis-server --appendonly yes --maxmemory-policy noeviction --requirepass \"$$APP_REDIS_PASSWORD\""]
    ports:
      - "127.0.0.1:16379:6379"
    volumes:
      - app_redis_data:/data
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a \"$$APP_REDIS_PASSWORD\" ping | grep -q PONG"]
      interval: 5s
      timeout: 5s
      retries: 12
    mem_limit: ${APP_REDIS_MEMORY:-512m}
    networks: [infra]
```

- [ ] **Step 4: Add the `vector` service and top-level resources**

```yaml
  chroma:
    profiles: [vector]
    image: ${CHROMA_IMAGE:?set in versions.env}
    restart: unless-stopped
    environment:
      CHROMA_PERSIST_PATH: /data
      CHROMA_ALLOW_RESET: "false"
    ports:
      - "127.0.0.1:18000:8000"
    volumes:
      - chroma_data:/data
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8000/api/v2/heartbeat >/dev/null || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 12
    mem_limit: ${CHROMA_MEMORY:-4g}
    networks: [infra]

networks:
  infra:
    name: remote-infra-stack-infra

volumes:
  app_postgres_data:
    name: remote-infra-stack-app-postgres-data
  app_redis_data:
    name: remote-infra-stack-app-redis-data
  chroma_data:
    name: remote-infra-stack-chroma-data
```

- [ ] **Step 5: Run the profile tests and inspect rendered configuration**

Run: `python -m unittest tests.test_compose_core_vector -v`

Run: `docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile core --profile vector config --quiet`

Expected: both commands PASS.

- [ ] **Step 6: Commit core and vector profiles**

```bash
git add compose.yaml tests/helpers.py tests/test_compose_core_vector.py
git commit -m "feat: add core and vector profiles"
```

---

### Task 4: Implement the secured `search` profile

**Files:**
- Modify: `compose.yaml`
- Create: `config/opensearch/opensearch.yml`
- Create: `tests/test_compose_search.py`

**Interfaces:**
- Produces: `opensearch` HTTPS API on VM loopback port 9200
- Produces: `opensearch-dashboards` UI on VM loopback port 5601
- Requires: `OPENSEARCH_INITIAL_ADMIN_PASSWORD`
- Requires host: `vm.max_map_count >= 262144`

- [ ] **Step 1: Write the failing search profile test**

```python
# tests/test_compose_search.py
import unittest
from tests.helpers import render_compose


class SearchComposeTests(unittest.TestCase):
    def test_search_keeps_security_and_host_limits(self):
        model = render_compose("search")
        search = model["services"]["opensearch"]
        dashboards = model["services"]["opensearch-dashboards"]
        self.assertNotIn("DISABLE_SECURITY_PLUGIN", search["environment"])
        self.assertEqual("single-node", search["environment"]["discovery.type"])
        self.assertEqual("127.0.0.1", search["ports"][0]["host_ip"])
        self.assertEqual(9200, search["ports"][0]["published"])
        self.assertEqual(5601, dashboards["ports"][0]["published"])
        self.assertEqual(["opensearch"], list(dashboards["depends_on"]))
        self.assertEqual(-1, search["ulimits"]["memlock"]["soft"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_compose_search -v`

Expected: FAIL because `opensearch` is absent.

- [ ] **Step 3: Add OpenSearch configuration and service**

Create `config/opensearch/opensearch.yml`:

```yaml
cluster.name: remote-infra-stack
network.host: 0.0.0.0
plugins.security.ssl.http.enabled: true
```

Add `opensearch` with `discovery.type=single-node`, `bootstrap.memory_lock=true`, `OPENSEARCH_JAVA_OPTS`, `OPENSEARCH_INITIAL_ADMIN_PASSWORD`, loopback ports, named data volume, config read-only mount, memlock/nofile ulimits, 6 GiB container limit, and an authenticated `curl -k` health check.

```yaml
    healthcheck:
      test: ["CMD-SHELL", "curl -ksS -u admin:\"$$OPENSEARCH_INITIAL_ADMIN_PASSWORD\" https://127.0.0.1:9200/_cluster/health >/dev/null"]
      interval: 10s
      timeout: 5s
      retries: 18
      start_period: 30s
```

- [ ] **Step 4: Add matching OpenSearch Dashboards**

Use the matching image variable, depend on healthy OpenSearch, set `OPENSEARCH_HOSTS` to `https://opensearch:9200`, use the admin credentials, disable certificate verification only for the bundled development certificate, publish `127.0.0.1:5601:5601`, add a `/api/status` health check, and apply the 1 GiB limit.

- [ ] **Step 5: Add and validate the named search volume**

Add volume name `remote-infra-stack-opensearch-data`, render the profile, and confirm no search service uses `DISABLE_SECURITY_PLUGIN=true`.

Run: `python -m unittest tests.test_compose_search -v`

Expected: PASS.

- [ ] **Step 6: Commit the search profile**

```bash
git add compose.yaml config/opensearch/opensearch.yml tests/test_compose_search.py
git commit -m "feat: add secured search profile"
```

---

### Task 5: Implement the isolated Langfuse observability profile

**Files:**
- Modify: `compose.yaml`
- Create: `tests/test_compose_observability.py`

**Interfaces:**
- Produces: `langfuse-web` on VM loopback 3000
- Produces: MinIO API/console on VM loopback 9090/9091
- Keeps: Langfuse PostgreSQL, Redis, ClickHouse, and worker private to Compose network
- Consumes: every `LANGFUSE_*` secret in `.env`

- [ ] **Step 1: Write the failing observability topology test**

```python
# tests/test_compose_observability.py
import unittest
from tests.helpers import render_compose


class ObservabilityComposeTests(unittest.TestCase):
    expected = {
        "langfuse-web", "langfuse-worker", "langfuse-postgres",
        "langfuse-redis", "clickhouse", "minio",
    }

    def test_observability_topology_is_complete_and_isolated(self):
        model = render_compose("observability")
        self.assertEqual(self.expected, set(model["services"]))
        for private in ("langfuse-worker", "langfuse-postgres", "langfuse-redis", "clickhouse"):
            self.assertFalse(model["services"][private].get("ports"), private)
        self.assertEqual(3000, model["services"]["langfuse-web"]["ports"][0]["published"])
        self.assertEqual({9090, 9091}, {item["published"] for item in model["services"]["minio"]["ports"]})

    def test_web_and_worker_wait_for_healthy_dependencies(self):
        model = render_compose("observability")
        for service in ("langfuse-web", "langfuse-worker"):
            dependencies = model["services"][service]["depends_on"]
            self.assertEqual(
                {"langfuse-postgres", "langfuse-redis", "clickhouse", "minio"},
                set(dependencies),
            )
            self.assertTrue(all(value["condition"] == "service_healthy" for value in dependencies.values()))
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m unittest tests.test_compose_observability -v`

Expected: FAIL because Langfuse services are absent.

- [ ] **Step 3: Add Langfuse private datastores**

Add profile `observability` to `langfuse-postgres`, `langfuse-redis`, `clickhouse`, and `minio`. Each uses a named volume, approved memory limit, `restart: unless-stopped`, private network only, and a health check. Use:

```text
remote-infra-stack-langfuse-postgres-data
remote-infra-stack-langfuse-redis-data
remote-infra-stack-clickhouse-data
remote-infra-stack-clickhouse-logs
remote-infra-stack-minio-data
```

Configure Redis with authenticated `noeviction`; ClickHouse with the configured user/password; and MinIO with `server --address :9000 --console-address :9001 /data`. Publish MinIO only as `127.0.0.1:9090:9000` and `127.0.0.1:9091:9001`.

- [ ] **Step 4: Add the shared Langfuse environment anchor**

Define a YAML extension field `x-langfuse-environment` and include these exact connection contracts:

```yaml
x-langfuse-environment: &langfuse-environment
  DATABASE_URL: postgresql://${LANGFUSE_POSTGRES_USER}:${LANGFUSE_POSTGRES_PASSWORD}@langfuse-postgres:5432/${LANGFUSE_POSTGRES_DB}
  SALT: ${LANGFUSE_SALT}
  ENCRYPTION_KEY: ${LANGFUSE_ENCRYPTION_KEY}
  CLICKHOUSE_URL: http://clickhouse:8123
  CLICKHOUSE_MIGRATION_URL: clickhouse://clickhouse:9000
  CLICKHOUSE_USER: ${LANGFUSE_CLICKHOUSE_USER}
  CLICKHOUSE_PASSWORD: ${LANGFUSE_CLICKHOUSE_PASSWORD}
  REDIS_HOST: langfuse-redis
  REDIS_PORT: "6379"
  REDIS_AUTH: ${LANGFUSE_REDIS_PASSWORD}
  LANGFUSE_S3_EVENT_UPLOAD_BUCKET: langfuse
  LANGFUSE_S3_EVENT_UPLOAD_REGION: auto
  LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID: ${LANGFUSE_MINIO_ROOT_USER}
  LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY: ${LANGFUSE_MINIO_ROOT_PASSWORD}
  LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT: http://minio:9000
  LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE: "true"
  LANGFUSE_S3_MEDIA_UPLOAD_BUCKET: langfuse
  LANGFUSE_S3_MEDIA_UPLOAD_REGION: auto
  LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID: ${LANGFUSE_MINIO_ROOT_USER}
  LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY: ${LANGFUSE_MINIO_ROOT_PASSWORD}
  LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE: "true"
  TELEMETRY_ENABLED: "false"
```

- [ ] **Step 5: Add Langfuse worker and web services**

Both use the shared environment and wait for all four healthy dependencies. Worker uses the internal MinIO endpoint and remains unpublished. Web adds:

```yaml
environment:
  <<: *langfuse-environment
  NEXTAUTH_URL: http://localhost:3000
  NEXTAUTH_SECRET: ${LANGFUSE_NEXTAUTH_SECRET}
  LANGFUSE_S3_MEDIA_UPLOAD_ENDPOINT: http://localhost:9090
ports:
  - "127.0.0.1:3000:3000"
```

Add readiness checks using Node, which is guaranteed to exist in both application images:

```yaml
healthcheck:
  test: ["CMD", "node", "-e", "fetch('http://127.0.0.1:3000/api/public/ready').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))"]
  interval: 10s
  timeout: 5s
  retries: 18
```

Use the same form for the worker with `http://127.0.0.1:3030/api/health`. Do not install packages inside running containers.

- [ ] **Step 6: Run observability and combined-model validation**

Run: `python -m unittest tests.test_compose_observability -v`

Run: `docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile core --profile vector --profile search --profile observability config --quiet`

Expected: PASS.

- [ ] **Step 7: Commit observability**

```bash
git add compose.yaml tests/test_compose_observability.py
git commit -m "feat: add isolated langfuse profile"
```

---

### Task 6: Add optional administration tools and global Compose invariants

**Files:**
- Modify: `compose.yaml`
- Create: `tests/test_compose_tools.py`
- Create: `tests/test_compose_invariants.py`

**Interfaces:**
- Produces: pgAdmin loopback UI on 5050
- Produces: RedisInsight loopback UI on 5540
- Requires: `core` selected whenever `tools` is selected

- [ ] **Step 1: Write failing tool and invariant tests**

```python
# tests/test_compose_tools.py
import unittest
from tests.helpers import render_compose


class ToolComposeTests(unittest.TestCase):
    def test_tools_join_core_databases(self):
        model = render_compose("core", "tools")
        self.assertEqual(
            {"app-postgres", "app-redis", "pgadmin", "redisinsight"},
            set(model["services"]),
        )
        self.assertIn("app-postgres", model["services"]["pgadmin"]["depends_on"])
        self.assertIn("app-redis", model["services"]["redisinsight"]["depends_on"])
```

```python
# tests/test_compose_invariants.py
import unittest
from tests.helpers import render_compose


class ComposeInvariantTests(unittest.TestCase):
    def test_all_published_ports_are_loopback(self):
        model = render_compose("core", "vector", "search", "observability", "tools")
        for name, service in model["services"].items():
            for port in service.get("ports", []):
                self.assertEqual("127.0.0.1", port["host_ip"], name)

    def test_all_stateful_services_use_named_volumes_and_healthchecks(self):
        model = render_compose("core", "vector", "search", "observability", "tools")
        stateful = {
            "app-postgres", "app-redis", "chroma", "opensearch",
            "langfuse-postgres", "langfuse-redis", "clickhouse", "minio",
        }
        for name in stateful:
            self.assertTrue(model["services"][name].get("volumes"), name)
            self.assertTrue(model["services"][name].get("healthcheck"), name)
```

- [ ] **Step 2: Run tests to verify tool services fail**

Run: `python -m unittest tests.test_compose_tools tests.test_compose_invariants -v`

Expected: FAIL because pgAdmin and RedisInsight are absent.

- [ ] **Step 3: Add pgAdmin and RedisInsight**

Configure pgAdmin with `PGADMIN_LISTEN_PORT=5050`, required email/password, persistent `/var/lib/pgadmin`, loopback port 5050, 512 MiB limit, and healthy `app-postgres` dependency. Configure RedisInsight with `RI_APP_PORT=5540`, `RI_ENCRYPTION_KEY`, a persistent `/data` volume, loopback 5540, 512 MiB limit, healthy `app-redis` dependency, and its `/api/health/` health endpoint.

Preconfigure RedisInsight to use `app-redis:6379` and `APP_REDIS_PASSWORD`; leave pgAdmin database password entry to the user so it is not duplicated in pgAdmin's configuration database.

- [ ] **Step 4: Run every Compose test**

Run: `python -m unittest tests.test_compose_core_vector tests.test_compose_search tests.test_compose_observability tests.test_compose_tools tests.test_compose_invariants -v`

Expected: PASS.

- [ ] **Step 5: Commit tools and invariants**

```bash
git add compose.yaml tests/test_compose_tools.py tests/test_compose_invariants.py
git commit -m "feat: add optional admin tools"
```

---

### Task 7: Build the capability-gated Ubuntu bootstrap

**Files:**
- Create: `scripts/remote/bootstrap-host.sh`
- Create: `tests/fixtures/os-release/ubuntu-22.04`
- Create: `tests/fixtures/os-release/ubuntu-24.04`
- Create: `tests/fixtures/os-release/ubuntu-26.04`
- Create: `tests/fixtures/os-release/ubuntu-future-lts`
- Create: `tests/fixtures/os-release/debian`
- Create: `tests/test_bootstrap.py`

**Interfaces:**
- Produces: `bootstrap-host.sh --check` for non-mutating host validation
- Produces: `bootstrap-host.sh --install` for idempotent installation
- Test hooks: `STACK_OS_RELEASE_FILE`, `STACK_DOCKER_REPO_BASE`, and `STACK_BOOTSTRAP_DRY_RUN=1`

- [ ] **Step 1: Write failing OS detection and future-release tests**

```python
# tests/test_bootstrap.py
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import repo_path


class BootstrapTests(unittest.TestCase):
    def run_check(self, fixture: str, repo_root: Path):
        env = os.environ.copy()
        env.update({
            "STACK_OS_RELEASE_FILE": str(repo_path(f"tests/fixtures/os-release/{fixture}")),
            "STACK_DOCKER_REPO_BASE": repo_root.as_uri(),
            "STACK_BOOTSTRAP_DRY_RUN": "1",
        })
        return subprocess.run(
            ["bash", str(repo_path("scripts/remote/bootstrap-host.sh")), "--check"],
            env=env, capture_output=True, text=True,
        )

    def test_supported_and_future_ubuntu_are_repository_gated(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for codename in ("jammy", "noble", "resolute", "future"):
                release = repo / "dists" / codename / "Release"
                release.parent.mkdir(parents=True)
                release.write_text("Origin: Docker\n", encoding="utf-8")
            for fixture in ("ubuntu-22.04", "ubuntu-24.04", "ubuntu-26.04", "ubuntu-future-lts"):
                self.assertEqual(0, self.run_check(fixture, repo).returncode, fixture)

    def test_non_ubuntu_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_check("debian", Path(directory))
            self.assertNotEqual(0, result.returncode)
            self.assertIn("ID=ubuntu", result.stderr)
```

- [ ] **Step 2: Run the test to verify the bootstrap is missing**

Run: `python -m unittest tests.test_bootstrap -v`

Expected: FAIL because the script and fixtures do not exist.

- [ ] **Step 3: Create exact os-release fixtures**

Each Ubuntu fixture defines `ID=ubuntu`, `VERSION_ID`, `VERSION_CODENAME`, and `UBUNTU_CODENAME`. Use codenames `jammy`, `noble`, `resolute`, and `future`. The Debian fixture uses `ID=debian`.

- [ ] **Step 4: Implement non-mutating capability detection**

The script must source the selected os-release file, require Ubuntu, require `x86_64`/`amd64`, check `systemctl`, `apt-get`, `sudo`, and `curl`, and probe:

```bash
docker_release_url="${STACK_DOCKER_REPO_BASE:-https://download.docker.com/linux/ubuntu}/dists/${UBUNTU_CODENAME:-$VERSION_CODENAME}/Release"
curl --fail --silent --show-error --location "$docker_release_url" >/dev/null
```

Do not hardcode an upper Ubuntu version. Return an actionable error containing `VERSION_ID` and codename if the repository probe fails.

- [ ] **Step 5: Implement idempotent installation**

For `--install`, follow Docker's official apt `.sources` method, remove conflicting packages if installed, install the exact prerequisites in the design, install Docker CE/CLI/containerd/buildx/Compose, enable Docker, add `$SUDO_USER` or the current login user to group `docker`, and write:

```text
/etc/sysctl.d/99-remote-infra-stack.conf
vm.max_map_count=262144
```

Apply it with `sysctl --system`, verify `docker version`, `docker compose version`, `systemctl is-active docker`, architecture, and the kernel value. In dry-run mode, print commands but execute no privileged mutation.

- [ ] **Step 6: Run bootstrap tests and Bash syntax validation**

Run: `bash -n scripts/remote/bootstrap-host.sh`

Run: `python -m unittest tests.test_bootstrap -v`

Expected: PASS.

- [ ] **Step 7: Bootstrap the configured GCP test VM and verify every image manifest remotely**

Run the new bootstrap against the configured Ubuntu 26.04 GCP VM. On the VM, run `docker buildx imagetools inspect` for every value in `versions.env` and require a `linux/amd64` manifest. If a candidate registry tag does not exist, replace it with the exact release tag published by that image owner and repeat all 13 checks. Record each reported manifest-list digest by changing the value to `image:tag@sha256:digest`, then repeat the inspections against the pinned references. Do not start containers yet.

Expected host facts: Ubuntu 26.04 LTS, `x86_64`, Docker daemon active, Compose v2 available, `vm.max_map_count=262144`, and all image manifests resolvable.

- [ ] **Step 8: Commit bootstrap support and any verified tag corrections**

```bash
git add scripts/remote/bootstrap-host.sh tests/fixtures/os-release tests/test_bootstrap.py
git commit -m "feat: bootstrap supported ubuntu hosts"
```

---

### Task 8: Implement remote Compose, health, and release lifecycle commands

**Files:**
- Create: `scripts/remote/compose.sh`
- Create: `scripts/remote/health.sh`
- Create: `scripts/remote/stack.sh`
- Create: `scripts/remote/deploy-release.sh`
- Create: `tests/fakes/docker`
- Create: `tests/test_remote_runtime.py`
- Create: `tests/test_release_lifecycle.py`

**Interfaces:**
- `compose.sh profiles... -- compose-arguments...` constructs the canonical command with both env files.
- `health.sh profiles...` verifies selected service endpoints and container health.
- `stack.sh ACTION [arguments...]` implements `up`, `stop`, `down`, `status`, `logs`, and `destroy`.
- `deploy-release.sh --root PATH --archive PATH --checksum PATH --profiles CSV` activates a release.

- [ ] **Step 1: Write failing profile and destructive-operation runtime tests**

```python
# tests/test_remote_runtime.py
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import repo_path


class RemoteRuntimeTests(unittest.TestCase):
    def test_tools_without_core_is_rejected(self):
        result = subprocess.run(
            ["bash", str(repo_path("scripts/remote/stack.sh")), "up", "tools"],
            env={**os.environ, "STACK_TEST_MODE": "1"}, capture_output=True, text=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("tools requires core", result.stderr)

    def test_down_never_adds_volume_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "docker.log"
            env = {**os.environ, "STACK_TEST_MODE": "1", "STACK_DOCKER_LOG": str(log)}
            subprocess.run(["bash", str(repo_path("scripts/remote/stack.sh")), "down"], env=env, check=True)
            self.assertNotIn(" -v", log.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Write the failing atomic-release test**

Create a temporary Git archive fixture containing `compose.yaml`, `versions.env`, and the remote scripts. Assert checksum mismatch fails before extraction; a valid checksum creates `releases/<name>`, invokes fake Compose, updates `current`, and prunes only successful releases beyond three.

- [ ] **Step 3: Run tests to verify remote scripts are absent**

Run: `python -m unittest tests.test_remote_runtime tests.test_release_lifecycle -v`

Expected: FAIL.

- [ ] **Step 4: Implement canonical profile validation and Compose construction**

In `compose.sh`, accept only the five profile names, reject duplicates/unknown values, require `core` with `tools`, and build:

```bash
docker compose \
  --env-file "$release_dir/versions.env" \
  --env-file "$stack_root/runtime/.env" \
  --project-directory "$release_dir" \
  --profile core --profile vector \
  "$@"
```

Add only the selected `--profile` flags. Support `DOCKER_BIN` for tests.

- [ ] **Step 5: Implement health and remote stack operations**

`health.sh` maps profiles to exact service names and first requires `docker compose ps --format json` to report each selected container as healthy. It then checks PostgreSQL/Redis through `docker compose exec -T`, Chroma at `http://127.0.0.1:18000/api/v2/heartbeat`, OpenSearch with authenticated HTTPS, Langfuse/MinIO local HTTP endpoints, and tool health endpoints.

Before `up`, render Compose as JSON and use `jq` to sum selected services' `mem_limit` values. Add 2 GiB for the host and warn when that total exceeds `/proc/meminfo` `MemTotal`; do not reject the deployment. Check `df --output=avail -B1 "$stack_root"`: fail below 10 GiB free and warn below 20 GiB free.

`stack.sh` behavior:

```text
up profiles...       compose up -d --wait
stop profiles...     expand profile service names; compose stop <services>
down                 compose --profile '*' down (never -v)
status               compose --profile '*' ps plus free/df/docker system df
logs target          expand a profile or validate one service; compose logs -f
destroy target token require token DESTROY-<target>; compose --profile '*' down -v
```

- [ ] **Step 6: Implement the locked release receiver**

Use `flock` on `$root/runtime/deploy.lock`; validate every supplied path remains below `$root`; verify with `sha256sum -c`; extract into `releases/<archive-base>`; copy runtime `.env` with mode `0600`; run Compose `config --quiet`, `pull`, `up -d --wait`, and `health.sh`; atomically replace `current` with `ln -sfn`; then sort successful release names and remove only entries older than the newest three.

The script must leave `current` and existing volumes unchanged on validation/checksum failure. On a post-start health failure, leave the extracted release and exit nonzero without changing `current` or pruning.

- [ ] **Step 7: Run runtime and lifecycle tests**

Run: `python -m unittest tests.test_remote_runtime tests.test_release_lifecycle -v`

Run: `bash -n scripts/remote/compose.sh scripts/remote/health.sh scripts/remote/stack.sh scripts/remote/deploy-release.sh`

Expected: PASS.

- [ ] **Step 8: Commit remote lifecycle implementation**

```bash
git add scripts/remote tests/fakes tests/test_remote_runtime.py tests/test_release_lifecycle.py
git commit -m "feat: manage remote stack releases"
```

---

### Task 9: Implement the Bash local operator interface

**Files:**
- Create: `scripts/lib/common.sh`
- Create: `scripts/bootstrap.sh`
- Create: `scripts/deploy.sh`
- Create: `scripts/stack.sh`
- Create: `scripts/check.sh`
- Create: `tests/fakes/ssh`
- Create: `tests/fakes/scp`
- Create: `tests/test_bash_operator.py`

**Interfaces:**
- `common.sh` produces `load_remote_env`, `validate_profiles`, `ssh_args`, `scp_args`, and `ssh_target`.
- Local commands consume ignored `remote.env` and `.env` from repository root.
- `deploy.sh profiles...` archives only committed Git `HEAD`.

- [ ] **Step 1: Write failing Bash dry-run tests**

Tests prepend `tests/fakes` to `PATH`, point `STACK_REMOTE_ENV` at the fixture, and assert:

```text
bootstrap.sh -> scp bootstrap-host.sh, then ssh bash --install
deploy.sh core vector -> git status check, git archive, checksum, scp archive/checksum/.env/deploy script, ssh deploy-release with core,vector
stack.sh status -> ssh current/scripts/remote/stack.sh status
check.sh -> validates secrets and profiles without ssh mutation
```

Assert a dirty Git tree causes deployment failure before fake `scp` is called. Use a temporary Git repository in the test rather than dirtying the real checkout.

- [ ] **Step 2: Run tests to verify Bash wrappers are missing**

Run: `python -m unittest tests.test_bash_operator -v`

Expected: FAIL.

- [ ] **Step 3: Implement strict config and SSH argument helpers**

`common.sh` must parse only `KEY=VALUE` lines from `remote.env` without `eval`; require a relative `REMOTE_ROOT` without `..`; construct `[user@]host`; and add optional `-p`/`-P` and `-i` arguments. It validates the exact profile set and `tools` dependency.

- [ ] **Step 4: Implement bootstrap and deployment**

`bootstrap.sh` uploads `bootstrap-host.sh` to a unique incoming name and invokes `sudo bash ... --install` over SSH.

`deploy.sh` requires `git diff --quiet`, `git diff --cached --quiet`, and no untracked files except ignored files; uses `git archive --format=tar.gz`; creates a portable SHA-256 file using `sha256sum` or `shasum -a 256`; uploads archive/checksum/`.env`/`deploy-release.sh`; then invokes the remote receiver with the selected CSV profiles. Temporary artifacts live under `.artifacts/` and are removed through `trap`.

- [ ] **Step 5: Implement stack and check wrappers**

`stack.sh` forwards supported actions to the current remote script. For `destroy`, it asks the operator to type the configured target and then `DESTROY-<target>` before sending the token remotely.

`check.sh` validates required local commands, configuration files, placeholders, profile combinations, Git state, and script syntax. If local Docker Compose exists, it also renders the selected model; absence of a local Docker daemon is a warning because remote validation is authoritative.

- [ ] **Step 6: Run Bash wrapper tests and syntax checks**

Run: `python -m unittest tests.test_bash_operator -v`

Run: `bash -n scripts/*.sh scripts/lib/*.sh`

Expected: PASS.

- [ ] **Step 7: Commit the Bash operator interface**

```bash
git add scripts tests/fakes/ssh tests/fakes/scp tests/test_bash_operator.py
git commit -m "feat: add bash operator commands"
```

---

### Task 10: Implement the equivalent PowerShell operator interface

**Files:**
- Create: `scripts/lib/Common.psm1`
- Create: `scripts/bootstrap.ps1`
- Create: `scripts/deploy.ps1`
- Create: `scripts/stack.ps1`
- Create: `scripts/check.ps1`
- Create: `tests/test_powershell_operator.py`

**Interfaces:**
- PowerShell commands accept the same actions and ordered profile arguments as Bash.
- `Common.psm1` produces `Import-RemoteEnv`, `Assert-Profiles`, `Get-SshArguments`, `Get-ScpArguments`, and `Get-SshTarget`.
- Both implementations produce semantically identical fake SSH/SCP logs.

- [ ] **Step 1: Write failing PowerShell parity tests**

For each locally available PowerShell executable, invoke scripts with the same fixtures/fake commands as Task 9. Normalize path separators and assert the operation, remote target, profile CSV, archive name pattern, and uploaded file set equal the Bash golden expectations.

- [ ] **Step 2: Run tests to verify PowerShell wrappers are missing**

Run: `python -m unittest tests.test_powershell_operator -v`

Expected: FAIL on a machine with PowerShell.

- [ ] **Step 3: Implement safe PowerShell config parsing**

Use `Set-StrictMode -Version Latest` and `$ErrorActionPreference = 'Stop'`. Parse `KEY=VALUE` without `Invoke-Expression`. Reject duplicate keys, unknown profile names, `tools` without `core`, absolute `REMOTE_ROOT`, and parent traversal.

- [ ] **Step 4: Implement bootstrap, deploy, stack, and check**

Use `git archive`, `Get-FileHash -Algorithm SHA256`, `scp`, and `ssh`; never construct one shell command string when an argument array is possible. Match Bash clean-tree, secret upload, release naming, confirmation, and local-Docker-warning semantics.

PowerShell's `deploy.ps1` must convert local paths to arguments understood by Windows OpenSSH and upload remote scripts without rewriting their LF endings.

- [ ] **Step 5: Validate PowerShell syntax and parity**

Run:

```powershell
$errors = $null
Get-ChildItem scripts -Recurse -Include *.ps1,*.psm1 | ForEach-Object {
    [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$null, [ref]$errors)
    if ($errors) { throw ($errors | Out-String) }
}
```

Run: `python -m unittest tests.test_powershell_operator -v`

Expected: PASS.

- [ ] **Step 6: Commit PowerShell parity**

```bash
git add scripts tests/test_powershell_operator.py
git commit -m "feat: add powershell operator commands"
```

---

### Task 11: Implement profile-aware SSH tunnels on Bash and PowerShell

**Files:**
- Create: `scripts/tunnel.sh`
- Create: `scripts/tunnel.ps1`
- Create: `tests/test_tunnels.py`

**Interfaces:**
- Bash: `tunnel.sh profiles...`
- PowerShell: `tunnel.ps1 profile1 profile2 ...`
- Both build the same ordered `-L local:127.0.0.1:remote` set.

- [ ] **Step 1: Write failing tunnel mapping and parity tests**

```python
# tests/test_tunnels.py
import unittest


EXPECTED = {
    "core": {"5432:127.0.0.1:15432", "6379:127.0.0.1:16379"},
    "vector": {"18000:127.0.0.1:18000"},
    "search": {"9200:127.0.0.1:9200", "5601:127.0.0.1:5601"},
    "observability": {
        "3000:127.0.0.1:3000",
        "9090:127.0.0.1:9090",
        "9091:127.0.0.1:9091",
    },
    "tools": {"5050:127.0.0.1:5050", "5540:127.0.0.1:5540"},
}
```

Invoke both tunnel scripts with fake `ssh`, extract each `-L` value, and assert the exact union for selected profiles. Assert vector never contains local port 8000. Assert duplicate local overrides fail before SSH.

- [ ] **Step 2: Run tests to verify tunnel scripts are absent**

Run: `python -m unittest tests.test_tunnels -v`

Expected: FAIL.

- [ ] **Step 3: Implement Bash tunnel construction and port checks**

Load local port overrides from `remote.env`, validate profiles through `common.sh`, assemble mappings in stable profile order, reject duplicate local ports, test availability using `lsof` on macOS or `ss` on Linux when available, and invoke:

```bash
ssh "${ssh_args[@]}" -NT \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  "${forward_args[@]}" "$target"
```

If neither `lsof` nor `ss` exists, print a warning and rely on `ExitOnForwardFailure`.

- [ ] **Step 4: Implement PowerShell tunnel construction and port checks**

Use the same ordering and mapping table. Check each local port by starting and stopping a `.NET` `TcpListener` on `IPAddress.Loopback`. Invoke `ssh.exe` with an argument array and the same keepalive options.

- [ ] **Step 5: Run tunnel and operator parity tests**

Run: `python -m unittest tests.test_tunnels tests.test_bash_operator tests.test_powershell_operator -v`

Expected: PASS.

- [ ] **Step 6: Commit tunnel support**

```bash
git add scripts/tunnel.sh scripts/tunnel.ps1 tests/test_tunnels.py
git commit -m "feat: add profile-aware ssh tunnels"
```

---

### Task 12: Document, validate, and smoke-test the complete stack

**Files:**
- Create: `README.md`
- Create: `docs/operations.md`
- Create: `tests/test_documentation.py`
- Modify: `scripts/check.sh`
- Modify: `scripts/check.ps1`

**Interfaces:**
- Produces: copy-paste setup paths for Windows PowerShell and macOS/Linux Bash.
- Produces: endpoint and application environment reference.
- Produces: destructive-operation and disposable-data warnings.

- [ ] **Step 1: Write failing documentation contract tests**

Assert README contains all five profile names, both local script forms, Ubuntu 26.04, the future-LTS capability caveat, all ten local endpoints, Chroma port 18000, `LANGFUSE_BASE_URL`, the no-backup warning, and explicit `destroy` data-loss language.

- [ ] **Step 2: Run the documentation test to verify it fails**

Run: `python -m unittest tests.test_documentation -v`

Expected: FAIL because user documentation is absent.

- [ ] **Step 3: Write the user workflow**

Document this exact sequence for both shells:

```text
git clone
init-env
copy/edit remote.env
check selected profiles
bootstrap existing VM
deploy selected profiles
tunnel selected profiles
configure local applications with localhost endpoints
status/logs/stop/down
destroy only when intentional
```

Include application examples:

```dotenv
DATABASE_URL=postgresql://app:<password>@127.0.0.1:5432/app
REDIS_URL=redis://:<password>@127.0.0.1:6379/0
CHROMA_HOST=127.0.0.1
CHROMA_PORT=18000
OPENSEARCH_URL=https://127.0.0.1:9200
LANGFUSE_BASE_URL=http://127.0.0.1:3000
```

Explain that Langfuse API keys are created in the UI after first startup, that OpenSearch uses a development certificate, and that Chroma is protected only by SSH/loopback.

- [ ] **Step 4: Run the complete local verification suite**

Run: `python -m unittest discover -s tests -v`

Run: `bash scripts/check.sh core vector search observability tools`

Run: `powershell -NoProfile -File scripts/check.ps1 core vector search observability tools`

Run: `git diff --check`

Expected: all available-platform tests PASS; platform-specific missing executables are explicit SKIPs, not silent omissions.

- [ ] **Step 5: Smoke-test on the supplied Ubuntu 26.04 minimal GCP VM**

With the user's actual `remote.env` and generated `.env` kept ignored:

```text
bootstrap
deploy core vector
tunnel core vector
psql/Redis ping/Chroma heartbeat from the local machine
deploy search
OpenSearch API and Dashboards through the tunnel
deploy observability tools
Langfuse, MinIO, pgAdmin, and RedisInsight through the tunnel
status
down
up all selected profiles
```

Record command output in the handoff, not in Git. Do not run `destroy` during the smoke test.

- [ ] **Step 6: Commit documentation and final checks**

```bash
git add README.md docs/operations.md scripts/check.sh scripts/check.ps1 tests/test_documentation.py
git commit -m "docs: add remote stack operations guide"
```

- [ ] **Step 7: Verify the final repository state**

Run: `git status --short --branch`

Expected: `## main` with no modified or untracked files.

Run: `git log --oneline --decorate -12`

Expected: the design commit plus one focused commit for each completed implementation task.
