# Data and Inference Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Chroma Admin, DynamoDB Local with DynamoDB Admin, and isolated Gemma 4 and EmbeddingGemma Ollama servers to the profile-based remote development stack.

**Architecture:** Extend the single repository-owned Compose model with `dynamodb` and `inference` profiles and add Chroma Admin to `vector`. Build Chroma Admin natively for `linux/amd64` from a vendored, pinned upstream snapshot; use pinned registry images for DynamoDB and Ollama; retain loopback-only VM ports, profile-scoped SSH tunnels, versioned SSH/SCP releases, disposable named volumes, and symmetric Bash/PowerShell operators.

**Tech Stack:** Docker Engine and Compose v2, Bash, PowerShell, Python `unittest`, SSH/SCP, Node.js 20/Next.js for the vendored Chroma UI, DynamoDB Local 3.3.0, DynamoDB Admin 5.3.4, Ollama 0.32.5.

## Global Constraints

- Target existing Ubuntu 22.04, 24.04, 26.04, and capability-compatible future Ubuntu LTS hosts on `amd64`.
- The immediate acceptance host is the CPU-only GCP `e2-standard-8` VM with 8 vCPUs and 32 GiB RAM.
- No service may publish on a non-loopback VM address; every host mapping starts with `127.0.0.1:`.
- User-facing Chroma endpoints remain API `18000` and Admin UI `18001`; port 8000 is Docker-internal only.
- New endpoints are DynamoDB `18002`, DynamoDB Admin `18003`, Ollama LLM `11440`, and Ollama embeddings `11441`.
- The single `inference` profile always starts two isolated Ollama containers.
- Models are exactly `gemma4:e4b` and `embeddinggemma:300m`.
- First inference deployment blocks until both models have downloaded and are registered.
- Default limits are Chroma Admin `512m`, DynamoDB `1g`, DynamoDB Admin `512m`, Ollama LLM `14g`, Ollama embeddings `2g`, context `8192`, and keep-alive `5m`.
- Ordinary `down`, failed deployment cleanup, and release pruning preserve volumes; only the confirmed `destroy` operation deletes them.
- Real `.env` and `remote.env` remain ignored; never regenerate an existing `.env` to add non-secret keys because doing so rotates persisted credentials.
- No real AWS credentials are used. DynamoDB development values are region `us-east-1`, access key `local`, and secret key `local`.
- Every completed task is reviewed, committed, and pushed directly to remote GitHub `master` before the next task begins.

## File and Interface Map

| Area | Files | Responsibility |
| --- | --- | --- |
| External inputs | `versions.env`, `tests/fixtures/verified-manifests.json` | Exact registry image and model selections |
| Vendored UI | `vendor/chromadb-admin/**`, `vendor/chromadb-admin/UPSTREAM.md` | License-preserving upstream source snapshot |
| Native UI image | `.dockerignore`, `images/chromadb-admin/Dockerfile` | Reproducible non-root AMD64 Chroma Admin build |
| Compose topology | `compose.yaml`, `.env.example`, `remote.env.example` | Profiles, ports, health, limits, volumes |
| Ollama initialization | `config/ollama/bootstrap.sh` | Serve, resumably pull one configured model, and gate readiness |
| Local operators | `scripts/lib/common.sh`, `scripts/lib/Common.psm1`, `scripts/tunnel.*`, `scripts/stack.*` | Profile validation, log targets, tunnels |
| Remote operators | `scripts/remote/compose.sh`, `scripts/remote/preflight.sh`, `scripts/remote/stack.sh`, `scripts/remote/health.sh` | Compose invocation, capacity, lifecycle, functional health |
| Release receiver | `scripts/remote/deploy-release.sh` | Buildable image handling, activation, and rollback cleanup |
| Tests | Existing `tests/test_*.py` plus three focused new modules | TDD contracts and cross-shell parity |
| Documentation | `README.md`, `docs/operations.md`, new GCP verification record | Usage, upgrade, security, and acceptance evidence |

---

### Task 1: Pin External Inputs and Vendor the Chroma Admin Build

**Files:**
- Modify: `versions.env`
- Modify: `tests/fixtures/verified-manifests.json`
- Modify: `tests/test_repository_contract.py`
- Create: `.dockerignore`
- Create: `images/chromadb-admin/Dockerfile`
- Create: `vendor/chromadb-admin/UPSTREAM.md`
- Create: `vendor/chromadb-admin/**` from upstream commit `efe867c86c78683d90b0eb74b88b351fc08f0b5f`

**Interfaces:**
- Produces registry variables `CHROMA_ADMIN_NODE_IMAGE`, `DYNAMODB_LOCAL_IMAGE`, `DYNAMODB_ADMIN_IMAGE`, and `OLLAMA_IMAGE`.
- Produces model variables `OLLAMA_LLM_MODEL` and `OLLAMA_EMBEDDING_MODEL`.
- Produces local image tag `remote-infra-stack/chromadb-admin:efe867c86c78` from the repository build context.

- [ ] **Step 1: Write failing repository input tests**

Change the image inventory test to filter only `*_IMAGE` variables, require 17 verified registry inputs, and test models separately:

```python
versions = read_env(repo_path("versions.env"))
images = {key: value for key, value in versions.items() if key.endswith("_IMAGE")}
self.assertEqual(17, len(images))
self.assertEqual("gemma4:e4b", versions["OLLAMA_LLM_MODEL"])
self.assertEqual("embeddinggemma:300m", versions["OLLAMA_EMBEDDING_MODEL"])
self.assertEqual(
    {name: record["reference"] for name, record in verified_images.items()},
    images,
)
```

Add a vendor contract asserting the exact revision, v2 client dependency, lockfile, license, port, and safe Dockerfile:

