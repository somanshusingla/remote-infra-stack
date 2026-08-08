# Remote Infra Stack

Remote Infra Stack runs its data services and GPU inference on separate existing SSH-accessible
Ubuntu VMs. Applications and browsers stay on your Windows, macOS, or
Linux machine and connect through profile-scoped SSH tunnels; service ports are never
published on either VM's public interfaces.

This is a personal development stack, not production infrastructure. Its data is
**disposable**. Persistent state uses named Docker volumes, but deleting the VM or
running `destroy` permanently loses that state. No backup/export automation is included.

For the full command-by-command runbook, see [docs/operations.md](docs/operations.md).

## What it runs

No service starts unless its profile is selected.

| Profile | Services | Purpose |
| --- | --- | --- |
| `core` | PostgreSQL, Redis | Application datastores |
| `vector` | Chroma, `chroma-admin` | Vector database API and browser UI |
| `search` | OpenSearch, OpenSearch Dashboards | Search API and ELK-style browser UI |
| `observability` | Langfuse web/worker, dedicated PostgreSQL and Redis, ClickHouse, MinIO | Isolated tracing stack |
| `tools` | pgAdmin, RedisInsight | Administration UIs for `core`; always select `core` with `tools` |
| `dynamodb` | DynamoDB Local, `dynamodb-admin` | Local-compatible API and browser UI |
| `inference` | Two isolated Ollama servers | GPU chat and embedding inference |

Application PostgreSQL and Redis are independent from the private PostgreSQL and Redis
used by Langfuse.

## Requirements and supported hosts

Run the local scripts from the repository root. The local machine needs Git, OpenSSH
(`ssh` and `scp`), and either:

- Bash and OpenSSL on macOS/Linux, with the remaining standard utilities checked by
  `./scripts/check.sh`; or
- Windows PowerShell 5.1+ or PowerShell 7+, using the `.ps1` scripts.

The project does not provision cloud resources. Supply two existing SSH-accessible
Ubuntu VMs in AWS, GCP, or another provider with:

- Ubuntu 22.04, Ubuntu 24.04, or Ubuntu 26.04 LTS on `amd64`;
- systemd, apt, and passwordless `sudo` for the SSH user; and
- a direct OpenSSH/SCP route from the local machine. Only the SSH route needs a cloud
  firewall rule.

The remote preflight requires at least 10 GiB free below the release root and
`net.ipv4.ip_forward=1` for Docker bridge egress. Selecting `inference` additionally
requires at least 20 GiB free on Docker's storage filesystem for the two model caches
and image layers. The Compose memory values are container limits, not reservations.
A 32 GiB host does not guarantee that all profiles fit at peak; select only what you need,
monitor the VM, and resize the host or raise individual limits when real workloads
require it. Put `inference` on the GPU host; its first model download can still take
minutes, while later deployments reuse its persistent model caches.

`net.ipv4.ip_forward=1` is a host-global IPv4 routing capability, not a container-only
setting. Stack ports remain loopback-only behind an SSH-only cloud firewall. On a
multi-NIC host or one with a custom host firewall, routing and firewall policy are the
operator responsibility.

Ubuntu 26.04 bootstrap and pinned `linux/amd64` image manifests were verified on a
minimal GCP VM; the sanitized evidence is in
[docs/verification/task-7-ubuntu-bootstrap.md](docs/verification/task-7-ubuntu-bootstrap.md).
The data profiles and inference were subsequently deployed and exercised; that
sanitized acceptance record is in
[docs/verification/data-and-inference-gcp-smoke.md](docs/verification/data-and-inference-gcp-smoke.md).
All seven profiles and their browser UIs were then started together on that VM; the
sanitized record is in
[docs/verification/all-profiles-gcp-smoke.md](docs/verification/all-profiles-gcp-smoke.md).
Support for a future Ubuntu LTS is capability-gated, not promised by version number:
the bootstrap proceeds only when Docker's official Docker apt repository exists for
the detected codename and all required packages are available. Container publishers
must also support that release.

