# Remote Infra Stack Design

Status: approved on 2026-08-02

## Purpose

`remote-infra-stack` is a personal, single-VM development infrastructure repository. It runs reusable stateful services on an SSH-accessible Ubuntu LTS VM while applications, IDEs, database clients, and browsers remain on a local Windows, macOS, or Linux machine.

The Compose model is cloud-neutral. GCP, AWS, and other providers are responsible only for supplying an existing Ubuntu VM and an SSH route to it.

## Goals

- Deploy PostgreSQL, Redis, Chroma, OpenSearch, and Langfuse from one repository-owned Compose model.
- Start only requested groups through Compose profiles.
- Make remote APIs and web interfaces available as local loopback endpoints through SSH tunnels.
- Support equivalent PowerShell and Bash operator commands.
- Deploy committed, versioned release archives over SSH/SCP without requiring Git credentials on the VM.
- Keep service secrets outside Git and upload them separately.
- Preserve data across container and release replacement with named Docker volumes.
- Run on Ubuntu LTS minimal cloud images, including Ubuntu 22.04, 24.04, and 26.04 on `amd64`.
- Remain forward-compatible with future Ubuntu LTS releases when Docker publishes a compatible apt repository.

## Non-goals

- Production hardening, high availability, zero-downtime deployment, or horizontal scaling.
- VM, firewall, IAM, DNS, TLS certificate, or cloud-network provisioning.
- Public service endpoints or reverse-proxy configuration.
- Automated backups, restores, or cross-VM data migration.
- Kubernetes, Docker Swarm, or managed service deployments.
- Guaranteed ARM64 support in v1.
- Installing or running Codex on the remote VM.

The data is disposable. Named volumes survive ordinary Compose operations, but deleting the VM or explicitly destroying volumes may permanently remove all data.

## Compose Architecture

The repository owns a single `compose.yaml` and declares a stable project name:

```yaml
name: remote-infra-stack
```

No service starts without an explicitly selected profile.

| Profile | Services | Purpose |
| --- | --- | --- |
| `core` | `app-postgres`, `app-redis` | General-purpose application databases |
| `vector` | `chroma` | Vector database API |
| `search` | `opensearch`, `opensearch-dashboards` | Search API and browser UI |
| `observability` | `langfuse-web`, `langfuse-worker`, `langfuse-postgres`, `langfuse-redis`, `clickhouse`, `minio` | Complete isolated Langfuse deployment |
| `tools` | `pgadmin`, `redisinsight` | Optional administration UIs for the `core` services |

The `tools` profile is selected together with `core`. Wrapper validation rejects `tools` without `core`. Langfuse has dedicated PostgreSQL and Redis services so application data and Langfuse lifecycle remain independent.

All containers share a private Compose bridge network and use service DNS names. Langfuse's PostgreSQL, Redis, and ClickHouse services are not published to the host.

## Images and Versions

All image references are parameterized by a committed `versions.env`. Exact release tags are required and `latest` is forbidden. Where practical, image digests may be added after the initial tags have been verified.

The repository-owned Langfuse definition is derived from the upstream Compose topology but is maintained locally so port bindings, secrets, health checks, resource limits, and image versions cannot change unexpectedly.

## Ports and Local Access

Every published port binds explicitly to `127.0.0.1` on the VM. The SSH process exposes selected endpoints on the local machine.

| Service | Container port | VM loopback port | Local endpoint |
| --- | ---: | ---: | --- |
| Application PostgreSQL | 5432 | 15432 | `127.0.0.1:5432` |
| Application Redis | 6379 | 16379 | `127.0.0.1:6379` |
| Chroma | 8000 | 18000 | `http://127.0.0.1:18000` |
| OpenSearch | 9200 | 9200 | `https://127.0.0.1:9200` |
| OpenSearch Dashboards | 5601 | 5601 | `http://127.0.0.1:5601` |
| Langfuse | 3000 | 3000 | `http://127.0.0.1:3000` |
| pgAdmin | 5050 | 5050 | `http://127.0.0.1:5050` |
| RedisInsight | 5540 | 5540 | `http://127.0.0.1:5540` |
| MinIO API | 9000 | 9090 | `http://127.0.0.1:9090` |
| MinIO Console | 9001 | 9091 | `http://127.0.0.1:9091` |

Self-hosted Chroma does not include a bundled official UI. It is accessed through its SDK and HTTP API.

The tunnel command enables mappings only for selected profiles and uses `ExitOnForwardFailure`, keepalive intervals, and keepalive failure limits. It exits before connecting if a requested local port is already occupied.

## Local Configuration

The local checkout contains four configuration files with separate responsibilities:

| File | Git status | Uploaded | Purpose |
| --- | --- | --- | --- |
| `versions.env` | committed | in release archive | Exact container image versions |
| `.env.example` | committed | in release archive | Documented runtime secret template |
| `.env` | ignored | separately to `runtime/.env` | Service credentials |
| `remote.env.example` | committed | no | SSH target template |
| `remote.env` | ignored | no | SSH host alias, remote root, and local overrides |

