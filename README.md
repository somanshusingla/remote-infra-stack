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
| `vector` | Chroma | Vector database API |
| `search` | OpenSearch, OpenSearch Dashboards | Search API and ELK-style browser UI |
| `observability` | Langfuse web/worker, dedicated PostgreSQL and Redis, ClickHouse, MinIO | Isolated tracing stack |
| `tools` | pgAdmin, RedisInsight | Administration UIs for `core`; always select `core` with `tools` |

Application PostgreSQL and Redis are independent from the private PostgreSQL and Redis
used by Langfuse.

## Requirements and supported hosts

Run the local scripts from the repository root. The local machine needs Git, OpenSSH
(`ssh` and `scp`), and either:

- Bash on macOS/Linux, with the standard utilities checked by `./scripts/check.sh`; or
- Windows PowerShell 5.1+ or PowerShell 7+, using the `.ps1` scripts.

The project does not provision cloud resources. Supply an existing SSH-accessible
Ubuntu VM in AWS, GCP, or another provider with:

- Ubuntu 22.04, Ubuntu 24.04, or Ubuntu 26.04 LTS on `amd64`;
- systemd, apt, and passwordless `sudo` for the SSH user; and
- a direct OpenSSH/SCP route from the local machine. Only the SSH route needs a cloud
  firewall rule.

Ubuntu 26.04 bootstrap and pinned `linux/amd64` image manifests were verified on a
minimal GCP VM; the sanitized evidence is in
[docs/verification/task-7-ubuntu-bootstrap.md](docs/verification/task-7-ubuntu-bootstrap.md).
Support for a future Ubuntu LTS is capability-gated, not promised by version number:
the bootstrap proceeds only when Docker's official Docker apt repository exists for
the detected codename and all required packages are available. Container publishers
must also support that release.

## Quick start

The examples select every profile. For a smaller stack, pass only the required
profiles. `tools` requires `core`, and duplicate or unknown profiles are rejected.

### macOS/Linux Bash

```bash
git clone https://github.com/somanshusingla/remote-infra-stack.git
cd remote-infra-stack

./scripts/init-env.sh
cp remote.env.example remote.env
${EDITOR:-vi} remote.env

./scripts/check.sh core vector search observability tools
./scripts/bootstrap.sh
./scripts/deploy.sh core vector search observability tools
./scripts/tunnel.sh core vector search observability tools
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

.\scripts\check.ps1 core vector search observability tools
.\scripts\bootstrap.ps1
.\scripts\deploy.ps1 core vector search observability tools
.\scripts\tunnel.ps1 core vector search observability tools
```

Keep this PowerShell window open for the tunnel and use another window for local
applications and lifecycle commands. `Ctrl+C` closes the tunnel without stopping the
remote services.

### Configuration files

`init-env` generates an ignored `.env` containing service credentials. It refuses to
overwrite an existing file unless `--force` (Bash) or `-Force` (PowerShell) is given.
Do not commit or share this file.

Copy `remote.env.example` to the ignored `remote.env` and edit at least the SSH target:

```dotenv
REMOTE_HOST=remote-infra-stack
REMOTE_USER=
REMOTE_PORT=22
REMOTE_IDENTITY_FILE=
REMOTE_ROOT=remote-infra-stack
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
| `vector` | Chroma | `http://127.0.0.1:18000` | HTTP API/SDK; no bundled official UI |
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
./scripts/stack.sh up core vector search observability tools
```

```powershell
.\scripts\stack.ps1 status
.\scripts\stack.ps1 logs search
.\scripts\stack.ps1 stop search
.\scripts\stack.ps1 down
.\scripts\stack.ps1 up core vector search observability tools
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
