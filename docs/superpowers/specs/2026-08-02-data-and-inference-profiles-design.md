# Data and Inference Profiles Design

Status: approved on 2026-08-02

## Purpose

Extend `remote-infra-stack` with browser administration for Chroma, a local DynamoDB development service with a browser UI, and two isolated Ollama inference servers. The extension keeps the repository's existing single-VM, profile-selected, loopback-only operating model.

The immediate target is the existing GCP `e2-standard-8` Ubuntu `amd64` VM with 8 vCPUs and 32 GiB of memory. CPU inference may be slow and that is acceptable. The same Compose model must remain usable on an existing SSH-accessible Ubuntu LTS `amd64` VM in GCP or AWS.

## Goals

- Add an unofficial Chroma administration UI to the existing `vector` profile.
- Add a `dynamodb` profile containing official DynamoDB Local and an unofficial DynamoDB Admin UI.
- Add one `inference` profile containing two separate Ollama containers.
- Serve `gemma4:e4b` from the LLM container and `embeddinggemma:300m` from the embedding container.
- Make every API and UI available to local applications and browsers through profile-scoped SSH tunnels.
- Keep all VM port bindings on loopback and require no public service firewall rules.
- Block the first deployment until both model downloads have completed and both inference APIs are ready.
- Preserve models and service data across releases in named Docker volumes while retaining the existing explicit disposable-data behavior.
- Keep Bash and PowerShell operator behavior equivalent.
- Validate the complete change on the user's actual CPU-only GCP VM before declaring it complete.

## Non-goals

- Production or high-availability deployment.
- Public ingress, TLS termination, DNS, reverse proxying, or UI/API authentication.
- GPU support or cloud-specific accelerator configuration in this change.
- Automated backups, exports, restores, or migration of DynamoDB, Chroma, or Ollama data.
- An inference chat UI or OpenAI-compatible translation proxy.
- Running every profile at its configured peak memory on a 32-GiB VM.
- ARM64 support; the repository continues to target verified Ubuntu `amd64` hosts.

## Selected Approach

The repository-owned `compose.yaml` is extended directly. Compose profiles, the remote release receiver, health verification, lifecycle scripts, SSH tunnel scripts, configuration examples, tests, and documentation remain one coherent contract.

Two alternatives were rejected:

1. Separate Compose override files would make each Bash and PowerShell operation assemble a different file set and would weaken the current single-manifest validation.
2. One shared Ollama container would reduce container count but would not provide the requested isolation between LLM and embedding inference, and would make lifecycle and memory behavior less explicit.

## Profiles and Services

The supported profile set becomes:

| Profile | Services | Purpose |
| --- | --- | --- |
| `core` | `app-postgres`, `app-redis` | Existing application databases |
| `vector` | `chroma`, `chroma-admin` | Chroma API and browser administration |
| `search` | `opensearch`, `opensearch-dashboards` | Existing search API and UI |
| `observability` | Existing Langfuse topology | Existing tracing stack |
| `tools` | `pgadmin`, `redisinsight` | Existing `core` administration UIs |
| `dynamodb` | `dynamodb-local`, `dynamodb-admin` | DynamoDB-compatible development API and UI |
| `inference` | `ollama-llm`, `ollama-embedding` | Separate LLM and embedding inference APIs |

`tools` continues to require `core`. The new profiles have no dependency on another profile. Selecting `inference` always starts both Ollama containers, as requested.

## Service Topology

### Chroma Admin

`chroma-admin` uses the unofficial `flanker/chromadb-admin` project. Its published `fengzhichao/chromadb-admin:0.0.2` image was inspected from the target GCP Docker host and is a single-platform `linux/arm64/v8` manifest, so it cannot run natively on the repository's required `linux/amd64` hosts.

Instead, the repository vendors a license-preserving source snapshot from exact upstream commit `efe867c86c78683d90b0eb74b88b351fc08f0b5f`, which includes the Chroma v2 API migration. A repository-owned multi-stage Dockerfile uses a digest-pinned `linux/amd64` Node 20 base, installs with `npm ci` from the vendored lockfile, builds the application, and runs it as a non-root user on port 3001. Development artifacts, alternate lockfiles, and Git metadata are excluded. This keeps the approved UI while avoiding architecture emulation and an unauditable upstream image.