## Quick start

The canonical workflow has two targets: the data host runs `core`, `vector`,
`dynamodb`, and optionally `search`, `observability`, or `tools`; the GPU host runs
only `inference`. `tools` requires `core`, and duplicate or unknown profiles are
rejected. Both targets deliberately share the same ignored `.env`: this is an accepted
secret-distribution trade-off for this personal stack, so restrict SSH access to both
hosts and do not treat the GPU host as a less-trusted environment.

### macOS/Linux Bash

```bash
git clone https://github.com/somanshusingla/remote-infra-stack.git
cd remote-infra-stack

./scripts/init-env.sh
cp remote.env.example remote.data.env
cp remote.env.example remote.gpu.env
${EDITOR:-vi} remote.data.env remote.gpu.env

STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/check.sh core vector dynamodb
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/bootstrap.sh
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/deploy.sh core vector dynamodb
STACK_REMOTE_ENV=/absolute/path/to/remote.gpu.env ./scripts/check.sh inference
STACK_REMOTE_ENV=/absolute/path/to/remote.gpu.env ./scripts/bootstrap.sh
STACK_REMOTE_ENV=/absolute/path/to/remote.gpu.env ./scripts/deploy.sh inference
```

First open the GPU acceptance tunnel in one terminal:

```bash
STACK_REMOTE_ENV=/absolute/path/to/remote.gpu.env ./scripts/tunnel.sh inference
```

