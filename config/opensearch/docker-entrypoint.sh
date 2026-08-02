#!/usr/bin/env bash
set -eu

config_template="${OPENSEARCH_CONFIG_TEMPLATE:-/usr/share/opensearch/config-template/opensearch.yml}"
runtime_config="${OPENSEARCH_CONFIG_PATH:-/usr/share/opensearch/config/opensearch.yml}"
delegate_entrypoint="${OPENSEARCH_DOCKER_ENTRYPOINT:-/usr/share/opensearch/opensearch-docker-entrypoint.sh}"

cp "$config_template" "$runtime_config"
exec "$delegate_entrypoint" "$@"