It belongs to the existing `vector` profile, depends on healthy `chroma`, and connects to Chroma over the private Compose network at `http://chroma:8000`. If the UI requires its connection to be entered interactively, the operations guide documents that internal URL. Its published VM port and laptop tunnel never use port 8000.

The UI is stateless and does not receive a named volume unless validation proves the selected image needs one for user settings.

### DynamoDB Local and Admin

`dynamodb-local` uses the official `amazon/dynamodb-local` image. It runs with `-sharedDb` and stores its database in a named volume. Shared-database mode ensures the application and UI see the same tables regardless of their development credential identity.

`dynamodb-admin` uses the unofficial `aaronshaf/dynamodb-admin` image, depends on healthy `dynamodb-local`, and connects to `http://dynamodb-local:8000` over the private Compose network. Both services use explicit dummy development values:

```text
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=local
AWS_SECRET_ACCESS_KEY=local
```

These values are not cloud credentials and must not be replaced with real AWS credentials. A local application uses the tunneled endpoint together with dummy credentials and the same region.

### Ollama LLM

`ollama-llm` uses a pinned official Ollama image and a dedicated `ollama_llm_data` named volume. It downloads and serves only the committed model selection `gemma4:e4b`.

### Ollama Embeddings

`ollama-embedding` uses the same pinned official Ollama image and a separate `ollama_embedding_data` named volume. It downloads and serves only `embeddinggemma:300m`. The selected Ollama release must satisfy EmbeddingGemma's minimum Ollama version.

Each container has its own API, model cache, memory limit, health check, and lifecycle. Neither container shares model files or an inference process with the other.

## Ports and SSH Tunnels

All published VM ports bind explicitly to `127.0.0.1`. Docker-internal standard ports are intentionally retained because they cannot collide across containers. Only the VM loopback and laptop tunnel ports form the user-facing contract.

| Service | Container port | VM loopback port | Default laptop endpoint |
| --- | ---: | ---: | --- |
| Chroma API | 8000 | 18000 | `http://127.0.0.1:18000` |
| Chroma Admin | 3001 | 18001 | `http://127.0.0.1:18001` |
| DynamoDB Local | 8000 | 18002 | `http://127.0.0.1:18002` |
| DynamoDB Admin | 8001 | 18003 | `http://127.0.0.1:18003` |
| Ollama LLM | 11434 | 11440 | `http://127.0.0.1:11440` |
| Ollama Embeddings | 11434 | 11441 | `http://127.0.0.1:11441` |

`remote.env.example` gains these local overrides:

```text
LOCAL_CHROMA_ADMIN_PORT=18001
LOCAL_DYNAMODB_PORT=18002
LOCAL_DYNAMODB_ADMIN_PORT=18003
LOCAL_OLLAMA_LLM_PORT=11440
LOCAL_OLLAMA_EMBEDDING_PORT=11441
```

Existing local-port validation continues to reject malformed, duplicate, unavailable, or out-of-range ports before opening SSH. Tunnel construction remains deterministic and includes mappings only for selected profiles.

## Images and Model Selection

`versions.env` remains the committed version catalog. It gains exact, non-`latest` container references for:

- The Node build/runtime base used for the repository-built Chroma Admin image
- DynamoDB Local
- DynamoDB Admin
- Ollama

Every reference is verified from the remote Docker host to contain a `linux/amd64` manifest and is then committed with its manifest-list digest. The verified manifest inventory and repository contract tests are updated in the same task.

The initial verified selections are:

```text
DYNAMODB_LOCAL_IMAGE=docker.io/amazon/dynamodb-local:3.3.0@sha256:d89f8fcc6b1a39cb35976c248ed42a28c66ae00dc043099210f5571e42648ab4
DYNAMODB_ADMIN_IMAGE=docker.io/aaronshaf/dynamodb-admin:5.3.4@sha256:ac41724cd99706256d405a14a5fb96f51f18c41a630c84fa3357f900cbd16d2e
OLLAMA_IMAGE=docker.io/ollama/ollama:0.32.5@sha256:4dea9fb511947e24a84237bb636b0203abcb2ff0d3fbc7b4ff865deb91362131
```

The vendored Chroma Admin snapshot records its exact upstream commit and license. The locally built image is tagged with the source revision, is not treated as a registry image, and is rebuilt from pinned repository inputs when required. Its resulting image identity is recorded during GCP verification. Remote deployment pulls registry services with Compose's buildable services excluded, then builds the selected local image before starting services.

