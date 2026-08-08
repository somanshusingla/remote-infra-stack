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
compose_script=${STACK_COMPOSE_SCRIPT:-$script_dir/compose.sh}
versions_env=${STACK_VERSIONS_ENV_FILE:-$release_dir/versions.env}
curl_bin=${CURL_BIN:-curl}
docker_bin=${DOCKER_BIN:-docker}
jq_bin=${JQ_BIN:-jq}
nvidia_smi_bin=${NVIDIA_SMI_BIN:-nvidia-smi}

(($# > 0)) || die "usage: health.sh profile..."
profiles=("$@")
services=()
declare -A selected=()
for profile in "${profiles[@]}"; do
  case "$profile" in
    core) profile_services=(app-postgres app-redis) ;;
    vector) profile_services=(chroma chroma-admin) ;;
    search) profile_services=(opensearch opensearch-dashboards) ;;
    observability) profile_services=(langfuse-postgres langfuse-redis clickhouse minio langfuse-worker langfuse-web) ;;
    tools) profile_services=(pgadmin redisinsight) ;;
    dynamodb) profile_services=(dynamodb-local dynamodb-admin) ;;
    inference) profile_services=(ollama-llm ollama-embedding) ;;
    *) die "unknown profile: $profile" ;;
  esac
  [[ -z "${selected[$profile]+x}" ]] || die "duplicate profile: $profile"
  selected[$profile]=1
  services+=("${profile_services[@]}")
done
if [[ -n "${selected[tools]+x}" && -z "${selected[core]+x}" ]]; then
  die "tools requires core"
fi

command -v "$jq_bin" >/dev/null 2>&1 || die "required command is unavailable: jq"
ps_output=$(bash "$compose_script" "${profiles[@]}" -- ps --all --format json) ||
  die "could not read Compose container health"
ps_json=$("$jq_bin" -c -s '[.[] | if type == "array" then .[] else . end]' <<<"$ps_output") ||
  die "Compose returned invalid JSON health data"
for service in "${services[@]}"; do
  if ! "$jq_bin" -e --arg service "$service" \
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

catalog_model() {
  local key=$1
  awk -v wanted="$key" '
    index($0, wanted "=") == 1 {
      count++
      value = substr($0, length(wanted) + 2)
      if (value !~ /^[A-Za-z0-9][A-Za-z0-9._\/-]*:[A-Za-z0-9][A-Za-z0-9._-]*$/) invalid=1
    }
    END {
      if (count != 1 || invalid) exit 1
      print value
    }
  ' "$versions_env" 2>/dev/null
}