The required `.env` contract is:

```dotenv
APP_POSTGRES_USER=app
APP_POSTGRES_DB=app
APP_POSTGRES_PASSWORD=<generated-password>
APP_REDIS_PASSWORD=<generated-password>

OPENSEARCH_INITIAL_ADMIN_PASSWORD=<generated-strong-password>

LANGFUSE_POSTGRES_USER=langfuse
LANGFUSE_POSTGRES_DB=langfuse
LANGFUSE_POSTGRES_PASSWORD=<generated-password>
LANGFUSE_REDIS_PASSWORD=<generated-password>
LANGFUSE_CLICKHOUSE_USER=clickhouse
LANGFUSE_CLICKHOUSE_PASSWORD=<generated-password>
LANGFUSE_MINIO_ROOT_USER=langfuse
LANGFUSE_MINIO_ROOT_PASSWORD=<generated-password>
LANGFUSE_SALT=<generated-secret>
LANGFUSE_ENCRYPTION_KEY=<generated-64-character-hex-key>
LANGFUSE_NEXTAUTH_SECRET=<generated-secret>

PGADMIN_DEFAULT_EMAIL=admin@example.local
PGADMIN_DEFAULT_PASSWORD=<generated-password>
REDISINSIGHT_ENCRYPTION_KEY=<generated-secret>
```

`init-env.sh` and `init-env.ps1` generate Compose-safe random credentials. Validation rejects missing values and template placeholders. The remote copy uses file mode `0600`. Secrets do not appear in release archives, command arguments, or normal script output.

Langfuse project public and secret keys are created later through the Langfuse UI and belong in each consuming application's local environment, not this infrastructure repository.

## Security Model

- The VM exposes only its SSH route; no database, API, or UI port requires a public firewall rule.
- Chroma relies on the SSH access boundary because its current self-hosted server does not provide built-in authentication.
- PostgreSQL uses a dedicated application role, database, and password.
- Redis requires a password and uses the `noeviction` memory policy.
- OpenSearch keeps its security plugin enabled and requires a strong initial administrator password.
- Langfuse receives separate datastore credentials, salt, encryption key, and session secret.
- pgAdmin requires a login, and RedisInsight receives an encryption key for stored connection details.
- SSH keys are referenced through the user's SSH configuration and never copied into the repository.
- Users with Docker daemon access can inspect container environment variables. This is accepted for the single-user personal VM.
- SSH provides transport encryption. Public TLS termination is outside scope.

## Repository Layout

```text
remote-infra-stack/
├── compose.yaml
├── versions.env
├── .env.example
├── remote.env.example
├── .gitignore
├── README.md
├── config/
│   ├── opensearch/
│   └── redis/
├── scripts/
│   ├── init-env.sh
│   ├── init-env.ps1
│   ├── bootstrap.sh
│   ├── bootstrap.ps1
│   ├── deploy.sh
│   ├── deploy.ps1
│   ├── stack.sh
│   ├── stack.ps1
│   ├── tunnel.sh
│   ├── tunnel.ps1
│   └── remote/
│       ├── bootstrap-host.sh
│       ├── deploy-release.sh
│       ├── stack.sh
│       └── health.sh
├── tests/
└── docs/
    └── superpowers/specs/
```

PowerShell and Bash expose equivalent user operations but delegate deployment behavior to the same remote Bash scripts. This prevents the two local interfaces from developing different remote behavior.

## Deployment Lifecycle

Deployment targets an existing SSH-accessible VM and requires a clean, committed Git revision.

1. Read and validate `remote.env`, `.env`, requested profiles, and local dependencies.
2. Verify SSH connectivity and remote prerequisites.
3. Create a compressed archive from Git `HEAD`, named with a UTC timestamp and short commit SHA.
4. Compute a SHA-256 checksum.
5. Upload the archive and checksum to the remote `incoming` directory.
6. Upload `.env` separately to the remote runtime directory and apply mode `0600`.
7. Acquire a remote deployment lock.
8. Verify the checksum and extract the archive into a new release directory.
9. Run Compose interpolation and configuration validation.
10. Pull pinned images and run selected profiles with `docker compose up -d --wait`.
11. Run service-specific health verification.
12. Update the `current` symlink only after success.
13. Retain the latest three successful code releases.

Remote layout:

```text
~/remote-infra-stack/
├── current -> releases/<timestamp>-<git-sha>
├── incoming/
├── releases/
└── runtime/
    └── .env
```

Named Docker volumes and the stable Compose project name keep data independent from code release paths.

If deployment fails, the command exits nonzero, leaves the failed release for inspection, does not change `current`, does not prune successful releases, and never deletes volumes. This is a development convenience rather than a database-safe rollback guarantee because a newly started application may already have run datastore migrations.

## Ubuntu Bootstrap and Forward Compatibility

The bootstrap is idempotent and supports official Ubuntu minimal cloud images. It does not run `unminimize`.

