# All-Profiles GCP Smoke Verification

Verification date: 2026-08-03

Profiles: `core vector search observability tools dynamodb inference`

Target: GCP project `remote-infra-stack`, VM `high-mem-64-gb-us-east-1`, zone
`us-east1-c`, machine type `e2-highmem-8` (8 vCPU, 64 GB memory), Ubuntu 26.04 LTS
minimal, `linux/amd64`

This is a sanitized acceptance record for the complete personal-development stack on
one VM. It is not a production, high-availability, or backup validation.

## Release and security boundary

- Git commit: `1dce504c48501c92472939c281888f89b5ee610b`
- Activated release:
  `20260803T081220Z-1dce504c4850-9ce1f20c776746209ea4a82e9bfec671`
- Selected Compose memory limits: 45 GiB; preflight requirement with host allowance:
  47 GiB. The 64 GB VM passed preflight.

The release archive and ignored `.env` traveled through SSH over a GCP IAP listener.
The connection was addressed by project, VM name, and zone. No ephemeral IP was stored
in the repository or this record. `remote.env` remained local, service ports stayed
bound to loopback, and `.env` was uploaded separately with private permissions.

No secret values, credentials, Langfuse keys, private-key material, page bodies, model
output, or vector values were recorded.

## Service health

Compose waited for container health, the repository health script passed, and the new
release activated only after Compose reported all 18 services healthy:

| Profile | Healthy services |
| --- | --- |
| `core` | `app-postgres`, `app-redis` |
| `vector` | `chroma`, `chroma-admin` |
| `search` | `opensearch`, `opensearch-dashboards` |
| `observability` | `langfuse-postgres`, `langfuse-redis`, `clickhouse`, `minio`, `langfuse-worker`, `langfuse-web` |
| `tools` | `pgadmin`, `redisinsight` |
| `dynamodb` | `dynamodb-local`, `dynamodb-admin` |
| `inference` | `ollama-llm`, `ollama-embedding` |

The previously validated PostgreSQL, Redis, Chroma, DynamoDB, and Ollama data/model
volumes were reused. Their CRUD, UI-adapter, inference, cache-reuse, down/up persistence,
and cleanup checks are recorded in
[data-and-inference-gcp-smoke.md](data-and-inference-gcp-smoke.md).

## Local application and UI access

The repository's all-profile tunnel exposed the following endpoints on local loopback:

- PostgreSQL and Redis: `127.0.0.1:5432` and `127.0.0.1:6379`
- Chroma API and Admin UI: `http://127.0.0.1:18000` and
  `http://127.0.0.1:18001`
- OpenSearch API and OpenSearch Dashboards: `https://127.0.0.1:9200` and
  `http://127.0.0.1:5601`
- Langfuse: `http://127.0.0.1:3000`
- MinIO API and Console: `http://127.0.0.1:9090` and
  `http://127.0.0.1:9091`
- pgAdmin and RedisInsight: `http://127.0.0.1:5050` and
  `http://127.0.0.1:5540`
- DynamoDB Local and Admin UI: `http://127.0.0.1:18002` and
  `http://127.0.0.1:18003`
- Ollama chat and embeddings: `http://127.0.0.1:11440` and
  `http://127.0.0.1:11441`

The following tunnel-level checks passed without recording response bodies:

| Surface | Acceptance result |
| --- | --- |
| OpenSearch | Unauthenticated TLS request returned 401; authenticated cluster health returned 200 |
| OpenSearch Dashboards | Authenticated status API returned 200; ELK-style login UI followed its redirect and rendered with 200 |
| Langfuse | `/api/public/ready` and the initial browser UI returned 200 |
| MinIO | API liveness and Console UI returned 200 |
| pgAdmin | Ping returned 200; login redirect completed with a 200 UI |
| RedisInsight | Health endpoint and UI returned 200 |

OpenSearch accepted a uniquely named disposable index, document write, refreshed
search, and delete through the local tunnel. The search returned the expected single
result, and a final lookup proved the disposable index was cleaned. This validates the
local-application path as well as OpenSearch Dashboards, the requested ELK-style UI.

The first Langfuse account/project was intentionally left for the user to create in the
UI; application API keys do not exist until that setup is complete. Browser automation
was unavailable because the in-app browser tool rejected the session's sandbox
metadata, so UI acceptance used the products' rendered HTTP pages and health/status
adapters rather than automated click-through.

## Resource snapshot

With all 18 containers healthy, the VM reported 62 GiB usable memory, 7.1 GiB used,
and 55 GiB available. The boot filesystem used 41 GiB of 96 GiB. Docker reported
approximately 24.75 GB of images, 10.39 GB across 14 active named volumes, and 6.49 GB
of build cache.

The services and the full local tunnel were left running after verification. All
service exposure remains limited to SSH-forwarded loopback endpoints.
