# Remote Infra Stack

Remote Infra Stack runs a profile-selected set of development databases and UIs on one
existing SSH-accessible Ubuntu VM. Applications and browsers stay on your Windows,
macOS, or Linux machine and connect through profile-scoped SSH tunnels; service ports
are never published on the VM's public interfaces.

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
| `inference` | Two isolated Ollama servers | CPU chat and embedding inference |

Application PostgreSQL and Redis are independent from the private PostgreSQL and Redis
used by Langfuse.

## Requirements and supported hosts

Run the local scripts from the repository root. The local machine needs Git, OpenSSH
(`ssh` and `scp`), and either:

- Bash and OpenSSL on macOS/Linux, with the remaining standard utilities checked by
  `./scripts/check.sh`; or
- Windows PowerShell 5.1+ or PowerShell 7+, using the `.ps1` scripts.

The project does not provision cloud resources. Supply an existing SSH-accessible
Ubuntu VM in AWS, GCP, or another provider with:

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
require it. The CPU-only Ollama services can take minutes to answer on general-purpose
cloud CPUs, especially while `gemma4:e4b` is loading.

`net.ipv4.ip_forward=1` is a host-global IPv4 routing capability, not a container-only
setting. Stack ports remain loopback-only behind an SSH-only cloud firewall. On a
multi-NIC host or one with a custom host firewall, routing and firewall policy are the
operator responsibility.

Ubuntu 26.04 bootstrap and pinned `linux/amd64` image manifests were verified on a
minimal GCP VM; the sanitized evidence is in
[docs/verification/task-7-ubuntu-bootstrap.md](docs/verification/task-7-ubuntu-bootstrap.md).
Support for a future Ubuntu LTS is capability-gated, not promised by version number:
the bootstrap proceeds only when Docker's official Docker apt repository exists for
the detected codename and all required packages are available. Container publishers
must also support that release.

## Quick start

The examples select the usual personal-development set: `core`, `vector`, `dynamodb`,
and `inference`. Add `search`, `observability`, or `tools` when needed. `tools` requires
`core`, and duplicate or unknown profiles are rejected.

### macOS/Linux Bash

```bash
git clone https://github.com/somanshusingla/remote-infra-stack.git
cd remote-infra-stack

./scripts/init-env.sh
cp remote.env.example remote.env
${EDITOR:-vi} remote.env

./scripts/check.sh core vector dynamodb inference
./scripts/bootstrap.sh
./scripts/deploy.sh core vector dynamodb inference
./scripts/tunnel.sh core vector dynamodb inference
```

Keep the tunnel command running while local clients use the endpoints. It occupies the
terminal; open another terminal for application and lifecycle commands.

### Windows PowerShell

```powershell
git clone https://github.com/somanshusingla/remote-infra-stack.git
Set-Location remote-infra-stack

.\scripts\init-env.ps1
Copy-Item .\remote.env.example .\remote.env
notepad .\remote.env

.\scripts\check.ps1 core vector dynamodb inference
.\scripts\bootstrap.ps1
.\scripts\deploy.ps1 core vector dynamodb inference
.\scripts\tunnel.ps1 core vector dynamodb inference
```

Keep this PowerShell window open for the tunnel and use another window for local
applications and lifecycle commands. `Ctrl+C` closes the tunnel without stopping the
remote services.

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
OLLAMA_KEEP_ALIVE=5m
```

Copy `remote.env.example` to the ignored `remote.env` and edit at least the SSH target:

```dotenv
REMOTE_HOST=remote-infra-stack
REMOTE_USER=
REMOTE_PORT=22
REMOTE_IDENTITY_FILE=
REMOTE_ROOT=remote-infra-stack
```

For an existing ignored `remote.env`, manually add these five tunnel settings instead
of replacing the file:

```dotenv
LOCAL_CHROMA_ADMIN_PORT=18001
LOCAL_DYNAMODB_PORT=18002
LOCAL_DYNAMODB_ADMIN_PORT=18003
LOCAL_OLLAMA_LLM_PORT=11440
LOCAL_OLLAMA_EMBEDDING_PORT=11441
```

`REMOTE_HOST` may be a DNS name, IP address, or local SSH-config alias. Set
`REMOTE_USER` and `REMOTE_IDENTITY_FILE` when they are not already supplied by the SSH
configuration. Keep `REMOTE_ROOT` relative to the remote user's home. The remaining
`LOCAL_*_PORT` values in `remote.env.example` control the local side of each tunnel.

`check` validates both ignored files, the selected profiles, required local commands,
script syntax, a clean committed Git `HEAD`, and the Compose model when local Docker
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

Override local ports in `remote.env` if a default is occupied. The tunnel refuses
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
models are ready.

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
./scripts/stack.sh status
./scripts/stack.sh logs search
./scripts/stack.sh stop search
./scripts/stack.sh down
./scripts/stack.sh up core vector dynamodb inference
```

```powershell
.\scripts\stack.ps1 status
.\scripts\stack.ps1 logs search
.\scripts\stack.ps1 stop search
.\scripts\stack.ps1 down
.\scripts\stack.ps1 up core vector dynamodb inference
```

`stop` stops the selected profiles. `down` stops and removes the project's containers
and network, but **down preserves named volumes**. A later `up` or `deploy` reuses their
data.

`destroy` is different: **destroy permanently and irreversibly removes all project named volumes**,
causing permanent data loss. It requires typing the configured remote
target and the exact token `DESTROY-remote-infra-stack` interactively:

```bash
./scripts/stack.sh destroy
```

```powershell
.\scripts\stack.ps1 destroy
```

Do not use `destroy` for routine shutdown. There is no automated backup or restore path.
The named volumes are intentionally disposable and this repository provides no backup,
export, restore, or cross-VM migration script. Deleting the VM or its disk deletes the
data; that is an accepted boundary for this personal-development stack.