It installs explicit prerequisites and Docker packages:

```text
ca-certificates curl gnupg tar gzip openssl util-linux coreutils jq
docker-ce docker-ce-cli containerd.io docker-buildx-plugin
docker-compose-plugin
```

The bootstrap:

- Requires `ID=ubuntu`, `amd64`, systemd, apt, SSH, and usable `sudo` access.
- Explicitly supports Ubuntu 22.04, 24.04, and 26.04 LTS.
- Does not hardcode an upper Ubuntu version limit.
- Reads `UBUNTU_CODENAME`, falling back to `VERSION_CODENAME`.
- Probes Docker's official apt repository for the detected suite before changing package sources.
- Proceeds on a future Ubuntu LTS only when the required repository and packages are available.
- Fails safely with the detected release, codename, and remediation guidance when Docker has not yet published compatible packages.
- Removes or rejects conflicting distro Docker packages before installing Docker CE.
- Enables Docker at boot and adds the remote user to the Docker group.
- Persists `vm.max_map_count=262144` in a dedicated `/etc/sysctl.d` file for OpenSearch.
- Verifies the Docker daemon, Compose v2, architecture, and kernel setting.

This is a forward-compatible strategy, not a promise that an unknown future Ubuntu release will work before Docker and every container publisher support it.

## Operations

Both local interfaces provide these operations:

| Operation | Behavior |
| --- | --- |
| `init-env` | Generate the ignored service secret file |
| `bootstrap` | Upload and run the Ubuntu host bootstrap |
| `deploy` | Upload a committed release and start selected profiles |
| `up` | Start selected profiles from the current release |
| `stop` | Stop services belonging to selected profiles |
| `down` | Stop the complete project without removing volumes |
| `status` | Show containers, health, host memory, disk, and volume usage |
| `logs` | Follow logs for a service or selected profile |
| `check` | Validate local configuration without mutating the remote host |
| `tunnel` | Open SSH forwards for selected profiles |
| `destroy` | Remove project containers and named volumes after explicit confirmation |

`destroy` requires the operator to type the configured remote target and confirm permanent data loss. Normal `stop`, `down`, deployment, and release pruning never remove volumes.

## Resource Policy

The Compose file sets development-oriented memory limits that can be overridden in `.env`:

| Component | Default memory limit |
| --- | ---: |
| Application PostgreSQL | 1 GiB |
| Application Redis | 512 MiB |
| Chroma | 4 GiB |
| OpenSearch | 6 GiB with a 2 GiB JVM heap |
| OpenSearch Dashboards | 1 GiB |
| Langfuse web | 2 GiB |
| Langfuse worker | 2 GiB |
| Langfuse PostgreSQL | 2 GiB |
| Langfuse Redis | 512 MiB |
| ClickHouse | 6 GiB |
| MinIO | 1 GiB |
| pgAdmin | 512 MiB |
| RedisInsight | 512 MiB |

The preflight command totals selected limits, adds host overhead, and warns when the VM is undersized. It does not refuse deployment. A 32 GiB VM is appropriate for selected profiles under personal development load; running every profile simultaneously should use at least 48 GiB, with 64 GiB preferred.

## Health and Error Handling

Container health checks cover PostgreSQL readiness, authenticated Redis ping, Chroma heartbeat, authenticated OpenSearch cluster health, OpenSearch Dashboards status, Langfuse web readiness and worker dependencies, ClickHouse ping, MinIO readiness, pgAdmin ping, and the RedisInsight health endpoint.

Local and remote scripts use strict error behavior. Preflight checks validate profile names, the `tools`/`core` dependency, SSH connectivity, required secrets, local utilities, remote disk space, Docker/Compose compatibility, and available memory. Concurrent deployments are rejected with a remote lock.

## Verification Strategy

Implementation follows test-first development for script and configuration contracts. Verification includes:

- Render the Compose model for every supported profile combination.
- Reject unknown profiles and `tools` without `core`.
- Assert that no image reference uses `latest`.
- Assert that every published port binds to `127.0.0.1`.
- Assert that persistent services use named volumes and health checks.
- Reject missing secrets and unchanged example placeholders.
- Verify that Bash and PowerShell expose equivalent operations.
- Validate Bash and PowerShell syntax.
- Verify generated SSH mappings for each profile, including Chroma on local port `18000`.
- Exercise archive naming, checksum verification, release selection, locking, and pruning against temporary directories.
- Run remote Compose validation and service health checks after deployment.

The local `check` operation performs checks that do not require Docker and uses `docker compose config` when Docker Compose is available. Remote deployment always performs authoritative Compose validation after bootstrap.

## Reference Documentation

- [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [Langfuse Docker Compose deployment](https://langfuse.com/self-hosting/deployment/docker-compose)
- [Chroma Docker deployment](https://docs.trychroma.com/guides/deploy/docker)
- [OpenSearch Docker deployment](https://docs.opensearch.org/latest/install-and-configure/install-opensearch/docker/)