The requested Ollama model tags are also committed configuration:

```text
OLLAMA_LLM_MODEL=gemma4:e4b
OLLAMA_EMBEDDING_MODEL=embeddinggemma:300m
```

Ollama registry model tags do not provide the same immutable deployment contract as a container image digest. Startup records the resolved model information, and GCP verification records the models observed after download. A future model update is an intentional committed change followed by another remote smoke test.

## Inference Bootstrap and Readiness

A repository-owned bootstrap script is mounted read-only into both Ollama containers. The script:

1. Starts `ollama serve` and installs signal handling for a clean container stop.
2. Waits for the local Ollama API to accept requests.
3. Checks whether the container's configured model is already present in its named volume.
4. Pulls the model when missing, allowing Ollama to resume an interrupted download.
5. Verifies that the expected model is registered.
6. Keeps the Ollama server in the foreground as the container's long-running process.

Transient model-pull failures are retried with bounded backoff and Ollama's resumable download behavior. If download or verification still fails, the container does not become healthy and the deployment fails. The health start period and deployment wait allowance are deliberately generous for a roughly 10-GB first download; no short timeout may cause a normal CPU-hosted pull to be treated as successful or silently skipped.

An ordinary redeployment reuses the named model volumes and therefore does not download an unchanged model again.

## Health Contract

Compose health checks establish process-level readiness. The repository's remote `health.sh` then verifies the selected profile's externally useful behavior.

The new checks cover:

- Chroma Admin returns a successful HTTP response.
- DynamoDB Local accepts a DynamoDB API request.
- DynamoDB Admin returns a successful HTTP response.
- Each Ollama API is reachable and reports its exact configured model.

Routine deployment health does not run a full CPU inference on every release. The implementation's GCP acceptance test additionally:

- Creates, lists, reads, and deletes a disposable DynamoDB table or item.
- Loads the Chroma Admin UI against the existing Chroma service.
- Loads the DynamoDB Admin UI and observes the test table.
- Sends a small prompt to `gemma4:e4b` and requires a non-empty response.
- Sends text to `embeddinggemma:300m` and requires a non-empty numeric vector.
- Repeats API reachability through the local SSH tunnels.

## Resource Model

The committed `.env.example` and generated ignored `.env` use these 32-GiB CPU-host defaults:

```text
CHROMA_ADMIN_MEMORY=512m
DYNAMODB_MEMORY=1g
DYNAMODB_ADMIN_MEMORY=512m
OLLAMA_LLM_MEMORY=14g
OLLAMA_EMBEDDING_MEMORY=2g
OLLAMA_CONTEXT_LENGTH=8192
OLLAMA_KEEP_ALIVE=5m
```

Both Ollama containers use one loaded model and one parallel request as conservative CPU defaults. These settings reduce concurrent memory pressure without changing the selected models.

Compose memory limits are caps, not reservations. `core`, `vector`, `dynamodb`, and `inference` are intended to fit together under personal-development load on the existing 32-GiB VM. Adding the complete search and observability profiles can make the sum of configured limits exceed host memory. Preflight emits a prominent warning but does not prevent an intentional deployment.

If the VM is resized above 32 GiB, the ignored `.env` may raise service limits, context length, or concurrency without changing Compose or creating a cloud-specific variant.

Inference preflight also checks the Docker storage filesystem, not only the release-directory filesystem. It requires enough free space for approximately 10.2 GB of requested model content plus Ollama images and download overhead. The exact safety threshold is specified by implementation tests and documented for operators.

## Configuration and Secrets

The new services require no new secret values:

- Chroma Admin connects to the existing unauthenticated, loopback-protected Chroma development API.
- DynamoDB Local uses hard-coded dummy development credentials.
- Ollama uses public model downloads and local API access.

`init-env` adds the new resource settings when generating `.env`. `remote.env.example` gains the port settings, and operators merge those non-secret keys into an existing ignored `remote.env`; the repository does not generate that file. Existing generated secrets remain unchanged. Both real files continue to be ignored and mode-protected. Only `.env` is uploaded separately from the committed release archive; `remote.env` remains on the operator's machine.

## Security Boundary