```python
def test_chroma_admin_is_vendored_and_built_from_pinned_inputs(self):
    upstream = repo_path("vendor/chromadb-admin/UPSTREAM.md").read_text(encoding="utf-8")
    package = json.loads(repo_path("vendor/chromadb-admin/package.json").read_text())
    dockerfile = repo_path("images/chromadb-admin/Dockerfile").read_text()
    self.assertIn("efe867c86c78683d90b0eb74b88b351fc08f0b5f", upstream)
    self.assertEqual("^2.0.1", package["dependencies"]["chromadb"])
    self.assertTrue(repo_path("vendor/chromadb-admin/package-lock.json").is_file())
    self.assertTrue(repo_path("vendor/chromadb-admin/LICENSE.txt").is_file())
    self.assertIn("npm ci", dockerfile)
    self.assertIn("USER node", dockerfile)
    self.assertIn("EXPOSE 3001", dockerfile)
    self.assertNotRegex(dockerfile, r"(?m)^FROM\s+node:")
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python -m unittest tests.test_repository_contract -v
```

Expected: failures for the six missing version variables, missing manifest records, and missing vendored/build files.

- [ ] **Step 3: Add the exact version catalog entries**

Append these committed values to `versions.env`:

```dotenv
CHROMA_ADMIN_NODE_IMAGE=docker.io/library/node:20.19.2-bookworm-slim@sha256:7cd3fbc830c75c92256fe1122002add9a1c025831af8770cd0bf8e45688ef661
DYNAMODB_LOCAL_IMAGE=docker.io/amazon/dynamodb-local:3.3.0@sha256:d89f8fcc6b1a39cb35976c248ed42a28c66ae00dc043099210f5571e42648ab4
DYNAMODB_ADMIN_IMAGE=docker.io/aaronshaf/dynamodb-admin:5.3.4@sha256:ac41724cd99706256d405a14a5fb96f51f18c41a630c84fa3357f900cbd16d2e
OLLAMA_IMAGE=docker.io/ollama/ollama:0.32.5@sha256:4dea9fb511947e24a84237bb636b0203abcb2ff0d3fbc7b4ff865deb91362131
OLLAMA_LLM_MODEL=gemma4:e4b
OLLAMA_EMBEDDING_MODEL=embeddinggemma:300m
```

Add matching manifest-list records to `verified-manifests.json`, each with `verified_platforms: ["linux/amd64"]`. The top-level digests above are the registry index digests, not child manifests.

- [ ] **Step 4: Vendor the exact upstream source snapshot**

Use a temporary clone and archive so `.git` never enters the repository:

```powershell
git clone --filter=blob:none https://github.com/flanker/chromadb-admin.git $env:TEMP\remote-infra-chromadb-admin
git -C $env:TEMP\remote-infra-chromadb-admin checkout efe867c86c78683d90b0eb74b88b351fc08f0b5f
git -C $env:TEMP\remote-infra-chromadb-admin archive --format=tar --output=$env:TEMP\chromadb-admin.tar HEAD
New-Item -ItemType Directory -Force vendor\chromadb-admin
tar -xf $env:TEMP\chromadb-admin.tar -C vendor\chromadb-admin
```

Create `UPSTREAM.md` with the project URL, exact commit, import date, MIT license, and the note that the published `0.0.2` image is ARM64-only. Do not modify vendored application source.

- [ ] **Step 5: Add the repository-owned native Docker build**

Create the root `.dockerignore`:

```dockerignore
**
!images/
!images/chromadb-admin/
!images/chromadb-admin/Dockerfile
!vendor/
!vendor/chromadb-admin/
!vendor/chromadb-admin/**
vendor/chromadb-admin/.git
vendor/chromadb-admin/node_modules
vendor/chromadb-admin/.next
```

Create `images/chromadb-admin/Dockerfile`:

```dockerfile
ARG NODE_IMAGE
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY vendor/chromadb-admin/package.json vendor/chromadb-admin/package-lock.json ./
RUN npm ci
COPY vendor/chromadb-admin/ ./
RUN npm run build && npm prune --omit=dev

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY --from=build --chown=node:node /app ./
USER node
EXPOSE 3001
CMD ["npm", "start"]
```

- [ ] **Step 6: Verify GREEN and registry provenance**

Run:

```bash
python -m unittest tests.test_repository_contract -v
git diff --check
```

On the configured GCP VM, rerun `docker buildx imagetools inspect` for all four new registry references and require a `linux/amd64` child.

- [ ] **Step 7: Commit and push Task 1**

```bash
git add versions.env tests/fixtures/verified-manifests.json tests/test_repository_contract.py .dockerignore images/chromadb-admin vendor/chromadb-admin
git commit -m "build: pin data and inference inputs"
git push origin HEAD:master
```

---

### Task 2: Extend Configuration, Profiles, and SSH Tunnels on Both Shells

**Files:**
- Modify: `.env.example`
- Modify: `remote.env.example`
- Modify: `tests/fixtures/stack.env`
- Modify: `tests/fixtures/remote.env`
- Modify: `scripts/lib/common.sh`
- Modify: `scripts/lib/Common.psm1`
- Modify: `scripts/tunnel.sh`
- Modify: `scripts/tunnel.ps1`
- Modify: `scripts/stack.sh`
- Modify: `scripts/stack.ps1`
- Modify: `tests/test_env_generation.py`
- Modify: `tests/test_tunnels.py`
- Modify: `tests/test_bash_operator.py`
- Modify: `tests/test_powershell_operator.py`

**Interfaces:**
- Produces canonical profile set `core vector search observability tools dynamodb inference`.
- Produces five new required `LOCAL_*_PORT` keys and exact tunnel mappings.
- Does not mutate ignored operator files; Task 10 merges example additions into them manually.

- [ ] **Step 1: Write failing environment and parity tests**

Assert these non-secret `.env` defaults:

