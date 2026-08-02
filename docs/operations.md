# Remote Infra Stack Operations

This runbook covers first setup and routine operation from macOS/Linux Bash and Windows
PowerShell. The stack deploys to an existing VM; it does not create or configure AWS,
GCP, firewall, IAM, or networking resources.

Before starting, provide an SSH-accessible official Ubuntu 22.04, 24.04, or 26.04 LTS
minimal VM on `amd64`. The login user needs passwordless `sudo`; systemd and apt must be
available. Allow inbound SSH from the local machine, but do not open any database, API,
or UI port. Based on the full-stack Ubuntu 26.04 smoke test, 32 GiB is recommended when running all profiles.
A smaller VM may work when fewer profiles are selected; monitor actual workload usage.

The examples deliberately select all profiles. `tools` requires `core`.
The local Bash workflow also requires OpenSSL because `init-env.sh` generates secrets
before `check.sh` can validate the repository.

## macOS/Linux (Bash)

Run these steps from a local terminal in this order.

### 1. Clone and create local configuration

```bash
git clone https://github.com/somanshusingla/remote-infra-stack.git
cd remote-infra-stack
./scripts/init-env.sh
cp remote.env.example remote.env
${EDITOR:-vi} remote.env
```

Set `REMOTE_HOST` to the existing VM's DNS name, IP address, or SSH-config alias. Set
`REMOTE_USER`, `REMOTE_PORT`, and `REMOTE_IDENTITY_FILE` if the SSH configuration does
not already provide them. Leave `REMOTE_ROOT` as a relative path. The generated `.env`
and copied `remote.env` are ignored secrets/configuration and must remain untracked.

### 2. Check the selected profiles

```bash
./scripts/check.sh core vector search observability tools
```

The check is local and non-mutating. It verifies configuration, dependencies, syntax,
and the clean committed `HEAD`. If Docker Compose is installed locally, it also renders
the configuration without starting containers or pulling images.

### 3. Bootstrap the existing VM

```bash
./scripts/bootstrap.sh
```

Bootstrap installs Docker Engine and Compose, enables Docker, and configures the
OpenSearch kernel setting. It is idempotent. Re-run it after rebuilding the VM or when
you need to repair prerequisites.

### 4. Deploy the selected profiles

```bash
./scripts/deploy.sh core vector search observability tools
```

Deployment uploads the clean committed Git release and `.env` separately, starts the
selected profiles, waits for health checks, and only then activates the release.

### 5. Start the SSH tunnel

```bash
./scripts/tunnel.sh core vector search observability tools
```

Keep this terminal open. The command blocks while forwarding only the selected
profiles. Use `Ctrl+C` to close the tunnel; that does not stop remote services.

### 6. Configure and use local applications

Open another terminal or your application's local environment and use loopback
endpoints:

```dotenv
DATABASE_URL=postgresql://app:<password>@127.0.0.1:5432/app
REDIS_URL=redis://:<password>@127.0.0.1:6379/0
CHROMA_HOST=127.0.0.1
CHROMA_PORT=18000
OPENSEARCH_URL=https://127.0.0.1:9200
LANGFUSE_BASE_URL=http://127.0.0.1:3000
```

Use the generated values from `.env` for application database passwords and the
OpenSearch `admin` login. Create Langfuse project API keys in the Langfuse UI after its
first startup, then store those keys only in the consuming application's environment.

### 7. Inspect, stop, and restart

```bash
./scripts/stack.sh status
./scripts/stack.sh logs search
./scripts/stack.sh stop search
./scripts/stack.sh down
./scripts/stack.sh up core vector search observability tools
```

`logs` follows one profile or service until interrupted. `stop` affects only the named
profiles. `down` affects the complete Compose project but preserves all named volumes.
`up` starts selected profiles from the current successful release.

### 8. Destroy only when data loss is intentional

```bash
./scripts/stack.sh destroy
```

The command asks for the configured remote target and then the exact token
`DESTROY-remote-infra-stack`. Do not run `destroy` as a routine cleanup command.

## Windows (PowerShell)

Run these steps from Windows PowerShell 5.1+ or PowerShell 7+ in this order.

### 1. Clone and create local configuration

```powershell
git clone https://github.com/somanshusingla/remote-infra-stack.git
Set-Location remote-infra-stack
.\scripts\init-env.ps1
Copy-Item .\remote.env.example .\remote.env
notepad .\remote.env
```

Set `REMOTE_HOST` to the existing VM's DNS name, IP address, or SSH-config alias. Set
`REMOTE_USER`, `REMOTE_PORT`, and `REMOTE_IDENTITY_FILE` if the SSH configuration does
not already provide them. Leave `REMOTE_ROOT` as a relative path. The generated `.env`
and copied `remote.env` are ignored secrets/configuration and must remain untracked.

### 2. Check the selected profiles

```powershell
.\scripts\check.ps1 core vector search observability tools
```

The check is local and non-mutating. It verifies configuration, dependencies,
PowerShell syntax, and the clean committed `HEAD`. If Docker Compose is installed
locally, it also renders the configuration without starting containers or pulling
images.

