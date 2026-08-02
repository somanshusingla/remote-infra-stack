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
compose_script=$script_dir/compose.sh

profile_services() {
  case "$1" in
    core) printf '%s\n' app-postgres app-redis ;;
    vector) printf '%s\n' chroma ;;
    search) printf '%s\n' opensearch opensearch-dashboards ;;
    observability) printf '%s\n' langfuse-postgres langfuse-redis clickhouse minio langfuse-worker langfuse-web ;;
    tools) printf '%s\n' pgadmin redisinsight ;;
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

preflight_up() {
  local profiles=("$@")
  local df_bin=${DF_BIN:-df}
  local meminfo_file=${MEMINFO_FILE:-/proc/meminfo}
  local free_bytes
  free_bytes=$("$df_bin" --output=avail -B1 "$stack_root" 2>/dev/null | awk 'NR > 1 { value=$1 } END { print value }') ||
    die "could not determine free disk space for $stack_root"
  [[ "$free_bytes" =~ ^[0-9]+$ ]] || die "could not determine free disk space for $stack_root"
  if (( free_bytes < 10 * 1024 * 1024 * 1024 )); then
    die "less than 10 GiB of disk space is available under $stack_root"
  elif (( free_bytes < 20 * 1024 * 1024 * 1024 )); then
    printf 'WARNING: less than 20 GiB of disk space is available under %s\n' "$stack_root" >&2
  fi

  local model selected_services required_bytes available_kib
  model=$(compose "${profiles[@]}" -- config --format json) || die "Compose configuration rendering failed"
  mapfile -t selected_services < <(expand_profiles "${profiles[@]}")
  if command -v jq >/dev/null 2>&1; then
    required_bytes=$(jq --args \
      '[.services[$ARGS.positional[]].mem_limit // 0 | tonumber] | add // 0' \
      "${selected_services[@]}" <<<"$model") || die "could not total Compose memory limits"
    required_bytes=$((required_bytes + 2 * 1024 * 1024 * 1024))
    if [[ -r "$meminfo_file" ]]; then
      available_kib=$(awk '$1 == "MemTotal:" { print $2; exit }' "$meminfo_file")
      if [[ "$available_kib" =~ ^[0-9]+$ ]] && (( required_bytes > available_kib * 1024 )); then
        printf 'WARNING: selected service memory limits plus 2 GiB host overhead exceed host memory\n' >&2
      fi
    else
      printf 'WARNING: memory availability could not be read from %s\n' "$meminfo_file" >&2
    fi
  else
    printf 'WARNING: jq is unavailable; selected service memory limits were not totaled\n' >&2
  fi
}

action=${1:-}
[[ -n "$action" ]] || usage
shift

case "$action" in
  up)
    validate_profiles "$@"
    preflight_up "$@"
    compose "$@" -- up -d --wait
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
        app-postgres|app-redis|chroma|opensearch|opensearch-dashboards|langfuse-postgres|langfuse-redis|clickhouse|minio|langfuse-worker|langfuse-web|pgadmin|redisinsight)
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
    [[ -n "$target" && "$token" == "DESTROY-$target" ]] ||
      die "destroy confirmation token must exactly equal DESTROY-$target"
    compose -- --profile '*' down -v
    ;;
  *) usage ;;
esac