```python
expected = read_env(repo_path(".env.example"))
self.assertEqual("512m", expected["CHROMA_ADMIN_MEMORY"])
self.assertEqual("1g", expected["DYNAMODB_MEMORY"])
self.assertEqual("512m", expected["DYNAMODB_ADMIN_MEMORY"])
self.assertEqual("14g", expected["OLLAMA_LLM_MEMORY"])
self.assertEqual("2g", expected["OLLAMA_EMBEDDING_MEMORY"])
self.assertEqual("8192", expected["OLLAMA_CONTEXT_LENGTH"])
self.assertEqual("5m", expected["OLLAMA_KEEP_ALIVE"])
```

Extend `PROFILE_FORWARDS` in `tests/test_tunnels.py`:

```python
"vector": (("LOCAL_CHROMA_PORT", 18000), ("LOCAL_CHROMA_ADMIN_PORT", 18001)),
"dynamodb": (("LOCAL_DYNAMODB_PORT", 18002), ("LOCAL_DYNAMODB_ADMIN_PORT", 18003)),
"inference": (("LOCAL_OLLAMA_LLM_PORT", 11440), ("LOCAL_OLLAMA_EMBEDDING_PORT", 11441)),
```

Add Bash/PowerShell cases proving new profiles and log targets are accepted, duplicates and case mutations are rejected, and both clients emit identical ordered forwarding arguments.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python -m unittest tests.test_env_generation tests.test_tunnels tests.test_bash_operator tests.test_powershell_operator -v
```

Expected: failures naming missing keys and unknown `dynamodb`/`inference` profiles.

- [ ] **Step 3: Add example and fixture values**

Append the seven resource settings from Global Constraints to `.env.example` and `tests/fixtures/stack.env`. Append:

```dotenv
LOCAL_CHROMA_ADMIN_PORT=18001
LOCAL_DYNAMODB_PORT=18002
LOCAL_DYNAMODB_ADMIN_PORT=18003
LOCAL_OLLAMA_LLM_PORT=11440
LOCAL_OLLAMA_EMBEDDING_PORT=11441
```

to `remote.env.example` and `tests/fixtures/remote.env`, preserving identical key order between examples and fixtures.

- [ ] **Step 4: Extend both strict profile/configuration libraries**

Add `dynamodb|inference` to Bash `validate_profiles` and `@('core', 'vector', 'search', 'observability', 'tools', 'dynamodb', 'inference')` to PowerShell `Assert-Profiles`. Add all five local port names to both strict allowed/required environment-key lists. Keep the only cross-profile rule as `tools requires core`.

- [ ] **Step 5: Add deterministic tunnel mappings**

In both tunnel scripts, add mappings in this order after Chroma and after existing profiles respectively:

```text
vector     LOCAL_CHROMA_ADMIN_PORT     -> 127.0.0.1:18001
dynamodb   LOCAL_DYNAMODB_PORT         -> 127.0.0.1:18002
dynamodb   LOCAL_DYNAMODB_ADMIN_PORT   -> 127.0.0.1:18003
inference  LOCAL_OLLAMA_LLM_PORT       -> 127.0.0.1:11440
inference  LOCAL_OLLAMA_EMBEDDING_PORT -> 127.0.0.1:11441
```

All keys are normalized even when their profile is not selected so malformed configuration always fails consistently.

- [ ] **Step 6: Extend local log-target validation**

Allow profiles `dynamodb`, `inference` and services `chroma-admin`, `dynamodb-local`, `dynamodb-admin`, `ollama-llm`, `ollama-embedding` in `stack.sh` and `stack.ps1`. Forward exact arguments unchanged over SSH.

- [ ] **Step 7: Verify GREEN and shell parity**

```bash
python -m unittest tests.test_env_generation tests.test_tunnels tests.test_bash_operator tests.test_powershell_operator -v
```

Expected: PASS on every locally available Bash and PowerShell implementation; explicit skips only for unavailable shells.

- [ ] **Step 8: Commit and push Task 2**

```bash
git add .env.example remote.env.example tests/fixtures scripts/lib scripts/tunnel.sh scripts/tunnel.ps1 scripts/stack.sh scripts/stack.ps1 tests/test_env_generation.py tests/test_tunnels.py tests/test_bash_operator.py tests/test_powershell_operator.py
git commit -m "feat: add data and inference operator contracts"
git push origin HEAD:master
```

---

### Task 3: Add Chroma Admin to the Vector Profile

**Files:**
- Modify: `compose.yaml`
- Modify: `tests/test_compose_core_vector.py`
- Modify: `tests/test_compose_invariants.py`

**Interfaces:**
- Consumes Task 1 native build and `CHROMA_ADMIN_NODE_IMAGE`.
- Produces stateless service `chroma-admin` on VM loopback `18001`, depending on healthy `chroma`.

- [ ] **Step 1: Write failing vector topology tests**

Require exactly two vector services and the build contract:

```python
model = render_compose("vector")
self.assertEqual({"chroma", "chroma-admin"}, set(model["services"]))
admin = model["services"]["chroma-admin"]
self.assertEqual("remote-infra-stack/chromadb-admin:efe867c86c78", admin["image"])
self.assertEqual(repo_path(".").resolve(), Path(admin["build"]["context"]).resolve())
self.assertTrue(admin["build"]["dockerfile"].replace("\\", "/").endswith("/images/chromadb-admin/Dockerfile"))
self.assertEqual("service_healthy", admin["depends_on"]["chroma"]["condition"])
self.assertEqual("127.0.0.1", admin["ports"][0]["host_ip"])
self.assertEqual(18001, int(admin["ports"][0]["published"]))
self.assertEqual(3001, int(admin["ports"][0]["target"]))
self.assertNotIn("volumes", admin)
```

Update invariants incrementally to 14 services while retaining the existing five-profile render set, add the local Chroma Admin image literal to the image contract, and allow same-profile dependency `chroma-admin -> chroma`. Import `Path` and `repo_path` for the normalized build-path assertions. Tasks 4 and 6 extend the same invariant tables to their final 18-service, seven-profile form.

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m unittest tests.test_compose_core_vector tests.test_compose_invariants -v
```