verify_gpu_device_request() {
  local service=$1
  local container_id
  container_id=$(bash "$compose_script" "${profiles[@]}" -- ps --quiet "$service") ||
    die "could not identify Compose container for GPU validation: $service"
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] ||
    die "could not identify exactly one Compose container for GPU validation: $service"
  if ! "$docker_bin" inspect --format '{{json .HostConfig.DeviceRequests}}' \
      "$container_id" |
    "$jq_bin" -e '
      any(.[]?;
        (.Driver == "nvidia") and
        ((.Count // 0) == -1) and
        any(.Capabilities[]?; index("gpu") != null))
    ' >/dev/null 2>&1; then
    die "container did not request all NVIDIA GPU devices: $service"
  fi
}

verify_resident_model() {
  local endpoint=$1
  local model=$2
  if ! "$curl_bin" --fail --silent --show-error --max-time 120 "$endpoint" |
    "$jq_bin" -e --arg model "$model" '
      (.models | type == "array") and
      (.models | length == 1) and
      (.models[0] |
        ((.name == $model or .model == $model) and
         (.size | type == "number") and (.size > 0) and
         (.size_vram | type == "number") and (.size_vram > 0) and
         (.size_vram == .size)))
    ' >/dev/null 2>&1; then
    die "approved Ollama model is not exclusively and fully resident in VRAM"
  fi
}

verify_generation() {
  local timeout=$1
  local phase=$2
  if ! "$jq_bin" -n --arg model "$llm_model" \
      '{model: $model, prompt: "healthcheck", stream: false, options: {num_predict: 1}, keep_alive: "5m"}' |
    "$curl_bin" --fail --silent --show-error --max-time "$timeout" \
      --header 'Content-Type: application/json' --data-binary @- \
      http://127.0.0.1:11440/api/generate |
    "$jq_bin" -e \
      'type == "object" and (.response | type == "string") and (.done == true)' \
      >/dev/null 2>&1; then
    die "bounded Ollama $phase generation failed"
  fi
}

if [[ -n "${selected[core]+x}" ]]; then
  compose_exec app-postgres sh -c 'exec pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null
  compose_exec app-redis sh -c 'REDISCLI_AUTH="$APP_REDIS_PASSWORD" exec redis-cli ping' >/dev/null
fi
if [[ -n "${selected[vector]+x}" ]]; then
  http_get http://127.0.0.1:18000/api/v2/heartbeat
  http_get http://127.0.0.1:18001
fi
if [[ -n "${selected[dynamodb]+x}" ]]; then
  dynamodb_check="const { DynamoDBClient, ListTablesCommand } = require('@aws-sdk/client-dynamodb'); const client = new DynamoDBClient({ endpoint: process.env.DYNAMO_ENDPOINT, region: process.env.AWS_REGION, credentials: { accessKeyId: process.env.AWS_ACCESS_KEY_ID, secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY } }); client.send(new ListTablesCommand({ Limit: 1 })).then(() => client.destroy()).catch(error => { console.error(error); client.destroy(); process.exit(1); });"
  compose_exec dynamodb-admin node -e "$dynamodb_check" >/dev/null
  http_get http://127.0.0.1:18003
fi
if [[ -n "${selected[inference]+x}" ]]; then
  llm_model=$(catalog_model OLLAMA_LLM_MODEL) ||
    die "OLLAMA_LLM_MODEL is missing or malformed in versions.env"
  embedding_model=$(catalog_model OLLAMA_EMBEDDING_MODEL) ||
    die "OLLAMA_EMBEDDING_MODEL is missing or malformed in versions.env"

  verify_gpu_device_request ollama-llm
  verify_gpu_device_request ollama-embedding

  verify_generation 600 cold
  verify_resident_model http://127.0.0.1:11440/api/ps "$llm_model"
  verify_generation 120 warm

  if ! "$jq_bin" -n --arg model "$embedding_model" \
      '{model: $model, input: "healthcheck", keep_alive: "5m"}' |
    "$curl_bin" --fail --silent --show-error --max-time 120 \
      --header 'Content-Type: application/json' --data-binary @- \
      http://127.0.0.1:11441/api/embed |
    "$jq_bin" -e \
      'type == "object" and (.embeddings | type == "array")' \
      >/dev/null 2>&1; then
    die "bounded Ollama embedding failed"
  fi
  verify_resident_model http://127.0.0.1:11441/api/ps "$embedding_model"

  compute_memory=$("$nvidia_smi_bin" \
    --query-compute-apps=used_gpu_memory \
    --format=csv,noheader,nounits 2>/dev/null) ||
    die "could not read host GPU compute memory"
  if ! awk '
    /^[0-9]+$/ { seen=1; if ($1 + 0 > 0) positive=1; next }
    { invalid=1 }
    END { exit !(seen && positive && !invalid) }
  ' <<<"$compute_memory"; then
    die "host GPU compute memory is not positive while approved models are loaded"
  fi
  unset llm_model embedding_model compute_memory
fi
if [[ -n "${selected[search]+x}" ]]; then
  opensearch_password=$(env_value OPENSEARCH_INITIAL_ADMIN_PASSWORD) ||
    die "OPENSEARCH_INITIAL_ADMIN_PASSWORD is missing from the runtime environment"
  escaped_password=${opensearch_password//\\/\\\\}
  escaped_password=${escaped_password//\"/\\\"}
  printf 'user = "admin:%s"\n' "$escaped_password" |
    "$curl_bin" --config - --fail --silent --show-error --insecure --max-time 10 \
      https://127.0.0.1:9200/_cluster/health >/dev/null
  printf 'user = "admin:%s"\n' "$escaped_password" |
    "$curl_bin" --config - --fail --silent --show-error --max-time 10 \
      http://127.0.0.1:5601/api/status >/dev/null
  unset opensearch_password escaped_password
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
