#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
# shellcheck source=scripts/lib/common.sh
source "$script_dir/lib/common.sh"

normalize_local_port() {
  local key=$1 value=$2 destination=$3 digits
  [[ "$value" =~ ^[0-9]+$ ]] || common_die "$key must contain ASCII digits only"

  digits=$value
  while [[ "$digits" == 0* && ${#digits} -gt 1 ]]; do
    digits=${digits#0}
  done
  ((${#digits} <= 5)) || common_die "$key must be between 1 and 65535"
  ((10#$digits >= 1 && 10#$digits <= 65535)) ||
    common_die "$key must be between 1 and 65535"
  printf -v "$destination" '%s' "$((10#$digits))"
}

add_forward() {
  local local_port=$1 remote_port=$2 used_port
  for used_port in "${selected_local_ports[@]}"; do
    [[ "$used_port" != "$local_port" ]] || common_die "duplicate local port: $local_port"
  done
  selected_local_ports+=("$local_port")
  forward_args+=(-L "$local_port:127.0.0.1:$remote_port")
}

warn_probe_fallback() {
  printf '%s\n' \
    'WARNING: no supported local port probe is available; relying on SSH ExitOnForwardFailure' >&2
}

check_local_ports_available() {
  local platform probe= port output status
  platform=$(uname -s 2>/dev/null || printf 'Unknown')
  case "$platform" in
    Darwin)
      if command -v lsof >/dev/null 2>&1; then
        probe=lsof
      fi
      ;;
    Linux)
      if command -v ss >/dev/null 2>&1; then
        probe=ss
      fi
      ;;
  esac

  if [[ -z "$probe" ]]; then
    warn_probe_fallback
    return
  fi

  for port in "${selected_local_ports[@]}"; do
    if [[ "$probe" == lsof ]]; then
      status=0
      lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || status=$?
      if ((status == 0)); then
        common_die "local port is already in use: $port"
      fi
      if ((status > 1)); then
        printf 'WARNING: lsof could not inspect local port %s; relying on SSH ExitOnForwardFailure\n' \
          "$port" >&2
      fi
    else
      if output=$(ss -H -ltn "sport = :$port" 2>/dev/null); then
        [[ -z "$output" ]] || common_die "local port is already in use: $port"
      else
        printf 'WARNING: ss could not inspect local port %s; relying on SSH ExitOnForwardFailure\n' \
          "$port" >&2
      fi
    fi
  done
}

load_remote_env "${STACK_REMOTE_ENV:-$repo_root/remote.env}"
validate_profiles "$@"

normalize_local_port LOCAL_POSTGRES_PORT "$LOCAL_POSTGRES_PORT" LOCAL_POSTGRES_PORT
normalize_local_port LOCAL_REDIS_PORT "$LOCAL_REDIS_PORT" LOCAL_REDIS_PORT
normalize_local_port LOCAL_CHROMA_PORT "$LOCAL_CHROMA_PORT" LOCAL_CHROMA_PORT
normalize_local_port LOCAL_CHROMA_ADMIN_PORT "$LOCAL_CHROMA_ADMIN_PORT" LOCAL_CHROMA_ADMIN_PORT
normalize_local_port LOCAL_DYNAMODB_PORT "$LOCAL_DYNAMODB_PORT" LOCAL_DYNAMODB_PORT
normalize_local_port LOCAL_DYNAMODB_ADMIN_PORT "$LOCAL_DYNAMODB_ADMIN_PORT" LOCAL_DYNAMODB_ADMIN_PORT
normalize_local_port LOCAL_OLLAMA_LLM_PORT "$LOCAL_OLLAMA_LLM_PORT" LOCAL_OLLAMA_LLM_PORT
normalize_local_port LOCAL_OLLAMA_EMBEDDING_PORT "$LOCAL_OLLAMA_EMBEDDING_PORT" LOCAL_OLLAMA_EMBEDDING_PORT
normalize_local_port LOCAL_OPENSEARCH_PORT "$LOCAL_OPENSEARCH_PORT" LOCAL_OPENSEARCH_PORT
normalize_local_port \
  LOCAL_OPENSEARCH_DASHBOARDS_PORT "$LOCAL_OPENSEARCH_DASHBOARDS_PORT" \
  LOCAL_OPENSEARCH_DASHBOARDS_PORT
normalize_local_port LOCAL_LANGFUSE_PORT "$LOCAL_LANGFUSE_PORT" LOCAL_LANGFUSE_PORT
normalize_local_port LOCAL_MINIO_API_PORT "$LOCAL_MINIO_API_PORT" LOCAL_MINIO_API_PORT
normalize_local_port \
  LOCAL_MINIO_CONSOLE_PORT "$LOCAL_MINIO_CONSOLE_PORT" LOCAL_MINIO_CONSOLE_PORT
normalize_local_port LOCAL_PGADMIN_PORT "$LOCAL_PGADMIN_PORT" LOCAL_PGADMIN_PORT
normalize_local_port LOCAL_REDISINSIGHT_PORT "$LOCAL_REDISINSIGHT_PORT" LOCAL_REDISINSIGHT_PORT

selected_core=false
selected_vector=false
selected_dynamodb=false
selected_inference=false
selected_search=false
selected_observability=false
selected_tools=false
for profile in "$@"; do
  printf -v "selected_$profile" '%s' true
done

forward_args=()
selected_local_ports=()
if [[ "$selected_core" == true ]]; then
  add_forward "$LOCAL_POSTGRES_PORT" 15432
  add_forward "$LOCAL_REDIS_PORT" 16379
fi
if [[ "$selected_vector" == true ]]; then
  add_forward "$LOCAL_CHROMA_PORT" 18000
  add_forward "$LOCAL_CHROMA_ADMIN_PORT" 18001
fi
if [[ "$selected_search" == true ]]; then
  add_forward "$LOCAL_OPENSEARCH_PORT" 9200
  add_forward "$LOCAL_OPENSEARCH_DASHBOARDS_PORT" 5601
fi
if [[ "$selected_observability" == true ]]; then
  add_forward "$LOCAL_LANGFUSE_PORT" 3000
  add_forward "$LOCAL_MINIO_API_PORT" 9090
  add_forward "$LOCAL_MINIO_CONSOLE_PORT" 9091
fi
if [[ "$selected_tools" == true ]]; then
  add_forward "$LOCAL_PGADMIN_PORT" 5050
  add_forward "$LOCAL_REDISINSIGHT_PORT" 5540
fi
if [[ "$selected_dynamodb" == true ]]; then
  add_forward "$LOCAL_DYNAMODB_PORT" 18002
  add_forward "$LOCAL_DYNAMODB_ADMIN_PORT" 18003
fi
if [[ "$selected_inference" == true ]]; then
  add_forward "$LOCAL_OLLAMA_LLM_PORT" 11440
  add_forward "$LOCAL_OLLAMA_EMBEDDING_PORT" 11441
fi

check_local_ports_available
command -v ssh >/dev/null 2>&1 || common_die "required command is unavailable: ssh"

ssh "${ssh_args[@]}" -NT \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  "${forward_args[@]}" "$ssh_target"