- [ ] **Step 3: Add the Chroma Admin service**

Add to `compose.yaml`:

```yaml
  chroma-admin:
    profiles: [vector]
    image: remote-infra-stack/chromadb-admin:efe867c86c78
    build:
      context: .
      dockerfile: images/chromadb-admin/Dockerfile
      args:
        NODE_IMAGE: ${CHROMA_ADMIN_NODE_IMAGE:?set in versions.env}
    restart: unless-stopped
    ports:
      - "127.0.0.1:18001:3001"
    depends_on:
      chroma:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://127.0.0.1:3001').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))"]
      interval: 10s
      timeout: 5s
      retries: 18
      start_period: 30s
    mem_limit: ${CHROMA_ADMIN_MEMORY:-512m}
    networks: [infra]
```

- [ ] **Step 4: Render and verify GREEN**

```bash
docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile vector config --quiet
python -m unittest tests.test_compose_core_vector tests.test_compose_invariants -v
```

- [ ] **Step 5: Commit and push Task 3**

```bash
git add compose.yaml tests/test_compose_core_vector.py tests/test_compose_invariants.py
git commit -m "feat: add Chroma administration UI"
git push origin HEAD:master
```

---

### Task 4: Add the DynamoDB Profile

**Files:**
- Modify: `compose.yaml`
- Create: `tests/test_compose_dynamodb.py`
- Modify: `tests/test_compose_invariants.py`

**Interfaces:**
- Produces `dynamodb-local` and `dynamodb-admin` in profile `dynamodb`.
- Produces named volume `dynamodb_data` and internal endpoint `http://dynamodb-local:8000`.

- [ ] **Step 1: Write the failing DynamoDB Compose tests**

Create tests asserting exact profile ownership, pinned images, commands, dummy credentials, health dependency, loopback ports, memory, and volume:

```python
model = render_compose("dynamodb")
self.assertEqual({"dynamodb-local", "dynamodb-admin"}, set(model["services"]))
database = model["services"]["dynamodb-local"]
admin = model["services"]["dynamodb-admin"]
self.assertEqual(["-jar", "DynamoDBLocal.jar", "-sharedDb", "-dbPath", "./data"], database["command"])
self.assertEqual("dynamodb_data", database["volumes"][0]["source"])
self.assertEqual("/home/dynamodblocal/data", database["volumes"][0]["target"])
self.assertEqual(18002, int(database["ports"][0]["published"]))
self.assertEqual(18003, int(admin["ports"][0]["published"]))
self.assertEqual("http://dynamodb-local:8000", admin["environment"]["DYNAMO_ENDPOINT"])
self.assertEqual("0.0.0.0", admin["environment"]["HOST"])
self.assertEqual("service_healthy", admin["depends_on"]["dynamodb-local"]["condition"])
self.assertEqual("remote-infra-stack-dynamodb-data", model["volumes"]["dynamodb_data"]["name"])
```

Extend `ComposeInvariantTests.profiles` with `dynamodb`, map both service images to `DYNAMODB_LOCAL_IMAGE` and `DYNAMODB_ADMIN_IMAGE`, add both expected profile entries, and add `dynamodb-local` to the stateful named-volume set. The expected service count becomes 16 at this task.

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m unittest tests.test_compose_dynamodb tests.test_compose_invariants -v
```

- [ ] **Step 3: Implement both services and the named volume**

Use this Compose contract:

```yaml
  dynamodb-local:
    profiles: [dynamodb]
    image: ${DYNAMODB_LOCAL_IMAGE:?set in versions.env}
    restart: unless-stopped
    working_dir: /home/dynamodblocal
    command: ["-jar", "DynamoDBLocal.jar", "-sharedDb", "-dbPath", "./data"]
    ports:
      - "127.0.0.1:18002:8000"
    volumes:
      - dynamodb_data:/home/dynamodblocal/data
    healthcheck:
      test: ["CMD-SHELL", "exec bash -ec 'exec 3<>/dev/tcp/127.0.0.1/8000'"]
      interval: 5s
      timeout: 5s
      retries: 24
    mem_limit: ${DYNAMODB_MEMORY:-1g}
    networks: [infra]

  dynamodb-admin:
    profiles: [dynamodb]
    image: ${DYNAMODB_ADMIN_IMAGE:?set in versions.env}
    restart: unless-stopped
    environment:
      HOST: 0.0.0.0
      PORT: "8001"
      DYNAMO_ENDPOINT: http://dynamodb-local:8000
      AWS_REGION: us-east-1
      AWS_ACCESS_KEY_ID: local
      AWS_SECRET_ACCESS_KEY: local
    ports:
      - "127.0.0.1:18003:8001"
    depends_on:
      dynamodb-local:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://127.0.0.1:8001').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))"]
      interval: 10s
      timeout: 5s
      retries: 18
      start_period: 20s
    mem_limit: ${DYNAMODB_ADMIN_MEMORY:-512m}
    networks: [infra]
