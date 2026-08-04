# Split GPU Inference Host Design

**Date:** 2026-08-04
**Status:** Approved

## Goal

Move the existing `ollama-llm` and `ollama-embedding` services from the
general-purpose data VM to a dedicated NVIDIA T4 VM while preserving the stack's
profile-based deployment model, loopback-only exposure, release safety, model-cache
persistence, and equivalent Bash and PowerShell operator workflows.

## Current deployment inputs

The initial GPU host is a Spot Compute Engine VM with these observed properties:

- Name: `nvidia-t4-26-gb-us-central-1`
- Zone: `us-central1-f`
- Machine type: `n1-highmem-4` with 4 vCPUs and 26 GB system memory
- Accelerator: one NVIDIA T4; NVIDIA GRID/vWS is disabled
- Image: `common-cu129-ubuntu-2404-nvidia-580-v20260730`
- Boot disk: 150 GB balanced persistent disk
- Backup plan: `default-schedule-1`, daily between 03:00 and 04:00
- Provisioning: Spot, no maximum duration, termination action `Stop`
- Host maintenance: terminate; automatic restart disabled
- External network tier: Premium
- IP forwarding, public HTTP, and public HTTPS: disabled
- Current internal address: `10.128.0.2`
- Current external address: `<public-ip>` (ephemeral)

The addresses are operational inputs only. No cloud VM address, zone, or instance name
is committed into Compose or operator scripts. The ignored target configuration carries
the current external address and is updated after a Spot stop/start changes it.

## Chosen approach

The existing `inference` profile becomes GPU-required. The repository continues to own
one Compose model and one release format, deployed independently to two Docker hosts.
No generic multi-host orchestration layer, cross-host Docker network, automatic CPU
fallback, or separate Ollama repository is added.

This approach is intentionally narrower than a Compose overlay. CPU inference is not an
active supported topology after cutover, so maintaining two acceleration modes would add
state and tests without serving the approved operation model.

## Host responsibilities

| Host | Active profiles | Responsibilities |
| --- | --- | --- |
| Existing data VM | `core`, `vector`, `dynamodb`, `search`, `observability`, `tools` | Databases, vector/search services, Langfuse dependencies, and browser administration tools |
| NVIDIA T4 VM | `inference` | The isolated chat and embedding Ollama services and their model volumes |

Both hosts use the exact Compose project name `remote-infra-stack`. This does not conflict
because each Docker daemon owns an independent project namespace, networks, containers,
releases, runtime environment, and named volumes.

The hosts do not call each other's service endpoints. Local applications remain the
integration point and reach each host through independent SSH tunnels. IP forwarding is
not needed.

## Local target configuration

Operators maintain two ignored files derived from `remote.env.example`:

- `remote.data.env` targets the existing data VM.
- `remote.gpu.env` targets the NVIDIA T4 VM.

The ignore contract covers both files. The existing `STACK_REMOTE_ENV` override remains
the target-selection interface; no new global target registry is introduced. Examples
show absolute paths so a terminal's working directory cannot silently select the wrong
host.

The existing repository-root `.env` remains the runtime configuration source for both
deployments. The full file is uploaded to both hosts because Compose interpolates the
shared model and the current deployment contract validates one exact key set. The GPU
host therefore receives secrets for inactive services. This is an accepted trade-off for
this personal stack; splitting secret schemas and release environments is out of scope.

## GPU host bootstrap

The local bootstrap entry points gain an explicit GPU option:

- Bash: `scripts/bootstrap.sh --gpu`
- PowerShell: `scripts/bootstrap.ps1 -Gpu`

They forward GPU mode to the shared remote bootstrap. Normal CPU bootstrap behavior is
unchanged.

GPU bootstrap is idempotent and must:

1. Complete the existing Ubuntu, architecture, Docker, group-membership, sysctl, and
   forwarding checks.
2. Require `nvidia-smi` from the Deep Learning VM image and fail with an actionable error
   instead of replacing the host driver.
3. Require exactly one detected NVIDIA T4 for this topology.
4. Install the NVIDIA Container Toolkit from NVIDIA's official apt repository when it is
   absent.
5. Configure the NVIDIA runtime for Docker and restart Docker only when configuration
   changes require it.
