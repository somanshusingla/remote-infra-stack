#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  die "usage: stack.sh up|stop profiles... | down | status | logs target | destroy target DESTROY-target"
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
release_dir=${STACK_RELEASE_DIR:-$(cd -- "$script_dir/../.." && pwd -P)}
stack_root=${STACK_ROOT:-$(cd -- "$release_dir/../.." && pwd -P)}
compose_script=${STACK_COMPOSE_SCRIPT:-$script_dir/compose.sh}
preflight_script=$script_dir/preflight.sh

profile_services() {
  case "$1" in
    core) printf '%s\n' app-postgres app-redis ;;
    vector) printf '%s\n' chroma chroma-admin ;;
    search) printf '%s\n' opensearch opensearch-dashboards ;;
    observability) printf '%s\n' langfuse-postgres langfuse-redis clickhouse minio langfuse-worker langfuse-web ;;
    tools) printf '%s\n' pgadmin redisinsight ;;
    dynamodb) printf '%s\n' dynamodb-local dynamodb-admin ;;
    inference) printf '%s\n' ollama-llm ollama-embedding ;;
    *) return 1 ;;
  esac
}

validate_profiles() {
  (($# > 0)) || die "at least one profile is required"
  declare -A seen=()
  local profile
  for profile in "$@"; do
    profile_services "$profile" >/dev/null || die "unknown profile: $profile"
    [[ -z "${seen[$profile]+x}" ]] || die "duplicate profile: $profile"
    seen[$profile]=1
  done
  if [[ -n "${seen[tools]+x}" && -z "${seen[core]+x}" ]]; then
    die "tools requires core"
  fi
}

expand_profiles() {
  local profile
  for profile in "$@"; do
    profile_services "$profile"
  done
}

compose() {
  bash "$compose_script" "$@"
}

action=${1:-}
[[ -n "$action" ]] || usage
shift

case "$action" in
  up)
    validate_profiles "$@"
    STACK_COMPOSE_SCRIPT="$compose_script" bash "$preflight_script" "$@"
    compose "$@" -- up -d --wait --build
    ;;
  stop)
    validate_profiles "$@"
    mapfile -t services < <(expand_profiles "$@")
    compose -- stop "${services[@]}"
    ;;
  down)
    (($# == 0)) || usage
    compose -- --profile '*' down
    ;;
  status)
    (($# == 0)) || usage
    compose -- --profile '*' ps
    "${FREE_BIN:-free}" -h
    "${DF_BIN:-df}" -h "$stack_root"
    "${DOCKER_BIN:-docker}" system df
    ;;
  logs)
    (($# == 1)) || usage
    target=$1
    if profile_services "$target" >/dev/null 2>&1; then
      mapfile -t services < <(profile_services "$target")
      compose -- logs -f "${services[@]}"
    else
      case "$target" in
        app-postgres|app-redis|chroma|chroma-admin|dynamodb-local|dynamodb-admin|ollama-llm|ollama-embedding|opensearch|opensearch-dashboards|langfuse-postgres|langfuse-redis|clickhouse|minio|langfuse-worker|langfuse-web|pgadmin|redisinsight)
          compose -- logs -f "$target"
          ;;
        *) die "unknown log target: $target" ;;
      esac
    fi
    ;;
  destroy)
    (($# == 2)) || die "destroy requires a target and exact DESTROY-<target> token"
    target=$1
    token=$2
    [[ "$target" == remote-infra-stack ]] ||
      die "destroy target must exactly equal remote-infra-stack"
    [[ "$token" == DESTROY-remote-infra-stack ]] ||
      die "destroy confirmation token must exactly equal DESTROY-remote-infra-stack"
    compose -- --profile '*' down -v
    ;;
  *) usage ;;
esac