```

Add explicit volume name `remote-infra-stack-dynamodb-data`.

- [ ] **Step 4: Verify GREEN**

```bash
docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile dynamodb config --quiet
python -m unittest tests.test_compose_dynamodb tests.test_compose_invariants -v
```

- [ ] **Step 5: Commit and push Task 4**

```bash
git add compose.yaml tests/test_compose_dynamodb.py tests/test_compose_invariants.py
git commit -m "feat: add DynamoDB development profile"
git push origin HEAD:master
```

---

### Task 5: Implement and Unit-Test the Ollama Bootstrap State Machine

**Files:**
- Create: `config/ollama/bootstrap.sh`
- Create: `tests/test_ollama_bootstrap.py`
- Modify: `.gitattributes` only if the existing `*.sh text eol=lf` rule does not already cover the file

**Interfaces:**
- Consumes `OLLAMA_MODEL` and optional test controls `OLLAMA_BIN`, `OLLAMA_READY_FILE`, `OLLAMA_STARTUP_ATTEMPTS`, `OLLAMA_PULL_ATTEMPTS`, `OLLAMA_RETRY_SECONDS`, and `SLEEP_BIN`.
- Produces readiness file `/tmp/remote-infra-model-ready` only after exact-model verification.
- Starts one `ollama serve`, forwards TERM/INT/HUP, and preserves Ollama's named-volume download state.

- [ ] **Step 1: Write fake-driven failing tests**

The test fake records `serve`, `list`, `show`, and `pull` calls and simulates server readiness. Implement a `run_bootstrap` helper returning `(CompletedProcess, calls: list[str], ready: bool)`, then cover these exact cases:

```python
def test_first_start_waits_pulls_verifies_and_marks_ready(self):
    result, calls, ready = self.run_bootstrap(
        list_failures=2, show_results=(1, 0), pull_results=(0,)
    )
    self.assertEqual(0, result.returncode, result.stderr)
    self.assertEqual(1, calls.count("pull gemma4:e4b"))
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
    self.assertTrue(ready)

def test_exhausted_pull_failures_never_mark_ready(self):
    result, calls, ready = self.run_bootstrap(show_results=(1,), pull_results=(1, 1, 1))
    self.assertNotEqual(0, result.returncode)
    self.assertEqual(3, calls.count("pull gemma4:e4b"))
    self.assertFalse(ready)
```

Add equally explicit final-verification-failure and TERM cases: final `show` nonzero must leave `ready` false, while TERM to the bootstrap PID must appear as TERM in the fake server's signal log and exit nonzero. Assert call order `serve -> list readiness -> initial show -> pull -> final show -> ready`, exact model argument preservation, maximum three pull attempts, and nonzero failure.

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m unittest tests.test_ollama_bootstrap -v
```

- [ ] **Step 3: Implement the portable bootstrap script**

Use POSIX shell and this state machine:

```sh
#!/bin/sh
set -eu

: "${OLLAMA_MODEL:?OLLAMA_MODEL is required}"
ollama_bin=${OLLAMA_BIN:-/bin/ollama}
ready_file=${OLLAMA_READY_FILE:-/tmp/remote-infra-model-ready}
startup_attempts=${OLLAMA_STARTUP_ATTEMPTS:-300}
pull_attempts=${OLLAMA_PULL_ATTEMPTS:-3}
retry_seconds=${OLLAMA_RETRY_SECONDS:-2}
sleep_bin=${SLEEP_BIN:-sleep}
server_pid=

cleanup() {
  signal=${1:-TERM}
  if [ -n "$server_pid" ] && kill -0 "$server_pid" 2>/dev/null; then
    kill -"$signal" "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
}
on_exit() {
  status=$?
  trap - EXIT
  cleanup TERM
  exit "$status"
}
trap on_exit EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP
rm -f -- "$ready_file"
OLLAMA_HOST=0.0.0.0:11434 "$ollama_bin" serve &
server_pid=$!

attempt=1
while ! OLLAMA_HOST=127.0.0.1:11434 "$ollama_bin" list >/dev/null 2>&1; do
  kill -0 "$server_pid" 2>/dev/null || exit 1
  [ "$attempt" -lt "$startup_attempts" ] || exit 1
  attempt=$((attempt + 1))
  "$sleep_bin" "$retry_seconds"
done

if ! OLLAMA_HOST=127.0.0.1:11434 "$ollama_bin" show "$OLLAMA_MODEL" >/dev/null 2>&1; then
  attempt=1
  until OLLAMA_HOST=127.0.0.1:11434 "$ollama_bin" pull "$OLLAMA_MODEL"; do
    [ "$attempt" -lt "$pull_attempts" ] || exit 1
    attempt=$((attempt + 1))
    "$sleep_bin" "$retry_seconds"
  done
fi
OLLAMA_HOST=127.0.0.1:11434 "$ollama_bin" show "$OLLAMA_MODEL" >/dev/null
: >"$ready_file"
wait "$server_pid"
```

Adjust only what the tests prove necessary; do not parse human-formatted `ollama list` output.

- [ ] **Step 4: Verify GREEN and shell syntax**

```bash
bash -n config/ollama/bootstrap.sh
python -m unittest tests.test_ollama_bootstrap -v
```

- [ ] **Step 5: Commit and push Task 5**

```bash
git add config/ollama/bootstrap.sh tests/test_ollama_bootstrap.py .gitattributes
git commit -m "feat: add resumable Ollama model bootstrap"
git push origin HEAD:master
```

---

### Task 6: Add the Two-Container Inference Profile

**Files:**
- Modify: `compose.yaml`
- Create: `tests/test_compose_inference.py`
- Modify: `tests/test_compose_invariants.py`

**Interfaces:**
- Consumes Task 5 bootstrap script and committed model/image variables.
- Produces `ollama-llm` at VM `11440` and `ollama-embedding` at VM `11441`.
- Produces independent volumes `ollama_llm_data` and `ollama_embedding_data`.

- [ ] **Step 1: Write failing inference topology tests**

Require two services with the same pinned image but distinct model, port, volume, and limit:

