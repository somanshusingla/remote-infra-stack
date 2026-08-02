#!/usr/bin/env bash
set -eu

runtime_config="${OPENSEARCH_CONFIG_PATH:-/usr/share/opensearch/config/opensearch.yml}"
delegate_entrypoint="${OPENSEARCH_DOCKER_ENTRYPOINT:-/usr/share/opensearch/opensearch-docker-entrypoint.sh}"

[[ -n "${REMOTE_INFRA_OPENSEARCH_CONFIG_B64:-}" ]] || {
  printf 'ERROR: verified OpenSearch config is unavailable\n' >&2
  exit 1
}
printf '%s' "$REMOTE_INFRA_OPENSEARCH_CONFIG_B64" | base64 --decode >"$runtime_config"
exec "$delegate_entrypoint" "$@"