6. Verify the runtime after restart with a committed, non-`latest`, digest-pinned CUDA
   validation image whose `linux/amd64` manifest is checked remotely before acceptance.
7. Prove that a disposable container can see the T4 and that Docker remains healthy.

Dry-run mode prints every planned NVIDIA repository, package, configuration, restart, and
verification action without privileged mutation. CPU hosts never install NVIDIA
packages unless GPU mode was explicitly requested.

## Compose inference contract

The existing `inference` profile remains the public interface. Both services become
GPU-required by declaring GPU access in the base Compose model:

- `ollama-llm` receives the host GPU.
- `ollama-embedding` receives the same host GPU.

The services retain their current separation:

- Separate containers and Ollama server processes
- Separate named volumes (`ollama_llm_data` and `ollama_embedding_data`)
- Separate models (`gemma4:e4b` and `embeddinggemma:300m`)
- Separate loopback ports (`11440` and `11441`)
- One loaded model and one request stream per server
- Existing memory limits and 90-minute first-pull health window

Both containers may load their model concurrently on the T4. GPU memory is shared by the
two processes. A deployment is rejected if the two approved models cannot load and serve
their acceptance requests concurrently.

Inactive inference services must not cause GPU discovery or runtime requirements when a
data host renders, deploys, or operates only non-inference profiles. Attempting to start
`inference` on a CPU-only host is expected to fail clearly.

## Deployment and cutover

The first cutover follows this order:

1. Create `remote.gpu.env` with the current external IP, SSH user, port, identity, remote
   root, and existing local tunnel ports.
2. Run local checks for `inference` against the GPU target configuration.
3. Run GPU bootstrap and reconnect after any group-membership or runtime restart.
4. Deploy the clean committed release with only `inference` selected.
5. Allow both model volumes to download fresh copies. An interrupted pull is retried with
   the same named volumes so reusable layers remain available.
6. Complete GPU acceptance verification for both services.
7. Open the GPU SSH tunnel and repeat chat and embedding checks from the local machine.
8. Switch to `remote.data.env` and stop `inference` on the existing VM.
9. Verify every non-inference service on the data VM remains healthy.

The old CPU-host Ollama containers remain stopped. Their named volumes are preserved but
are not an operational fallback. Routine data-host deployments omit `inference`.

## Operator workflow

PowerShell examples:

```powershell
$env:STACK_REMOTE_ENV = (Resolve-Path .\remote.gpu.env)
.\scripts\bootstrap.ps1 -Gpu
.\scripts\deploy.ps1 inference
.\scripts\tunnel.ps1 inference
```

In a separate terminal:

```powershell
$env:STACK_REMOTE_ENV = (Resolve-Path .\remote.data.env)
.\scripts\deploy.ps1 core vector dynamodb search observability tools
.\scripts\tunnel.ps1 core vector dynamodb search observability tools
```

Bash exposes the same operations through `STACK_REMOTE_ENV` and `--gpu`.

The two tunnel processes preserve all local application endpoints. Inference remains:

- Chat API: `http://127.0.0.1:11440`
- Embedding API: `http://127.0.0.1:11441`

No consuming application configuration changes beyond keeping the appropriate tunnels
open.

## GPU acceptance verification

Container health alone is insufficient because Ollama can serve through CPU fallback.
GPU acceptance must establish actual accelerated execution:

1. `nvidia-smi` reports the T4 on the host.
2. Docker reports GPU device requests for both Ollama containers.
3. A bounded chat generation request succeeds through the chat server.
4. A bounded embedding request succeeds through the embedding server.
5. Each Ollama server's `/api/ps` response reports a positive VRAM allocation for its
   loaded approved model.
6. Host `nvidia-smi` observes both Ollama processes or their combined GPU memory usage
   while both models remain loaded.
7. Both published ports remain bound only to `127.0.0.1`.
8. The same requests succeed through the local SSH tunnel.

GPU acceptance is part of inference deployment health. A release cannot become `current`
when either model serves exclusively from CPU, cannot load, or cannot answer its bounded
request.

## Failure behavior

- Missing driver, wrong GPU, toolkit failure, or failed Docker GPU access stops bootstrap
  before deployment.
