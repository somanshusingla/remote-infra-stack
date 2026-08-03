# Remote Infra Stack Operations

This runbook covers first setup and routine operation from macOS/Linux Bash and Windows
PowerShell. The stack deploys to an existing VM; it does not create or configure AWS,
GCP, firewall, IAM, or networking resources.

Before starting, provide an SSH-accessible official Ubuntu 22.04, 24.04, or 26.04 LTS
minimal VM on `amd64`. The login user needs passwordless `sudo`; systemd and apt must be
available. Allow inbound SSH from the local machine, but do not open any database, API,
or UI port. 32 GiB does not guarantee that all profiles fit at peak. Select only
the profiles needed for the current project, monitor actual usage, and resize the VM or
raise the documented container limits when the workload requires it.

The examples select `core vector dynamodb inference`. Add `search`, `observability`, or
`tools` as needed; `tools` requires `core`.
The local Bash workflow also requires OpenSSL because `init-env.sh` generates secrets
before `check.sh` can validate the repository.

## Upgrade existing ignored configuration

The ignored `.env` carries generated credentials, while `remote.env` carries the SSH
target and local tunnel choices. Preserve both files across repository upgrades. Do not
run `init-env --force` or `init-env.ps1 -Force` during an upgrade because that replaces
the existing `.env` instead of merging new defaults.

Manually append these seven non-secret keys to an existing `.env` when absent:

```dotenv
CHROMA_ADMIN_MEMORY=512m
DYNAMODB_MEMORY=1g
DYNAMODB_ADMIN_MEMORY=512m
OLLAMA_LLM_MEMORY=14g
OLLAMA_EMBEDDING_MEMORY=2g
OLLAMA_CONTEXT_LENGTH=8192
OLLAMA_KEEP_ALIVE=5m
```

Manually append these five keys to an existing `remote.env` when absent:

```dotenv
LOCAL_CHROMA_ADMIN_PORT=18001
LOCAL_DYNAMODB_PORT=18002
LOCAL_DYNAMODB_ADMIN_PORT=18003
LOCAL_OLLAMA_LLM_PORT=11440
LOCAL_OLLAMA_EMBEDDING_PORT=11441
```

Set `REMOTE_HOST` only in the ignored `remote.env`. Use a DNS name, SSH-config alias, or
an address refreshed outside this repository; do not hardcode a cloud VM IP or zone in
Compose or the committed scripts.

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
./scripts/check.sh core vector dynamodb inference
```

The check is local and non-mutating. It verifies configuration, dependencies, syntax,
and the clean committed `HEAD`. If Docker Compose is installed locally, it also renders
the configuration without starting containers or pulling images.

### 3. Bootstrap the existing VM

```bash
./scripts/bootstrap.sh
```

Bootstrap installs Docker Engine and Compose, enables Docker, and persists both
`vm.max_map_count=262144` for OpenSearch and `net.ipv4.ip_forward=1` for Docker bridge
egress. It applies and verifies both settings. It is idempotent. Re-run it after
rebuilding the VM or when you need to repair prerequisites. Deployment preflight
refuses to start services while IPv4 forwarding is disabled.

Kernel IPv4 forwarding enables Docker bridge egress; it does not publish the stack's
ports. The repository leaves iptables/UFW, Docker daemon networking, and cloud
IP-forwarding settings unchanged. Keep every Compose publication on `127.0.0.1` and
allow only SSH through the cloud firewall.

### 4. Deploy the selected profiles

```bash
./scripts/deploy.sh core vector dynamodb inference
```

Deployment uploads the clean committed Git release and `.env` separately, starts the
selected profiles, waits for health checks, and only then activates the release.
On the first `inference` deployment this wait includes downloading and verifying
`gemma4:e4b` and `embeddinggemma:300m`; it can take a long time on a CPU VM. If the pull
or deployment is interrupted, run the same deploy command again. Each Ollama container
resumes or reuses the partial model layers in its own named volume.

### 5. Start the SSH tunnel

```bash
./scripts/tunnel.sh core vector dynamodb inference
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

For Chroma Admin, browse to `http://127.0.0.1:18001` and enter the one-time internal
Compose address `http://chroma:8000`. Local SDKs still use the tunneled Chroma API at
`http://127.0.0.1:18000`.

DynamoDB Local uses non-secret dummy credentials:

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

Call the two isolated Ollama APIs independently:

```bash
curl http://127.0.0.1:11440/api/chat \
  -d '{"model":"gemma4:e4b","messages":[{"role":"user","content":"Hello"}],"stream":false}'

curl http://127.0.0.1:11441/api/embed \
  -d '{"model":"embeddinggemma:300m","input":"hello from the remote stack"}'
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
./scripts/stack.sh up core vector dynamodb inference
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
.\scripts\check.ps1 core vector dynamodb inference
```

The check is local and non-mutating. It verifies configuration, dependencies,
PowerShell syntax, and the clean committed `HEAD`. If Docker Compose is installed
locally, it also renders the configuration without starting containers or pulling
images.

### 3. Bootstrap the existing VM

```powershell
.\scripts\bootstrap.ps1
```

Bootstrap installs Docker Engine and Compose, enables Docker, and persists both
`vm.max_map_count=262144` for OpenSearch and `net.ipv4.ip_forward=1` for Docker bridge
egress. It applies and verifies both settings. It is idempotent. Re-run it after
rebuilding the VM or when you need to repair prerequisites. Deployment preflight
refuses to start services while IPv4 forwarding is disabled.