```python
model = render_compose("inference")
self.assertEqual({"ollama-llm", "ollama-embedding"}, set(model["services"]))
llm = model["services"]["ollama-llm"]
embedding = model["services"]["ollama-embedding"]
self.assertEqual(llm["image"], embedding["image"])
self.assertEqual("gemma4:e4b", llm["environment"]["OLLAMA_MODEL"])
self.assertEqual("embeddinggemma:300m", embedding["environment"]["OLLAMA_MODEL"])
self.assertEqual(11440, int(llm["ports"][0]["published"]))
self.assertEqual(11441, int(embedding["ports"][0]["published"]))
self.assertEqual("ollama_llm_data", llm["volumes"][0]["source"])
self.assertEqual("ollama_embedding_data", embedding["volumes"][0]["source"])
self.assertEqual("1", llm["environment"]["OLLAMA_NUM_PARALLEL"])
self.assertEqual("1", embedding["environment"]["OLLAMA_MAX_LOADED_MODELS"])
self.assertEqual("1h30m0s", llm["healthcheck"]["start_period"])
```

Extend `ComposeInvariantTests.profiles` with `inference`, map both services to `OLLAMA_IMAGE`, add both expected profile entries, and add both Ollama services to the stateful named-volume set. The expected service count becomes the final 18 across seven profiles. Keep the YAML duration at 90 minutes; Compose renders it as `1h30m0s`.

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m unittest tests.test_compose_inference tests.test_compose_invariants -v
```

- [ ] **Step 3: Add both inference services**

Use this shared contract for each service, substituting model, published port, volume, and memory:

```yaml
    profiles: [inference]
    image: ${OLLAMA_IMAGE:?set in versions.env}
    restart: unless-stopped
    entrypoint: ["/bin/sh", "/opt/remote-infra/bootstrap.sh"]
    environment:
      OLLAMA_MODEL: ${OLLAMA_LLM_MODEL:?set in versions.env}
      OLLAMA_CONTEXT_LENGTH: ${OLLAMA_CONTEXT_LENGTH:-8192}
      OLLAMA_KEEP_ALIVE: ${OLLAMA_KEEP_ALIVE:-5m}
      OLLAMA_MAX_LOADED_MODELS: "1"
      OLLAMA_NUM_PARALLEL: "1"
    ports:
      - "127.0.0.1:11440:11434"
    volumes:
      - ollama_llm_data:/root/.ollama
      - ./config/ollama/bootstrap.sh:/opt/remote-infra/bootstrap.sh:ro
    healthcheck:
      test: ["CMD-SHELL", "test -f /tmp/remote-infra-model-ready && OLLAMA_HOST=127.0.0.1:11434 /bin/ollama show \"$$OLLAMA_MODEL\" >/dev/null"]
      interval: 10s
      timeout: 10s
      retries: 12
      start_period: 90m
    mem_limit: ${OLLAMA_LLM_MEMORY:-14g}
    networks: [infra]
```

Use `OLLAMA_EMBEDDING_MODEL`, `11441`, `ollama_embedding_data`, and `OLLAMA_EMBEDDING_MEMORY` for the second container. Add explicit stable volume names.

- [ ] **Step 4: Verify GREEN**

```bash
docker compose --env-file versions.env --env-file tests/fixtures/stack.env --profile inference config --quiet
python -m unittest tests.test_compose_inference tests.test_compose_invariants -v
```

- [ ] **Step 5: Commit and push Task 6**

```bash
git add compose.yaml tests/test_compose_inference.py tests/test_compose_invariants.py
git commit -m "feat: add isolated Ollama inference profile"
git push origin HEAD:master
```

---

### Task 7: Extend Remote Profile Lifecycle, Capacity Preflight, and Health

**Files:**
- Create: `scripts/remote/preflight.sh`
- Modify: `scripts/remote/compose.sh`
- Modify: `scripts/remote/stack.sh`
- Modify: `scripts/remote/health.sh`
- Modify: `tests/fakes/docker`
- Modify: `tests/test_remote_runtime.py`

**Interfaces:**
- `preflight.sh` accepts one or more profile arguments and exits nonzero below 10 GiB release storage or below 20 GiB Docker storage when inference is selected; memory overcommit remains a warning.
- `health.sh` accepts one or more profile arguments and verifies new UIs/APIs and exact Ollama model registration without generating text or embeddings.
- Profile mappings become vector=`chroma chroma-admin`, dynamodb=`dynamodb-local dynamodb-admin`, inference=`ollama-llm ollama-embedding`.

- [ ] **Step 1: Write failing remote runtime tests**

Add named tests with exact assertions: `test_new_profiles_expand_to_exact_services_for_stop_and_logs` compares the Docker argument log to the five new service names; `test_preflight_checks_docker_root_for_inference` asserts `docker info --format {{.DockerRootDir}}` and `df` receive the fake Docker root; `test_preflight_fails_below_twenty_gib_of_docker_storage` requires nonzero status and the 20-GiB message; `test_preflight_warns_but_does_not_fail_memory_overcommit` requires status zero and the host-memory warning; `test_health_checks_new_uis_dynamodb_and_exact_ollama_models` compares the curl and Compose-exec log to the endpoint list below; and `test_health_never_runs_generate_or_embed` rejects `/api/generate`, `/api/chat`, and `/api/embed` in every captured argument.

Fakes must make Docker Root Dir, `df`, Compose JSON, curl responses, and Ollama `show` calls observable without a daemon.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python -m unittest tests.test_remote_runtime -v
```

- [ ] **Step 3: Extract shared preflight**

Move the existing disk/memory logic from `stack.sh` into `preflight.sh`. Preserve 10-GiB hard and 20-GiB warning checks for `STACK_ROOT`. When `inference` is selected:

```sh
docker_root=$(${DOCKER_BIN:-docker} info --format '{{.DockerRootDir}}')
docker_free=$(${DF_BIN:-df} --output=avail -B1 "$docker_root" | awk 'NR > 1 { value=$1 } END { print value }')
((docker_free >= 20 * 1024 * 1024 * 1024)) ||
  die "less than 20 GiB is available on the Docker storage filesystem for inference"
```