- A failed image pull, model download, Compose wait, endpoint request, or GPU-use check
  leaves the previous GPU release inactive and does not change the data host.
- Interrupted model downloads are retried against persistent named volumes.
- The data-host Ollama services are not stopped until GPU acceptance passes.
- There is no automatic CPU failover after cutover.
- Data-host and GPU-host deployment locks, current symlinks, runtime files, releases, and
  rollback behavior remain independent.

## Spot lifecycle and recovery

The GPU containers keep `restart: unless-stopped`. After a Spot stop/start:

1. The persistent boot disk, Docker named volumes, release tree, and model caches remain.
2. Docker restarts both inference containers when the VM returns.
3. The operator obtains the new ephemeral external address.
4. The operator updates only `REMOTE_HOST` in `remote.gpu.env`.
5. The operator reopens the inference tunnel and runs the inference check.

A new release deployment is not required solely because the external IP changed. The
daily Backup and DR schedule is the disk-level recovery path. Restoring backup media and
recreating deleted cloud infrastructure remain manual cloud operations outside this
repository.

## Security boundaries

- No application or UI port is opened in a cloud firewall.
- Every Compose-published inference port binds to VM loopback.
- SSH is the only public ingress used by the operator.
- The internal address is not exposed to local applications.
- GPU bootstrap uses only official Docker, NVIDIA, Ubuntu, and configured image registry
  sources.
- Secrets remain ignored locally, uploaded separately, mode-restricted remotely, and
  absent from release archives and command output.

## Test strategy

### Repository and Compose contracts

- Assert both Ollama services retain the exact `inference` profile and require GPU access.
- Assert their models, ports, volumes, health windows, concurrency settings, and limits do
  not regress.
- Assert non-inference profiles render without requiring a local or remote GPU.
- Assert the CUDA validation image is committed, non-`latest`, digest-pinned, and remotely
  verified for `linux/amd64`.

### Bootstrap contracts

- Add fake `nvidia-smi`, apt, toolkit, Docker, and systemctl behavior.
- Test CPU bootstrap remains unchanged without GPU mode.
- Test dry-run non-mutation, wrong/missing GPU rejection, idempotent toolkit installation,
  conditional Docker restart, and failed container-GPU verification.
- Maintain Bash and PowerShell option parity.

### Operator and lifecycle contracts

- Test alternate data and GPU target files through `STACK_REMOTE_ENV`.
- Test GPU bootstrap argument forwarding in both shells.
- Test deployment rejection when GPU acceptance fails before release activation.
- Test inference tunnel mappings remain exactly `11440` and `11441`.
- Test data-host stop preserves CPU model volumes.

### Remote acceptance

- Bootstrap the approved T4 VM.
- Deploy only `inference` and perform the GPU acceptance sequence.
- Re-run deployment to demonstrate model-cache reuse.
- Stop inference on the data VM and verify all remaining profiles.
- Stop/start the Spot VM, update the ignored ephemeral address, and verify automatic
  container restart, persistent models, and renewed tunnel access.

Sanitized command evidence may be committed under `docs/verification`; secrets, instance
metadata, complete IP addresses, and raw model output are not committed.

## Non-goals

- Cloud VM, disk, network, firewall, static-IP, quota, or backup provisioning
- A generalized multi-host scheduler or target registry
- Cross-host Docker networking or service discovery
- Automatic DNS or ephemeral-IP synchronization
- Automatic CPU failover
- GPU partitioning, MIG, Kubernetes, or multi-GPU scheduling
- Model changes, quantization changes, or inference API changes
- Separate secret schemas per host
- Migrating CPU-host Docker volumes to the T4 host

## Success criteria

The work is complete when:

1. Both Ollama services run on the dedicated T4 VM and demonstrate positive VRAM use.
2. No inference container is running on the data VM.
3. Every non-inference data-host service remains healthy.
4. Local chat and embedding endpoints remain unchanged through the GPU SSH tunnel.
5. No service port is publicly exposed.
6. Fresh deployment, retry, rollback, Spot restart, and ephemeral-IP update workflows are
   documented and verified.
7. The complete local contract suite and all available shell parity checks pass.