Kernel IPv4 forwarding enables Docker bridge egress; it does not publish the stack's
ports. The repository leaves iptables/UFW, Docker daemon networking, and cloud
IP-forwarding settings unchanged. Keep every Compose publication on `127.0.0.1` and
allow only SSH through the cloud firewall.

### 4. Deploy the selected profiles

```powershell
.\scripts\deploy.ps1 core vector dynamodb inference
```

Deployment uploads the clean committed Git release and `.env` separately, starts the
selected profiles, waits for health checks, and only then activates the release.
On first use, the wait includes both model downloads. If it is interrupted, run this
same deployment command again; the named Ollama caches preserve reusable layers.

### 5. Start the SSH tunnel

```powershell
.\scripts\tunnel.ps1 core vector dynamodb inference
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

Open `http://127.0.0.1:18001` for Chroma Admin and enter
`http://chroma:8000` as its internal connection URL. DynamoDB and Ollama clients use
the same loopback addresses as the Bash workflow. Invoke the two Ollama APIs natively
from PowerShell as follows:

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

Use the generated values from `.env` for application database passwords and the
OpenSearch `admin` login. Create Langfuse project API keys in the Langfuse UI after its
first startup, then store those keys only in the consuming application's environment.

### 7. Inspect, stop, and restart

```powershell
.\scripts\stack.ps1 status
.\scripts\stack.ps1 logs search
.\scripts\stack.ps1 stop search
.\scripts\stack.ps1 down
.\scripts\stack.ps1 up core vector dynamodb inference
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
| `vector` | Chroma API | `http://127.0.0.1:18000` | No built-in authentication |
| `vector` | `chroma-admin` | `http://127.0.0.1:18001` | Unofficial UI; use `http://chroma:8000` internally |
| `dynamodb` | DynamoDB Local | `http://127.0.0.1:18002` | API with dummy local credentials |
| `dynamodb` | `dynamodb-admin` | `http://127.0.0.1:18003` | Browser UI |
| `inference` | Ollama chat | `http://127.0.0.1:11440` | `gemma4:e4b` API |
| `inference` | Ollama embeddings | `http://127.0.0.1:11441` | `embeddinggemma:300m` API |
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
protection. Never change host-side `127.0.0.1` publications or tunnel listeners to
`0.0.0.0`, and never publish Chroma or any other service through a public cloud
firewall. The `dynamodb-admin` container's internal listener does not make its
host-published port public.

## Capacity and first-pull behavior

The release root must have at least 10 GiB free; below 20 GiB produces a warning.
Selecting `inference` also requires at least 20 GiB free on Docker's storage
filesystem. Model images and caches can therefore need more disk than the Git release
archive itself.

Ollama is CPU-only in this stack. `gemma4:e4b` has a 14 GiB container memory limit and
`embeddinggemma:300m` has a 2 GiB limit; these are ceilings rather than reservations.
First-token and embedding latency can be high on general-purpose CPUs, and loading the
chat model adds another delay. The remote preflight warns when the selected limits plus
2 GiB host overhead exceed host memory, but it does not reject the deployment. Select
fewer profiles, increase `OLLAMA_LLM_MEMORY` or another service's limit only when
measurement justifies it, and resize the VM when the selected peak workload does not
fit.

## Data lifecycle and recovery boundary

Persistent services use stable named Docker volumes. Routine deployment, `stop`,
release pruning, and `down` preserve those named volumes. Code releases can therefore
change without intentionally resetting service data.

The data is disposable: no backup/export automation or script is provided, and there
is no automated restore or cross-VM migration workflow. Closing/deleting the VM or its
disk may delete every named volume; that is an accepted boundary for this personal
stack. Back up anything important using a service-specific process outside this
repository before making destructive changes.

`destroy` causes irreversible permanent data loss by removing project volumes. Deleting
or rebuilding the VM can have the same result. Do not run `destroy` as a
routine cleanup command; use `down` when the goal is to stop the stack while retaining
data.

## Safe smoke-test sequence

For a newly prepared VM, validate in stages so failures stay attributable:

1. Deploy `core vector`, open that tunnel, and verify PostgreSQL, authenticated Redis,
   the Chroma heartbeat, and `chroma-admin` using `http://chroma:8000`.
2. Deploy `dynamodb`, reopen/add that tunnel, then verify the DynamoDB SDK example and
   `dynamodb-admin` UI with `AWS_ACCESS_KEY_ID=local` and its matching dummy secret.
3. Deploy `inference`, wait for both model pulls, reopen/add that tunnel, then run the
   Ollama chat and embed examples. Re-run deploy to verify cached model reuse.
4. Deploy `search`, reopen/add the `search` tunnel, then verify the authenticated
   OpenSearch API and OpenSearch Dashboards.
5. Deploy `core observability tools`, reopen/add those tunnels, then verify Langfuse,
   the MinIO Console, pgAdmin, and RedisInsight.
6. Run `status`, then `down`, then
   `up core vector search observability tools dynamodb inference` and
   repeat endpoint checks.

Do not run `destroy` during a smoke test. Keep live command output and any secret-bearing
configuration out of Git.