Continue totaling selected `mem_limit` values plus 2 GiB host overhead and warn, not fail, when they exceed `MemTotal`.

- [ ] **Step 4: Extend remote profile/service mappings**

Add the two new profiles to `compose.sh`, `stack.sh`, and `health.sh`, expand `vector`, and add all five services to allowed log targets. Make `stack.sh up` invoke `preflight.sh` before `docker compose up -d --wait --build`.

- [ ] **Step 5: Add functional health checks**

Implement:

```text
vector:     GET http://127.0.0.1:18000/api/v2/heartbeat and GET http://127.0.0.1:18001
dynamodb:   AWS SDK v3 ListTables through dynamodb-admin against http://dynamodb-local:8000 and GET http://127.0.0.1:18003
inference:  GET /api/version on 11440 and 11441, then compose exec ollama show "$OLLAMA_MODEL" in each container
```

Run DynamoDB's protocol check with `compose exec -T dynamodb-admin node -e` and the container's installed `@aws-sdk/client-dynamodb`: construct `DynamoDBClient` from `DYNAMO_ENDPOINT`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY`, send `new ListTablesCommand({Limit: 1})`, and exit nonzero on rejection. Test the exact Compose-exec arguments against the fake before validating them on GCP.

- [ ] **Step 6: Verify GREEN and shell syntax**

```bash
bash -n scripts/remote/preflight.sh scripts/remote/compose.sh scripts/remote/stack.sh scripts/remote/health.sh
python -m unittest tests.test_remote_runtime -v
```

- [ ] **Step 7: Commit and push Task 7**

```bash
git add scripts/remote tests/fakes/docker tests/test_remote_runtime.py
git commit -m "feat: verify data and inference profiles remotely"
git push origin HEAD:master
```

---

### Task 8: Make Release Deployment Build-Aware and Failure-Safe

**Files:**
- Modify: `scripts/remote/deploy-release.sh`
- Modify: `tests/test_release_lifecycle.py`

**Interfaces:**
- Consumes `scripts/remote/preflight.sh`, native Chroma build context, and Ollama bootstrap.
- Pulls with `--ignore-buildable`, builds `chroma-admin` only when `vector` is selected, and activates only after health.
- On failure, restores the prior runtime environment and prior selected services, removes failed-release containers, and preserves named volumes.

- [ ] **Step 1: Write failing archive/build/rollback tests**

Add named tests with exact assertions. `test_release_requires_preflight_ollama_bootstrap_and_chroma_build_inputs` deletes each required archive member in a subtest and requires failure before Docker. `test_deploy_runs_preflight_pull_ignore_buildable_build_up_health_in_order` compares the operation log to `config`, `preflight`, `pull --ignore-buildable`, `build --pull chroma-admin`, `up -d --wait`, and `health`. `test_non_vector_deploy_does_not_build_chroma_admin` rejects every `build` record. `test_failed_first_inference_deploy_removes_containers_but_preserves_volumes` requires `rm -sf ollama-llm ollama-embedding` and rejects `-v`. `test_failed_upgrade_restores_previous_runtime_env_and_selected_services` compares the restored file bytes and previous Compose `up` call. `test_failed_deploy_never_writes_success_or_changes_current_or_prunes` compares the original symlink and release directory inventory byte-for-byte.

The fake Docker log must distinguish `rm -sf` from `down -v`; assert cleanup never includes `-v`.

- [ ] **Step 2: Run tests and verify RED**

```bash
python -m unittest tests.test_release_lifecycle -v
```

- [ ] **Step 3: Require and validate new release inputs**

Require regular, non-symlink files for:

```text
scripts/remote/preflight.sh
config/ollama/bootstrap.sh
images/chromadb-admin/Dockerfile
.dockerignore
vendor/chromadb-admin/package.json
vendor/chromadb-admin/package-lock.json
vendor/chromadb-admin/LICENSE.txt
vendor/chromadb-admin/UPSTREAM.md
```

Open security-critical scripts as verified leaves and recheck release-directory identity around preflight, build, start, and health.

- [ ] **Step 4: Add preflight, pull, and selected build order**

After Compose render:

```sh
bash "$preflight_script" "${profiles[@]}"
bash "$compose_script" "${profiles[@]}" -- pull --ignore-buildable
if [[ -n "${selected[vector]+x}" ]]; then
  bash "$compose_script" "${profiles[@]}" -- build --pull chroma-admin
