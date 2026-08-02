#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
release_dir=${STACK_RELEASE_DIR:-$(cd -- "$script_dir/../.." && pwd -P)}
stack_root=${STACK_ROOT:-$(cd -- "$release_dir/../.." && pwd -P)}
runtime_env=${STACK_RUNTIME_ENV_FILE:-$stack_root/runtime/.env}
compose_script=$script_dir/compose.sh
curl_bin=${CURL_BIN:-curl}

(($# > 0)) || die "usage: health.sh profile..."
profiles=("$@")
services=()
declare -A selected=()
for profile in "${profiles[@]}"; do
  case "$profile" in
    core) profile_services=(app-postgres app-redis) ;;
    vector) profile_services=(chroma) ;;
    search) profile_services=(opensearch opensearch-dashboards) ;;
    observability) profile_services=(langfuse-postgres langfuse-redis clickhouse minio langfuse-worker langfuse-web) ;;
    tools) profile_services=(pgadmin redisinsight) ;;
    *) die "unknown profile: $profile" ;;
  esac
  [[ -z "${selected[$profile]+x}" ]] || die "duplicate profile: $profile"
  selected[$profile]=1
  services+=("${profile_services[@]}")
done
if [[ -n "${selected[tools]+x}" && -z "${selected[core]+x}" ]]; then
  die "tools requires core"
fi

command -v jq >/dev/null 2>&1 || die "required command is unavailable: jq"
ps_output=$(bash "$compose_script" "${profiles[@]}" -- ps --all --format json) ||
  die "could not read Compose container health"
ps_json=$(jq -c -s '[.[] | if type == "array" then .[] else . end]' <<<"$ps_output") ||
  die "Compose returned invalid JSON health data"
for service in "${services[@]}"; do
  if ! jq -e --arg service "$service" \
    '([.[] | select(.Service == $service)]) as $matching |
     ($matching | length) > 0 and
     all($matching[]; .State == "running" and .Health == "healthy")' \
    <<<"$ps_json" >/dev/null; then
    die "service is not running and healthy: $service"
  fi
done

compose_exec() {
  bash "$compose_script" "${profiles[@]}" -- exec -T "$@"
}

http_get() {
  "$curl_bin" --fail --silent --show-error --location --max-time 10 "$1" >/dev/null
}

env_value() {
  local key=$1
  local file=$runtime_env
  [[ -f "$file" && ! -L "$file" ]] || die "runtime environment file is unavailable"
  awk -F= -v wanted="$key" '$1 == wanted { sub(/^[^=]*=/, ""); print; found=1; exit } END { if (!found) exit 1 }' "$file"
}

if [[ -n "${selected[core]+x}" ]]; then
  compose_exec app-postgres sh -c 'exec pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null
  compose_exec app-redis sh -c 'REDISCLI_AUTH="$APP_REDIS_PASSWORD" exec redis-cli ping' >/dev/null
fi
if [[ -n "${selected[vector]+x}" ]]; then
  http_get http://127.0.0.1:18000/api/v2/heartbeat
fi
if [[ -n "${selected[search]+x}" ]]; then
  opensearch_password=$(env_value OPENSEARCH_INITIAL_ADMIN_PASSWORD) ||
    die "OPENSEARCH_INITIAL_ADMIN_PASSWORD is missing from the runtime environment"
  escaped_password=${opensearch_password//\\/\\\\}
  escaped_password=${escaped_password//\"/\\\"}
  printf 'user = "admin:%s"\n' "$escaped_password" |
    "$curl_bin" --config - --fail --silent --show-error --insecure --max-time 10 \
      https://127.0.0.1:9200/_cluster/health >/dev/null
  unset opensearch_password escaped_password
  http_get http://127.0.0.1:5601/api/status
fi
if [[ -n "${selected[observability]+x}" ]]; then
  compose_exec langfuse-postgres sh -c 'exec pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null
  compose_exec langfuse-redis sh -c 'REDISCLI_AUTH="$LANGFUSE_REDIS_PASSWORD" exec redis-cli ping' >/dev/null
  compose_exec clickhouse wget -qO- http://127.0.0.1:8123/ping >/dev/null
  http_get http://127.0.0.1:9090/minio/health/ready
  http_get http://127.0.0.1:3000/api/public/ready
fi
if [[ -n "${selected[tools]+x}" ]]; then
  http_get http://127.0.0.1:5050/misc/ping
  http_get http://127.0.0.1:5540/api/health/
fi

printf 'Health verification passed for profiles:'
printf ' %s' "${profiles[@]}"
printf '\n'
