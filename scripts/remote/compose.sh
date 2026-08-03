#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
release_dir=${STACK_RELEASE_DIR:-$(cd -- "$script_dir/../.." && pwd -P)}
release_files_dir=${STACK_RELEASE_HELD_DIR:-$release_dir}
stack_root=${STACK_ROOT:-$(cd -- "$release_dir/../.." && pwd -P)}
runtime_env=${STACK_RUNTIME_ENV_FILE:-$stack_root/runtime/.env}
versions_env=${STACK_VERSIONS_ENV_FILE:-$release_files_dir/versions.env}
compose_file=${STACK_COMPOSE_FILE:-$release_files_dir/compose.yaml}
opensearch_config=${STACK_OPENSEARCH_CONFIG_FILE:-$release_files_dir/config/opensearch/opensearch.yml}
opensearch_entrypoint=${STACK_OPENSEARCH_ENTRYPOINT_FILE:-$release_files_dir/config/opensearch/docker-entrypoint.sh}

profiles=()
declare -A selected=()
separator=0
while (($#)); do
  if [[ "$1" == -- ]]; then
    separator=1
    shift
    break
  fi
  profile=$1
  case "$profile" in
    core|vector|search|observability|tools|dynamodb|inference) ;;
    *) die "unknown profile: $profile" ;;
  esac
  [[ -z "${selected[$profile]+x}" ]] || die "duplicate profile: $profile"
  selected[$profile]=1
  profiles+=("$profile")
  shift
done
(( separator == 1 )) || die "usage: compose.sh profiles... -- compose-arguments..."
(($# > 0)) || die "at least one Compose argument is required"
if [[ -n "${selected[tools]+x}" && -z "${selected[core]+x}" ]]; then
  die "tools requires core"
fi

docker_bin=${DOCKER_BIN:-docker}
if [[ "${STACK_TEST_MODE:-0}" == 1 && -z "${DOCKER_BIN:-}" && -x "$release_dir/tests/fakes/docker" ]]; then
  docker_bin=$release_dir/tests/fakes/docker
fi

for transport_file in "$opensearch_config" "$opensearch_entrypoint"; do
  [[ -f "$transport_file" ]] || die "verified OpenSearch input is unavailable"
  transport_size=$(stat -Lc '%s' -- "$transport_file")
  ((transport_size > 0 && transport_size <= 65536)) ||
    die "verified OpenSearch input exceeds the 64 KiB transport boundary"
done
STACK_OPENSEARCH_CONFIG_B64=$(base64 --wrap=0 <"$opensearch_config")
STACK_OPENSEARCH_ENTRYPOINT_B64=$(base64 --wrap=0 <"$opensearch_entrypoint")
export STACK_OPENSEARCH_CONFIG_B64 STACK_OPENSEARCH_ENTRYPOINT_B64

command=(
  "$docker_bin" compose
  --env-file "$versions_env"
  --env-file "$runtime_env"
  --project-directory "$release_dir"
  --file "$compose_file"
)
for profile in "${profiles[@]}"; do
  command+=(--profile "$profile")
done
command+=("$@")
exec "${command[@]}"