fi
bash "$compose_script" "${profiles[@]}" -- up -d --wait
bash "$health_script" "${profiles[@]}"
```

- [ ] **Step 5: Add scoped rollback cleanup**

Before replacing `runtime/.env`, preserve its bytes and whether it existed. Record the prior `current` target. Install an `ERR` trap after verified release placement that:

1. Disables itself to prevent recursion.
2. Expands only services in the attempted profiles.
3. Runs the new Compose model with `rm -sf` on those services, never `down -v`.
4. Restores the previous runtime environment atomically, or removes the new file if none existed.
5. Starts the same attempted profiles supported by the prior release; ignore new profiles that the prior release cannot parse.
6. Leaves partial named-volume data and the `current` symlink unchanged.
7. Returns the original failure status.

The success path clears the trap immediately before writing `.successful`.

- [ ] **Step 6: Verify GREEN and complete release regression**

```bash
bash -n scripts/remote/deploy-release.sh
python -m unittest tests.test_release_lifecycle -v
```

- [ ] **Step 7: Commit and push Task 8**

```bash
git add scripts/remote/deploy-release.sh tests/test_release_lifecycle.py
git commit -m "fix: make profile deployment rollback failure-safe"
git push origin HEAD:master
```

---

### Task 9: Document the Expanded Stack and Run the Local Regression Suite

**Files:**
- Modify: `README.md`
- Modify: `docs/operations.md`
- Modify: `tests/test_documentation.py`
- Modify any existing test whose exact service/profile count legitimately changed

**Interfaces:**
- Documents seven profiles, fifteen user-facing endpoints, first-pull behavior, manual ignored-env upgrade, dummy DynamoDB credentials, and CPU resource expectations.

- [ ] **Step 1: Write failing documentation assertions**

Require both documents to contain:

```text
`dynamodb`, `inference`, `chroma-admin`, `dynamodb-admin`
http://127.0.0.1:18001
http://127.0.0.1:18002
http://127.0.0.1:18003
http://127.0.0.1:11440
http://127.0.0.1:11441
gemma4:e4b
embeddinggemma:300m
AWS_ACCESS_KEY_ID=local
http://chroma:8000
```

Also assert explicit warnings against `0.0.0.0`, against `init-env --force` for upgrades, and against expecting every profile to fit at peak on 32 GiB.

- [ ] **Step 2: Run documentation tests and verify RED**

```bash
python -m unittest tests.test_documentation -v
```

- [ ] **Step 3: Update README quick start and client examples**

Document Bash and PowerShell commands for `core vector dynamodb inference`, all new URLs, Chroma Admin's one-time internal connection URL, a Python/boto3 DynamoDB endpoint example with dummy credentials, Ollama `/api/chat`, and Ollama `/api/embed` examples.

- [ ] **Step 4: Update operations and upgrade guidance**

Document manual addition of seven `.env` and five `remote.env` keys without regenerating secrets, first model download/resume behavior, named-volume deletion semantics, CPU latency, resizing memory limits, Docker disk requirements, and loopback-only security.

- [ ] **Step 5: Run the entire local suite**

```bash
python -m unittest discover -s tests -v
git diff --check
```

Expected: all applicable tests pass; skips must be only the repository's explicit unavailable-platform/tool skips.

- [ ] **Step 6: Commit and push Task 9**

```bash
git add README.md docs/operations.md tests
git commit -m "docs: add data and inference operations"
git push origin HEAD:master
```

---

### Task 10: Validate on the Actual GCP VM and Record Evidence

**Files:**
- Modify locally but do not commit: `.env`, `remote.env`
- Create: `docs/verification/data-and-inference-gcp-smoke.md`
- Modify implementation/tests only when systematic debugging proves a defect

**Interfaces:**
- Uses configured project `remote-infra-stack`, VM `remote-infra-stack`, zone `asia-south1-c`.
- Produces sanitized evidence for image manifests, native build, APIs, UIs, inference, tunnels, restart cache, memory, and GitHub `master` identity.

- [ ] **Step 1: Merge non-secret ignored configuration safely**

Append only missing Task 2 keys to the existing `.env` and `remote.env`. Preserve every existing secret byte-for-byte, enforce user-only permissions, and run both `check` surfaces against `core vector dynamodb inference`.

- [ ] **Step 2: Reverify all four new registry manifests remotely**

Run `docker buildx imagetools inspect` for the exact digest references and record index digest plus `linux/amd64` child. Confirm the vendored Chroma source revision and build the local image without using the ARM64 upstream image.

- [ ] **Step 3: Stage VM resources and deploy**

Inspect `free -h`, `df -h`, and `docker system df`. Stop only resource-heavy profiles if actual free memory requires it; do not delete volumes. Deploy:

```powershell
.\scripts\deploy.ps1 core vector dynamodb inference
```

Allow the first model download to finish. Provide progress updates at least once per minute while waiting. Confirm all eight selected containers are healthy: two core, two vector, two DynamoDB, and two inference.

- [ ] **Step 4: Exercise DynamoDB data and UI**

From `dynamodb-admin`, run an AWS SDK v3 script that creates a disposable table with string hash key `id`, inserts item `{id: "smoke", value: "ok"}`, reads it, lists tables, and deletes the table after UI verification. Confirm the UI at `18003` displays the table and item before deletion.

- [ ] **Step 5: Exercise Chroma UI**

Open Chroma Admin at `18001`, connect it to `http://chroma:8000`, list collections, create a disposable smoke collection through Chroma's API, observe it in the UI, then delete it. Use the in-app browser only after loading the required browser-control skill.

- [ ] **Step 6: Exercise real CPU inference**

POST to LLM `/api/chat` with `stream:false`, model `gemma4:e4b`, and a short deterministic prompt; require a non-empty assistant message. POST to embedding `/api/embed` with model `embeddinggemma:300m`; require a non-empty array containing finite numeric values.

- [ ] **Step 7: Verify profile-scoped local tunnels**

Start the PowerShell tunnel for `vector dynamodb inference`, probe all six local endpoints, and load both UIs locally. Stop only the local SSH process afterward; leave remote services running.

- [ ] **Step 8: Verify cached restart and disposable persistence**

Record model volume sizes, stop and start `inference`, and confirm both services return healthy without downloading models again. Confirm ordinary `down` semantics preserve volumes; do not invoke destructive `destroy` during acceptance.

- [ ] **Step 9: Record sanitized evidence and run final verification**

Write exact image references, release ID, health states, API response shapes, UI checks, timing, memory/disk observations, and cached-restart result without secrets or model response internals. Run:

```bash
python -m unittest discover -s tests -v
git diff --check
git status --short
```

- [ ] **Step 10: Commit evidence, push, and verify master**

```bash
git add docs/verification/data-and-inference-gcp-smoke.md
git commit -m "test: verify data and inference stack on GCP"
git push origin HEAD:master
git ls-remote origin refs/heads/master
```

Require the remote `master` SHA to equal local `HEAD`. If acceptance required a code fix, use the systematic-debugging skill, add a regression test first, review it, and push that fix as its own completed task before the evidence commit.
