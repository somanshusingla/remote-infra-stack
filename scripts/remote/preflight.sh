#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

is_t4_gpu_name() {
  case "$1" in
    "Tesla T4"|"NVIDIA T4") return 0 ;;
    *) return 1 ;;
  esac
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
release_dir=${STACK_RELEASE_DIR:-$(cd -- "$script_dir/../.." && pwd -P)}
stack_root=${STACK_ROOT:-$(cd -- "$release_dir/../.." && pwd -P)}
compose_script=${STACK_COMPOSE_SCRIPT:-$script_dir/compose.sh}
versions_env=${STACK_VERSIONS_ENV_FILE:-$release_dir/versions.env}

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

(($# > 0)) || die "at least one profile is required"
profiles=("$@")
declare -A selected=()
for profile in "${profiles[@]}"; do
  profile_services "$profile" >/dev/null || die "unknown profile: $profile"
  [[ -z "${selected[$profile]+x}" ]] || die "duplicate profile: $profile"
  selected[$profile]=1
done
if [[ -n "${selected[tools]+x}" && -z "${selected[core]+x}" ]]; then
  die "tools requires core"
fi

compose() {
  bash "$compose_script" "$@"
}

df_bin=${DF_BIN:-df}
jq_bin=${JQ_BIN:-jq}
docker_bin=${DOCKER_BIN:-docker}
nvidia_smi_bin=${NVIDIA_SMI_BIN:-nvidia-smi}
sysctl_bin=${SYSCTL_BIN:-sysctl}
meminfo_file=${MEMINFO_FILE:-/proc/meminfo}
command -v "$jq_bin" >/dev/null 2>&1 || die "required command is unavailable: jq"

if ! ip_forward=$("$sysctl_bin" -n net.ipv4.ip_forward 2>/dev/null); then
  die "net.ipv4.ip_forward must equal 1; could not read the kernel setting"
fi
[[ "$ip_forward" == 1 ]] ||
  die "net.ipv4.ip_forward must equal 1; found ${ip_forward:-empty}"

free_bytes=$("$df_bin" --output=avail -B1 "$stack_root" 2>/dev/null | awk 'NR > 1 { value=$1 } END { print value }') ||
  die "could not determine free disk space for $stack_root"
[[ "$free_bytes" =~ ^[0-9]+$ ]] || die "could not determine free disk space for $stack_root"
if (( free_bytes < 10 * 1024 * 1024 * 1024 )); then
  die "less than 10 GiB of disk space is available under $stack_root"
elif (( free_bytes < 20 * 1024 * 1024 * 1024 )); then
  printf 'WARNING: less than 20 GiB of disk space is available under %s\n' "$stack_root" >&2
fi

if [[ -n "${selected[inference]+x}" ]]; then
  cuda_image=$(awk -v wanted=NVIDIA_CUDA_IMAGE '
    index($0, wanted "=") == 1 {
      count++
      value = substr($0, length(wanted) + 2)
      if (value !~ /^[A-Za-z0-9][A-Za-z0-9._:\/-]*@sha256:[a-f0-9]{64}$/) invalid=1
    }
    END {
      if (count != 1 || invalid) exit 1
      print value
    }
  ' "$versions_env" 2>/dev/null) ||
    die "NVIDIA_CUDA_IMAGE is missing or malformed in versions.env"

  host_gpu_output_temp=$(mktemp) ||
    die "could not create host GPU validation temporary file"
  trap 'rm -f -- "$host_gpu_output_temp"' EXIT
  if ! "$nvidia_smi_bin" --query-gpu=name --format=csv,noheader \
    >"$host_gpu_output_temp" 2>/dev/null; then
    die "host GPU validation failed"
  fi
  mapfile -t host_gpu_names <"$host_gpu_output_temp"
  rm -f -- "$host_gpu_output_temp"
  trap - EXIT
  [[ ${#host_gpu_names[@]} -eq 1 ]] ||
    die "host GPU validation requires exactly one NVIDIA T4; nvidia-smi may label it Tesla T4"
  is_t4_gpu_name "${host_gpu_names[0]}" ||
    die "host GPU validation requires exactly one NVIDIA T4; nvidia-smi may label it Tesla T4"

  container_gpu_output_temp=$(mktemp) ||
    die "could not create container GPU validation temporary file"
  trap 'rm -f -- "$container_gpu_output_temp"' EXIT
  if ! "$docker_bin" run --rm --gpus all "$cuda_image" \
    nvidia-smi --query-gpu=name --format=csv,noheader \
    >"$container_gpu_output_temp" 2>/dev/null; then
    die "container GPU validation failed"
  fi
  mapfile -t container_gpu_names <"$container_gpu_output_temp"
  rm -f -- "$container_gpu_output_temp"
  trap - EXIT
  [[ ${#container_gpu_names[@]} -eq 1 ]] ||
    die "container GPU validation requires exactly one NVIDIA T4; nvidia-smi may label it Tesla T4"
  is_t4_gpu_name "${container_gpu_names[0]}" ||
    die "container GPU validation requires exactly one NVIDIA T4; nvidia-smi may label it Tesla T4"

  docker_root=$("$docker_bin" info --format '{{.DockerRootDir}}' 2>/dev/null) ||
    die "could not determine Docker storage root"
  [[ -n "$docker_root" && "$docker_root" != *$'\n'* ]] ||
    die "could not determine Docker storage root"
  docker_free=$("$df_bin" --output=avail -B1 "$docker_root" 2>/dev/null | awk 'NR > 1 { value=$1 } END { print value }') ||
    die "could not determine free space on Docker storage root \"$docker_root\""
  [[ "$docker_free" =~ ^[0-9]+$ ]] ||
    die "could not determine free space on Docker storage root \"$docker_root\""
  (( docker_free >= 20 * 1024 * 1024 * 1024 )) ||
    die "less than 20 GiB is available on the Docker storage filesystem for inference"
fi

model=$(compose "${profiles[@]}" -- config --format json) ||
  die "Compose configuration rendering failed"
selected_services=()
for profile in "${profiles[@]}"; do
  mapfile -t profile_service_names < <(profile_services "$profile")
  selected_services+=("${profile_service_names[@]}")
done
required_bytes=$("$jq_bin" --args \
  '[.services[$ARGS.positional[]].mem_limit // 0 | tonumber] | add // 0' \
  "${selected_services[@]}" <<<"$model") || die "could not total Compose memory limits"
[[ "$required_bytes" =~ ^[0-9]+$ ]] || die "could not total Compose memory limits"
required_bytes=$((required_bytes + 2 * 1024 * 1024 * 1024))
if [[ -r "$meminfo_file" ]]; then
  available_kib=$(awk '$1 == "MemTotal:" { print $2; exit }' "$meminfo_file")
  if [[ "$available_kib" =~ ^[0-9]+$ ]] && (( required_bytes > available_kib * 1024 )); then
    printf 'WARNING: selected service memory limits plus 2 GiB host overhead exceed host memory\n' >&2
  fi
else
  printf 'WARNING: memory availability could not be read from %s\n' "$meminfo_file" >&2
fi