Chroma Admin, DynamoDB Admin, DynamoDB Local, and Ollama do not provide a shared authentication boundary suitable for public exposure. Therefore:

- Every published port is bound to VM loopback.
- Every SSH forward binds to laptop loopback.
- No cloud firewall opening is required or documented.
- Documentation explicitly warns against changing bindings to `0.0.0.0`.
- The UIs and APIs are trusted personal-development tools reachable only by a user who already has SSH access to the VM.
- Third-party UI images are pinned and identified as unofficial in documentation.

## Operator Experience

Both operator surfaces accept the expanded profile set with matching behavior. Examples:

```bash
./scripts/check.sh core vector dynamodb inference
./scripts/deploy.sh core vector dynamodb inference
./scripts/tunnel.sh core vector dynamodb inference
```

```powershell
.\scripts\check.ps1 core vector dynamodb inference
.\scripts\deploy.ps1 core vector dynamodb inference
.\scripts\tunnel.ps1 core vector dynamodb inference
```

`up`, `stop`, `logs`, `status`, `down`, and `destroy` understand the new profiles and services. `down` preserves all named volumes. The existing explicit `destroy remote-infra-stack DESTROY-remote-infra-stack` operation deletes DynamoDB data, Chroma data, and both model caches together with the other stack volumes.

README and operations documentation provide connection examples for:

- Chroma's Python HTTP client.
- AWS SDK/CLI clients with `endpoint_url=http://127.0.0.1:18002` and dummy credentials.
- Ollama chat/generate calls through `http://127.0.0.1:11440`.
- Ollama embedding calls through `http://127.0.0.1:11441`.
- Both new browser UIs.

## Failure and Release Behavior

The existing versioned release contract remains authoritative:

- Only committed Git content is archived.
- The ignored `.env` is uploaded separately.
- Images are pulled before services start.
- Selected repository-owned images are built from pinned inputs before services start.
- Selected services must become healthy before the release is activated.
- A failed model pull, image pull, local image build, Compose render, service startup, or remote health check fails the deployment and leaves `current` on the prior active release.
- Failure handling removes containers created only by the unactivated release and restores previously active selected services without deleting named volumes. Partial Ollama model data is preserved so a later deployment can resume downloading, while failed-release containers cannot become orphans that the prior release cannot manage.
- Named volumes remain outside release directories and release pruning never deletes them.

First inference deployment is expected to be substantially slower than subsequent deployments. Logs expose model download progress, and an interrupted deployment can resume from the partially populated named volume.

## Test Strategy

Implementation follows test-driven development. Automated coverage includes:

- Repository and image-inventory contracts for all new pinned inputs.
- Chroma Admin source pin, checksum, Dockerfile, build selection, and `linux/amd64` runtime contract.
- Compose rendering for all seven profiles.
- Exact profile-to-service ownership and dependency invariants.
- Environment defaults, validation, and redaction behavior.
- Bash and PowerShell profile validation and command parity.
- Exact SSH forward sets and collision detection.
- Remote lifecycle expansion for `stop`, `logs`, and health.
- Inference bootstrap success, cached startup, interrupted pull, pull failure, and signal handling using fakes.
- Release activation and rollback while inference initialization is pending or failed.
- Failed-release cleanup for newly introduced containers without deleting partial model volumes.
- Documentation endpoint and safety assertions.
- Full existing regression suite.

Remote acceptance on the configured GCP VM verifies image manifests before pulling, deploys only the profiles needed for each smoke phase, exercises the APIs and UIs, and records non-secret evidence. The VM remains CPU-only throughout this validation.

## Delivery and Commit Policy

The specification, implementation tasks, review fixes, and verification evidence are committed as separate completed units. After each task passes its relevant checks, its commit is pushed directly to the GitHub `master` branch as requested. No unverified working state is pushed.

## References

- [Ollama Gemma 4 model](https://ollama.com/library/gemma4)
- [Ollama EmbeddingGemma model](https://ollama.com/library/embeddinggemma)
- [Ollama pull API](https://docs.ollama.com/api/pull)
- [AWS DynamoDB Local Docker guidance](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/DynamoDBLocal.DownloadingAndRunning.html)
- [Chroma Admin project](https://github.com/flanker/chromadb-admin)
- [DynamoDB Admin project](https://github.com/aaronshaf/dynamodb-admin)
