# Task 7 Ubuntu Bootstrap Verification

Verification date: 2026-08-02
Target: GCP project `remote-infra-stack`, VM `remote-infra-stack`, zone `asia-south1-c`

This is a sanitized verification record. Commands below show the remote payload rather than the local absolute path to `gcloud.cmd`. Output is limited to public software metadata, host capability facts, public registry metadata, and Task 7 paths.

## Data-handling attestation

`.env`, `remote.env`, credentials, private keys, SSH private/public key files, and SSH key material were neither transferred to the VM nor emitted to command output or this artifact. No secret value was read for this verification. Image manifests are public registry metadata. No stack service container was started, and no image was pulled.

## Uploaded scope

Only these Task 7 files were uploaded under the remote user's home:

```text
/home/Somanshu/task7-red/tests/helpers.py
/home/Somanshu/task7-red/tests/test_bootstrap.py
/home/Somanshu/task7-green1/scripts/remote/bootstrap-host.sh
/home/Somanshu/task7-green1/tests/helpers.py
/home/Somanshu/task7-green1/tests/test_bootstrap.py
/home/Somanshu/task7-green1/tests/fixtures/os-release/debian
/home/Somanshu/task7-green1/tests/fixtures/os-release/ubuntu-22.04
/home/Somanshu/task7-green1/tests/fixtures/os-release/ubuntu-24.04
/home/Somanshu/task7-green1/tests/fixtures/os-release/ubuntu-26.04
/home/Somanshu/task7-green1/tests/fixtures/os-release/ubuntu-future-lts
/home/Somanshu/task7-bootstrap-host.sh
/home/Somanshu/task7-versions.env
/home/Somanshu/task7-versions-corrected.env
/home/Somanshu/task7-versions-pinned.env
/home/Somanshu/task7-inspect-images.sh
/home/Somanshu/task7-query-redis.sh
```

Python bytecode caches and these public-output files were generated on the VM, not uploaded: `/home/Somanshu/task7-*.inspect`, `/home/Somanshu/task7-*.raw.json`, `/home/Somanshu/task7-manifests-*.tsv`, and `/home/Somanshu/task7-idempotent*.log`.

## Host and bootstrap facts

Sanitized remote command:

```bash
bash -n /home/Somanshu/task7-bootstrap-host.sh
bash /home/Somanshu/task7-bootstrap-host.sh --check
cat /etc/os-release
uname -m
dpkg --print-architecture
docker version
docker compose version
docker buildx version
systemctl is-active docker
systemctl is-enabled docker
sysctl -n vm.max_map_count
id -nG Somanshu
docker ps -a
docker image ls
```

Relevant sanitized output (2026-08-02):

```text
Host supports Docker bootstrap: Ubuntu 26.04 (resolute), amd64.
PRETTY_NAME="Ubuntu 26.04 LTS"
VERSION_ID="26.04"
VERSION_CODENAME=resolute
ID=ubuntu
UBUNTU_CODENAME=resolute
x86_64
amd64
Docker Engine client: 29.7.1, linux/amd64
Docker Engine server: 29.7.1, linux/amd64
containerd: 2.2.6
Docker Compose version v5.3.1
github.com/docker/buildx v0.36.0 df28b0a0b6a44453a87bd53c438432f4120962c9
docker.service active: active
docker.service enabled: enabled
vm.max_map_count: 262144
groups: Somanshu adm dialout cdrom floppy audio dip video plugdev lxd netdev ubuntu google-sudoers docker
CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS     NAMES
IMAGE   ID             DISK USAGE   CONTENT SIZE   EXTRA
```

The header-only `docker ps -a` and `docker image ls` output proves the VM had zero containers and zero locally stored images after all registry inspections.

## Idempotence

Executed installation commands:

```bash
bash /home/Somanshu/task7-bootstrap-host.sh --install
bash /home/Somanshu/task7-bootstrap-host.sh --install > /home/Somanshu/task7-idempotent.log
bash /home/Somanshu/task7-bootstrap-host.sh --install > /home/Somanshu/task7-idempotent-hardened.log
```

All three commands exited `0`. Both repeat-run log tails ended with:

```text
Docker Compose version v5.3.1
Docker bootstrap complete for Ubuntu 26.04 (resolute), amd64; log out and back in for Docker group access.
```

The final read-only `--check` also exited `0` with:

```text
Host supports Docker bootstrap: Ubuntu 26.04 (resolute), amd64.
```

## Registry-only manifest verification

For every pinned reference, the VM ran both commands without `pull` or `run`:

```bash
docker buildx imagetools inspect "$reference"
docker buildx imagetools inspect --raw "$reference"
```

The normal inspection supplied the top-level manifest-list digest. The raw index supplied the media type and platform descriptors; verification required at least one descriptor with `os=linux` and `architecture=amd64`. Fresh fix-round-1 verification exited `0` for all 13 references:

| Variable | Exact tag and manifest-list digest | Raw index media type | Platform |
|---|---|---|---|
| `APP_POSTGRES_IMAGE` | `docker.io/postgres:18.4-bookworm@sha256:1961f96e6029a02c3812d7cb329a3b03a3ac2bb067058dec17b0f5596aca9296` | `application/vnd.oci.image.index.v1+json` | `linux/amd64` |
| `APP_REDIS_IMAGE` | `docker.io/redis:8.8.0-trixie@sha256:234c902a2db49461a129e2d4aeff85b28cf20187ed274a67f6e50995fa713c7b` | `application/vnd.oci.image.index.v1+json` | `linux/amd64` |
| `CHROMA_IMAGE` | `docker.io/chromadb/chroma:1.5.9@sha256:1e0b73a187a28757c572acba508c46f48c9e8b0acaf5c20e6d95cdedce1acdf6` | `application/vnd.oci.image.index.v1+json` | `linux/amd64` |
| `OPENSEARCH_IMAGE` | `docker.io/opensearchproject/opensearch:3.7.0@sha256:44ba7ea58a319adf61c33ab16873f9ef5dbb30b291a832d375172f0b2d24e3c9` | `application/vnd.docker.distribution.manifest.list.v2+json` | `linux/amd64` |
| `OPENSEARCH_DASHBOARDS_IMAGE` | `docker.io/opensearchproject/opensearch-dashboards:3.7.0@sha256:1c9e0a50472123bd2cde615ffb7157ef436db794e082dba09e42c3a6d3ce91a3` | `application/vnd.docker.distribution.manifest.list.v2+json` | `linux/amd64` |
| `LANGFUSE_WEB_IMAGE` | `docker.io/langfuse/langfuse:3.176.0@sha256:88ca1ec907c8411f76c4602d2aac753045c18b5ae455cddfe08f609e07852976` | `application/vnd.docker.distribution.manifest.list.v2+json` | `linux/amd64` |
| `LANGFUSE_WORKER_IMAGE` | `docker.io/langfuse/langfuse-worker:3.176.0@sha256:56c99a7a1ce53e947280e207ac4a1540298098f440c5480389de34adac1c4fd3` | `application/vnd.docker.distribution.manifest.list.v2+json` | `linux/amd64` |
| `LANGFUSE_POSTGRES_IMAGE` | `docker.io/postgres:17.10-bookworm@sha256:4f736ae292687621d4dbe0d499ffd024a36bd2ee7d8ca6f2ccd4c800f047b394` | `application/vnd.oci.image.index.v1+json` | `linux/amd64` |
| `LANGFUSE_REDIS_IMAGE` | `docker.io/redis:7.4.3-bookworm@sha256:236e397c1d5ab7a94adaf1a51eec3ca8333b05fafcd6d423c6c7cc5987e519a0` | `application/vnd.oci.image.index.v1+json` | `linux/amd64` |
| `CLICKHOUSE_IMAGE` | `docker.io/clickhouse/clickhouse-server:25.12@sha256:8a790dd3468db22b1d4e7b18a176f378ff5ff6053b9c48dd4ea1fa71a24c5ba6` | `application/vnd.oci.image.index.v1+json` | `linux/amd64` |
| `MINIO_IMAGE` | `docker.io/minio/minio:RELEASE.2025-06-13T11-33-47Z@sha256:064117214caceaa8d8a90ef7caa58f2b2aeb316b5156afe9ee8da5b4d83e12c8` | `application/vnd.oci.image.index.v1+json` | `linux/amd64` |
| `PGADMIN_IMAGE` | `docker.io/dpage/pgadmin4:9.16@sha256:40fa840c5bb7c8463957f1255b01283732c2d8c9396a956d180f8e6c296753b3` | `application/vnd.oci.image.index.v1+json` | `linux/amd64` |
| `REDISINSIGHT_IMAGE` | `docker.io/redis/redisinsight:3.4.2@sha256:85562d67a9128ac7f764bb3d1ac909fcf77708c7c8f55bd1605a85b3c4becc83` | `application/vnd.docker.distribution.manifest.list.v2+json` | `linux/amd64` |

After this fresh 13-reference pass, `docker image ls` and `docker ps -a` remained empty.