### 3. Bootstrap the existing VM

```powershell
.\scripts\bootstrap.ps1
```

Bootstrap installs Docker Engine and Compose, enables Docker, and configures the
OpenSearch kernel setting. It is idempotent. Re-run it after rebuilding the VM or when
you need to repair prerequisites.

### 4. Deploy the selected profiles

```powershell
.\scripts\deploy.ps1 core vector search observability tools
```

Deployment uploads the clean committed Git release and `.env` separately, starts the
selected profiles, waits for health checks, and only then activates the release.

### 5. Start the SSH tunnel

```powershell
.\scripts\tunnel.ps1 core vector search observability tools
```

Keep this PowerShell window open. The command blocks while forwarding only the selected
profiles. Use `Ctrl+C` to close the tunnel; that does not stop remote services.

### 6. Configure and use local applications

Open another PowerShell window or your application's local environment and use
loopback endpoints:

```dotenv
DATABASE_URL=postgresql://app:<password>@127.0.0.1:5432/app
REDIS_URL=redis://:<password>@127.0.0.1:6379/0
CHROMA_HOST=127.0.0.1
CHROMA_PORT=18000
OPENSEARCH_URL=https://127.0.0.1:9200
LANGFUSE_BASE_URL=http://127.0.0.1:3000
```

Use the generated values from `.env` for application database passwords and the
OpenSearch `admin` login. Create Langfuse project API keys in the Langfuse UI after its
first startup, then store those keys only in the consuming application's environment.

### 7. Inspect, stop, and restart

```powershell
.\scripts\stack.ps1 status
.\scripts\stack.ps1 logs search
.\scripts\stack.ps1 stop search
.\scripts\stack.ps1 down
.\scripts\stack.ps1 up core vector search observability tools
```

`logs` follows one profile or service until interrupted. `stop` affects only the named
profiles. `down` affects the complete Compose project but preserves all named volumes.
`up` starts selected profiles from the current successful release.

### 8. Destroy only when data loss is intentional

```powershell
.\scripts\stack.ps1 destroy
```

The command asks for the configured remote target and then the exact token
`DESTROY-remote-infra-stack`. Do not run `destroy` as a routine cleanup command.

## Endpoint and UI reference

The defaults below exist only while the matching SSH tunnel is running.

| Profile | Component | Local address | Authentication/access note |
| --- | --- | --- | --- |
| `core` | Application PostgreSQL | `127.0.0.1:5432` | User/database `app`; password from `.env` |
| `core` | Application Redis | `127.0.0.1:6379` | Password from `.env` |
| `vector` | Chroma API | `http://127.0.0.1:18000` | No official bundled UI or built-in authentication |
| `search` | OpenSearch API | `https://127.0.0.1:9200` | User `admin`; password from `.env`; development certificate |
| `search` | OpenSearch Dashboards | `http://127.0.0.1:5601` | ELK-style UI; OpenSearch admin login |
| `observability` | Langfuse | `http://127.0.0.1:3000` | Create account/project and API keys in UI |
| `observability` | MinIO API | `http://127.0.0.1:9090` | S3-compatible endpoint |
| `observability` | MinIO Console | `http://127.0.0.1:9091` | Generated MinIO root credentials |
| `tools` | pgAdmin | `http://127.0.0.1:5050` | Email `admin@example.com`, generated password; database host `app-postgres` |
| `tools` | RedisInsight | `http://127.0.0.1:5540` | Preconfigured for `app-redis` |

OpenSearch's security plugin remains enabled, but the bundled TLS certificate is for
development. Explicitly trust it or disable verification only in development clients.
Chroma has no authentication layer; SSH and the VM/local loopback bindings are its only
protection. Never publish Chroma or any other service through a public cloud firewall.

## Data lifecycle and recovery boundary

Persistent services use stable named Docker volumes. Routine deployment, `stop`,
release pruning, and `down` preserve those named volumes. Code releases can therefore
change without intentionally resetting service data.

The data is disposable: no backup/export automation is provided, and there is no
automated restore or cross-VM migration workflow. Back up anything important using a
service-specific process outside this repository before making destructive changes.

`destroy` causes irreversible permanent data loss by removing project volumes. Deleting
or rebuilding the VM can have the same result. Do not run `destroy` as a
routine cleanup command; use `down` when the goal is to stop the stack while retaining
data.

## Safe smoke-test sequence

For a newly prepared VM, validate in stages so failures stay attributable:

1. Deploy `core vector`, open that tunnel, and verify PostgreSQL, authenticated Redis,
   and the Chroma heartbeat from the local machine.
2. Deploy `search`, reopen/add the `search` tunnel, then verify the authenticated
   OpenSearch API and OpenSearch Dashboards.
3. Deploy `core observability tools`, reopen/add those tunnels, then verify Langfuse,
   the MinIO Console, pgAdmin, and RedisInsight.
4. Run `status`, then `down`, then `up core vector search observability tools` and
   repeat endpoint checks.

Do not run `destroy` during a smoke test. Keep live command output and any secret-bearing
configuration out of Git.