In another terminal, run both Ollama calls shown in [Configure local
applications](#configure-local-applications). If either fails, leave legacy CPU
inference running and press `Ctrl+C` in the failed GPU tunnel terminal to release
`127.0.0.1:11440` and `127.0.0.1:11441`. Then select the data target and reopen the
legacy path with `inference` (and any required data profiles); do not run `stop
inference`:

```bash
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/tunnel.sh core vector dynamodb inference
```

Diagnose or retry the GPU target before another cutover attempt. Only after both calls
succeed, stop the legacy CPU service and open the data tunnel:

```bash
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/stack.sh stop inference
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/tunnel.sh core vector dynamodb
```

Keep both tunnel commands running while local clients use the endpoints. The preserved
CPU model volumes are not a supported fallback.

### Windows PowerShell

```powershell
git clone https://github.com/somanshusingla/remote-infra-stack.git
Set-Location remote-infra-stack

.\scripts\init-env.ps1
Copy-Item .\remote.env.example .\remote.data.env
Copy-Item .\remote.env.example .\remote.gpu.env
notepad .\remote.data.env
notepad .\remote.gpu.env

$env:STACK_REMOTE_ENV = 'C:\absolute\path\to\remote.data.env'
.\scripts\check.ps1 core vector dynamodb
.\scripts\bootstrap.ps1
.\scripts\deploy.ps1 core vector dynamodb
$env:STACK_REMOTE_ENV = 'C:\absolute\path\to\remote.gpu.env'
.\scripts\check.ps1 inference
.\scripts\bootstrap.ps1
.\scripts\deploy.ps1 inference
```

First open the GPU acceptance tunnel in one PowerShell window:

```powershell
$env:STACK_REMOTE_ENV = 'C:\absolute\path\to\remote.gpu.env'
.\scripts\tunnel.ps1 inference
```

In another window, run both PowerShell Ollama calls shown in [Configure local
applications](#configure-local-applications). If either fails, leave legacy CPU
inference running and press `Ctrl+C` in the failed GPU tunnel window to release
`127.0.0.1:11440` and `127.0.0.1:11441`. Then select the data target and reopen the
legacy path with `inference` (and any required data profiles); do not run `stop
inference`:

```powershell
$env:STACK_REMOTE_ENV = 'C:\absolute\path\to\remote.data.env'
.\scripts\tunnel.ps1 core vector dynamodb inference
```

Diagnose or retry the GPU target before another cutover attempt. Only after both calls
succeed, stop the legacy CPU service and open the data tunnel:

```powershell
$env:STACK_REMOTE_ENV = 'C:\absolute\path\to\remote.data.env'
.\scripts\stack.ps1 stop inference
.\scripts\tunnel.ps1 core vector dynamodb
```

Keep both PowerShell tunnel windows open for local clients. The preserved CPU model
volumes are not a supported fallback.

### Configuration files

`init-env` generates an ignored `.env` containing service credentials. It refuses to
overwrite an existing file unless `--force` (Bash) or `-Force` (PowerShell) is given.
Do not commit or share this file.

When upgrading an existing clone, preserve its secrets. Do not run
`init-env --force` or `init-env.ps1 -Force` to regenerate the ignored `.env`; manually
append these seven non-secret defaults when they are absent:

```dotenv
CHROMA_ADMIN_MEMORY=512m
DYNAMODB_MEMORY=1g
DYNAMODB_ADMIN_MEMORY=512m
OLLAMA_LLM_MEMORY=14g
OLLAMA_EMBEDDING_MEMORY=2g
OLLAMA_CONTEXT_LENGTH=8192
OLLAMA_KEEP_ALIVE=30m
```

If an existing `.env` already contains `OLLAMA_KEEP_ALIVE=5m`, replace only that
assignment with `OLLAMA_KEEP_ALIVE=30m`; do not regenerate the file or its secrets.

Copy `remote.env.example` to both ignored target files, then set the data host in
`remote.data.env` and the GPU host in `remote.gpu.env`:

```dotenv
REMOTE_HOST=remote-infra-stack
REMOTE_USER=
REMOTE_PORT=22
REMOTE_IDENTITY_FILE=
REMOTE_ROOT=remote-infra-stack
```

For an existing ignored target file, manually add these five tunnel settings instead of
replacing the file:

```dotenv
LOCAL_CHROMA_ADMIN_PORT=18001
LOCAL_DYNAMODB_PORT=18002
LOCAL_DYNAMODB_ADMIN_PORT=18003
LOCAL_OLLAMA_LLM_PORT=11440
LOCAL_OLLAMA_EMBEDDING_PORT=11441
```

`REMOTE_HOST` may be a DNS name, IP address, or local SSH-config alias. Set it
independently in each target file; `STACK_REMOTE_ENV` must be the absolute path to the
target file for every `check`, `bootstrap`, `deploy`, `tunnel`, or `stack` command. Set
`REMOTE_USER` and `REMOTE_IDENTITY_FILE` when they are not already supplied by the SSH
configuration. Keep `REMOTE_ROOT` relative to the remote user's home. The remaining
`LOCAL_*_PORT` values in `remote.env.example` control the local side of each tunnel.
The data tunnel owns the data endpoints and the GPU tunnel owns only
`127.0.0.1:11440` and `127.0.0.1:11441`.

`check` validates `.env`, the selected ignored target file, the selected profiles,
required local commands, script syntax, a clean committed Git `HEAD`, and the Compose model when local Docker
Compose is available. It does not mutate the VM. `deploy` archives only the clean Git
`HEAD` and uploads `.env` separately with private permissions.

## Local endpoints

Every container port binds to `127.0.0.1` on the VM. The selected tunnel exposes these
defaults only on the local loopback interface:

| Profile | Service | Local endpoint | Notes |
| --- | --- | --- | --- |
| `core` | Application PostgreSQL | `127.0.0.1:5432` | VM loopback port 15432 |
| `core` | Application Redis | `127.0.0.1:6379` | VM loopback port 16379 |
| `vector` | Chroma | `http://127.0.0.1:18000` | HTTP API/SDK |
| `vector` | `chroma-admin` | `http://127.0.0.1:18001` | Unofficial browser UI; connect it to `http://chroma:8000` |
| `dynamodb` | DynamoDB Local | `http://127.0.0.1:18002` | API; use dummy local AWS credentials |
| `dynamodb` | `dynamodb-admin` | `http://127.0.0.1:18003` | Browser UI |
| `inference` | Ollama chat | `http://127.0.0.1:11440` | `gemma4:e4b` API |
| `inference` | Ollama embeddings | `http://127.0.0.1:11441` | `embeddinggemma:300m` API |
| `search` | OpenSearch | `https://127.0.0.1:9200` | Authenticated API with development TLS certificate |
| `search` | OpenSearch Dashboards | `http://127.0.0.1:5601` | ELK-style/Kibana-equivalent UI |
| `observability` | Langfuse | `http://127.0.0.1:3000` | Browser UI and API base URL |
| `observability` | MinIO API | `http://127.0.0.1:9090` | S3-compatible API |
| `observability` | MinIO Console | `http://127.0.0.1:9091` | Browser UI |
| `tools` | pgAdmin | `http://127.0.0.1:5050` | Browser UI |
| `tools` | RedisInsight | `http://127.0.0.1:5540` | Browser UI |

Override local ports in the relevant target file if a default is occupied. The tunnel refuses
duplicate ports and, where the platform supports probing, refuses ports that are
already in use.

Every host-side Compose publication and SSH tunnel listener remains loopback-only.
Never change those `127.0.0.1` bindings to `0.0.0.0`, and do not add public cloud
firewall rules for any endpoint in this table.

Enabling `net.ipv4.ip_forward` permits Docker bridge egress but does not itself publish
a Compose port. This repository does not change iptables/UFW policy, Docker daemon
networking, or a cloud VM's IP-forwarding setting. Access control therefore continues
to rely on the exact loopback bindings above and an SSH-only cloud firewall.

## Configure local applications

After the matching profiles are deployed and their tunnel is running, add localhost
endpoints to the consuming application's own environment. Replace `<password>` with
the corresponding value from the ignored infrastructure `.env`:

```dotenv
DATABASE_URL=postgresql://app:<password>@127.0.0.1:5432/app
REDIS_URL=redis://:<password>@127.0.0.1:6379/0
CHROMA_HOST=127.0.0.1
CHROMA_PORT=18000
OPENSEARCH_URL=https://127.0.0.1:9200
LANGFUSE_BASE_URL=http://127.0.0.1:3000
```

On first use of Chroma Admin, enter the Compose-network address
`http://chroma:8000`. That internal address is correct for the UI container; local
applications continue to use `http://127.0.0.1:18000` through the SSH tunnel.

DynamoDB Local accepts dummy credentials. For example:

```python
import boto3

dynamodb = boto3.client(
    "dynamodb",
    endpoint_url="http://127.0.0.1:18002",
    region_name="us-east-1",
    aws_access_key_id="local",  # AWS_ACCESS_KEY_ID=local
    aws_secret_access_key="local",
)
print(dynamodb.list_tables(Limit=10))
```

The isolated Ollama endpoints use the normal HTTP API:

```bash
curl http://127.0.0.1:11440/api/chat \
  -d '{"model":"gemma4:e4b","messages":[{"role":"user","content":"Hello"}],"stream":false}'

curl http://127.0.0.1:11441/api/embed \
  -d '{"model":"embeddinggemma:300m","input":"hello from the remote stack"}'
```

PowerShell uses the same endpoints without relying on its `curl` alias:

```powershell
$chat = @{
  model = 'gemma4:e4b'
  messages = @(@{ role = 'user'; content = 'Hello' })
  stream = $false
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:11440/api/chat' -ContentType 'application/json' -Body $chat

$embed = @{ model = 'embeddinggemma:300m'; input = 'hello from the remote stack' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:11441/api/embed' -ContentType 'application/json' -Body $embed
```

The first `inference` deployment waits while each container downloads and verifies its
model, so it can be much slower than later deployments. Downloads live in separate
named volumes. If a pull or deployment is interrupted, run the same deploy command
again: Ollama resumes/reuses cached layers and the release is activated only after both
models are ready. Accept the GPU target only after both local Ollama calls succeed
through its tunnel. If GPU acceptance fails, keep legacy CPU inference running, close
the failed GPU tunnel with `Ctrl+C`, and reopen the data-target tunnel with `inference`
selected after switching `STACK_REMOTE_ENV`; do not cut over or run `stop inference`.
Once it succeeds, stop legacy data-host inference with that target's absolute
`STACK_REMOTE_ENV`, while preserving the old model volumes. Those old volumes are not a
supported fallback.

OpenSearch keeps its security plugin enabled. Sign in as `admin` with
`OPENSEARCH_INITIAL_ADMIN_PASSWORD`. Its bundled certificate is a development TLS
certificate, so development clients must explicitly trust it or opt out of certificate
verification; do not carry that exception into production.

Chroma has no built-in authentication in this stack. Its protection is the
SSH tunnel and loopback binding only. Never add a public Chroma firewall rule or change either
binding to a public interface.

Create the first Langfuse account/project at `http://127.0.0.1:3000`. Langfuse API keys
(public and secret) are created in the Langfuse UI after first startup and belong in
each consuming application's local environment, not in this infrastructure repository.

Sign in to pgAdmin with `PGADMIN_DEFAULT_EMAIL=admin@example.com` and the generated
`PGADMIN_DEFAULT_PASSWORD`. Then connect to host `app-postgres`, port `5432`, using the
application database credentials from `.env`; the UI runs inside the remote Compose
network. RedisInsight is preconfigured for `app-redis`. Sign in to the MinIO Console with the generated
`LANGFUSE_MINIO_ROOT_USER` and `LANGFUSE_MINIO_ROOT_PASSWORD` values.

## Operate and remove the stack

Use either local operator surface:

```bash
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/stack.sh status
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/stack.sh logs search
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/stack.sh stop search
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/stack.sh down
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/stack.sh up core vector dynamodb
STACK_REMOTE_ENV=/absolute/path/to/remote.gpu.env ./scripts/stack.sh status
```

```powershell
$env:STACK_REMOTE_ENV = 'C:\absolute\path\to\remote.data.env'
.\scripts\stack.ps1 status
.\scripts\stack.ps1 logs search
.\scripts\stack.ps1 stop search
.\scripts\stack.ps1 down
.\scripts\stack.ps1 up core vector dynamodb
$env:STACK_REMOTE_ENV = 'C:\absolute\path\to\remote.gpu.env'
.\scripts\stack.ps1 status
```

`stop` stops the selected profiles. `down` stops and removes the project's containers
and network, but **down preserves named volumes**. A later `up` or `deploy` reuses their
data.

`destroy` is different: **destroy permanently and irreversibly removes all project named volumes**,
causing permanent data loss. It requires typing the configured remote
target and the exact token `DESTROY-remote-infra-stack` interactively:

```bash
STACK_REMOTE_ENV=/absolute/path/to/remote.data.env ./scripts/stack.sh destroy
```

```powershell
$env:STACK_REMOTE_ENV = 'C:\absolute\path\to\remote.data.env'
.\scripts\stack.ps1 destroy
```

Do not use `destroy` for routine shutdown. There is no automated backup or restore path.
The named volumes are intentionally disposable and this repository provides no backup,
export, restore, or cross-VM migration script. Deleting the VM or its disk deletes the
data; that is an accepted boundary for this personal-development stack.

For ordinary GPU Spot recovery, stop and start only the existing GPU VM in the cloud
provider. When its ephemeral IP changes, edit only `REMOTE_HOST` in `remote.gpu.env`,
reopen the GPU tunnel using its absolute `STACK_REMOTE_ENV` path, and rerun both Ollama
endpoint checks without deploying again. Docker automatically restarts containers and
the existing VM's persistent named volumes retain the model caches. Do not disturb the
data host.

Replacement is separate recovery work: restore or reattach the recovery media that
contains the Docker volumes before treating a replacement VM as recovered. Do not
assume a replacement retains previous model caches. Treat the daily Backup and DR plan
as recovery media for the VM or disk, not an application-level backup guarantee; use
service-specific exports for important data.
