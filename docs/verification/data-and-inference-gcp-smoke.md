# Data and Inference GCP Smoke Verification

Verification date: 2026-08-03

Profiles: `core vector dynamodb inference`

Target: GCP project `remote-infra-stack`, VM `high-mem-64-gb-us-east-1`, zone
`us-east1-c`, machine type `e2-highmem-8` (8 vCPU, 64 GB memory), Ubuntu 26.04 LTS
minimal, `linux/amd64`

This is a sanitized acceptance record for a personal, single-VM development stack. It
does not claim production readiness, high availability, backup coverage, or validation
of `search`, `observability`, or `tools`.

## Release and transport

- Git commit: `c74a84896266b9978764eeb3b8f42c344767ad28`
- Activated release:
  `20260803T073926Z-c74a84896266-b791db2d7f6c467c953392ee7e92a6a4`
- Docker Engine: `29.7.1`
- Docker Compose: `5.3.1`
- Bootstrap persisted and verified `vm.max_map_count=262144` and
  `net.ipv4.ip_forward=1`.

The connection used GCP IAP plus SSH, addressed by project, VM name, and zone. The
repository's ignored `remote.env` pointed SSH at a local IAP listener. No ephemeral IP
was persisted in repository files or this record. The ignored `.env` was uploaded
separately from the versioned release and stored with private permissions; `remote.env`
remained local.

No secret values, credentials, private-key material, generated chat text, or embedding
vector values were written to this document or committed to Git.

## Deployment health

The repository-owned Compose manifest built the vendored Chroma Admin image natively,
pulled the pinned service images, waited for first model downloads, and reported all
eight selected services healthy:

As part of acceptance, the VM ran `docker buildx imagetools inspect --raw` on
each newly introduced digest-pinned registry index and required a
`linux/amd64 child manifest`:

| Input | Exact verified reference |
| --- | --- |
| `CHROMA_ADMIN_NODE_IMAGE` | `docker.io/library/node:20.19.2-bookworm-slim@sha256:7cd3fbc830c75c92256fe1122002add9a1c025831af8770cd0bf8e45688ef661` |
| `DYNAMODB_LOCAL_IMAGE` | `docker.io/amazon/dynamodb-local:3.3.0@sha256:d89f8fcc6b1a39cb35976c248ed42a28c66ae00dc043099210f5571e42648ab4` |
| `DYNAMODB_ADMIN_IMAGE` | `docker.io/aaronshaf/dynamodb-admin:5.3.4@sha256:ac41724cd99706256d405a14a5fb96f51f18c41a630c84fa3357f900cbd16d2e` |
| `OLLAMA_IMAGE` | `docker.io/ollama/ollama:0.32.5@sha256:4dea9fb511947e24a84237bb636b0203abcb2ff0d3fbc7b4ff865deb91362131` |

| Profile | Healthy services |
| --- | --- |
| `core` | `app-postgres`, `app-redis` |
| `vector` | `chroma`, `chroma-admin` |
| `dynamodb` | `dynamodb-local`, `dynamodb-admin` |
| `inference` | `ollama-llm`, `ollama-embedding` |

The local SSH tunnel exposed only loopback endpoints:

- PostgreSQL: `127.0.0.1:5432`
- Redis: `127.0.0.1:6379`
- Chroma API and Admin: `http://127.0.0.1:18000` and
  `http://127.0.0.1:18001`
- DynamoDB Local and Admin: `http://127.0.0.1:18002` and
  `http://127.0.0.1:18003`
- Ollama chat and embedding: `http://127.0.0.1:11440` and
  `http://127.0.0.1:11441`

## Data and UI acceptance

A uniquely timestamped disposable marker was created and read through each selected
datastore:

- PostgreSQL returned the expected row.
- Redis returned the expected key value.
- Chroma returned the expected collection record through its v2 API.
- DynamoDB Local returned the expected table item through the AWS SDK.

Chroma Admin's setup route returned HTTP 200, and its own collection and record backend
routes found the marker through the internal `chroma` service address. DynamoDB Admin's
table page returned HTTP 200 and rendered the marker table and item. The browser
automation was unavailable because the in-app browser tool rejected the session's
sandbox metadata,
so this run does not claim interactive click-through coverage.

An ordinary Compose `down` removed the containers and network without `-v`. The six
named volumes for PostgreSQL, Redis, Chroma, DynamoDB, Ollama chat, and Ollama embeddings
remained. After `up`, all eight services returned healthy and all four markers were read
again successfully. The four timestamped smoke artifacts were then cleaned, their
absence was verified through the APIs/admin adapters, and the services were left
running.

## Inference and cache reuse

- `gemma4:e4b` completed a non-streaming chat request with an assistant response, stop
  completion, 16 prompt tokens, 2 generated tokens, and approximately 2.61 seconds
  elapsed. Generated content was deliberately not recorded.
- `embeddinggemma:300m` returned one finite embedding with dimension 768 in
  approximately 5.73 seconds. Vector values were deliberately not recorded.
- Before lifecycle testing, the two model stores contained approximately 9.61 GB and
  622 MB. After both an inference-profile restart and a full Compose down/up, both
  registrations remained available. Logs contained zero new pull markers, proving
  model cache reuse from the named volumes.

## Host observations

After the down/up acceptance cycle, the VM reported 62 GiB usable memory, about 1.9 GiB
used, and about 60 GiB available. The boot filesystem used 30 GiB of 96 GiB. Docker
reported approximately 13.29 GB of images, 10.3 GB of named-volume data, and 6.49 GB of
build cache.

The unofficial Chroma Admin development build reported upstream npm audit findings.
That UI remains a loopback-only, tunneled personal-development tool; this record does
not reinterpret it as hardened or production-safe software.
