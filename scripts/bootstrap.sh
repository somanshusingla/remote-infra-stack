#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
# shellcheck source=scripts/lib/common.sh
source "$script_dir/lib/common.sh"

gpu_mode=false
case $# in
  0) ;;
  1) [[ $1 == --gpu ]] || common_die "usage: bootstrap.sh [--gpu]"; gpu_mode=true ;;
  *) common_die "usage: bootstrap.sh [--gpu]" ;;
esac

cuda_image=
if [[ "$gpu_mode" == true ]]; then
  versions_env=$repo_root/versions.env
  [[ -f "$versions_env" ]] || common_die "NVIDIA_CUDA_IMAGE catalog is missing: $versions_env"
  cuda_image_count=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    case "$line" in
      NVIDIA_CUDA_IMAGE=*)
        ((cuda_image_count += 1))
        cuda_image=${line#NVIDIA_CUDA_IMAGE=}
        ;;
      NVIDIA_CUDA_IMAGE*)
        common_die "invalid NVIDIA_CUDA_IMAGE assignment in versions.env"
        ;;
    esac
  done <"$versions_env"
  ((cuda_image_count == 1)) || common_die "versions.env must contain exactly one NVIDIA_CUDA_IMAGE assignment"
  [[ "$cuda_image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ && "$cuda_image" != *:latest* ]] ||
    common_die "NVIDIA_CUDA_IMAGE must be a single non-latest digest-pinned reference"
fi

load_remote_env "${STACK_REMOTE_ENV:-$repo_root/remote.env}"
for command_name in ssh scp mktemp basename rmdir; do
  command -v "$command_name" >/dev/null 2>&1 || common_die "required command is unavailable: $command_name"
done

bootstrap_source=$script_dir/remote/bootstrap-host.sh
[[ -f "$bootstrap_source" ]] || common_die "remote bootstrap script is missing: $bootstrap_source"
token_directory=$(mktemp -d "${TMPDIR:-/tmp}/remote-infra-stack-bootstrap.XXXXXXXX") ||
  common_die "could not create a unique bootstrap operation"
operation_id=${token_directory##*.}
remote_bootstrap=$REMOTE_ROOT/incoming/bootstrap-$operation_id.sh
remote_cleanup=false
cleanup() {
  local status=$?
  trap - EXIT
  if [[ "$remote_cleanup" == true ]]; then
    set +e
    run_ssh rm -f -- "$remote_bootstrap" >/dev/null 2>&1
    set -e
  fi
  rmdir -- "$token_directory" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT

run_ssh mkdir -p -- "$REMOTE_ROOT/incoming" "$REMOTE_ROOT/releases" "$REMOTE_ROOT/runtime"
remote_cleanup=true
scp "${scp_args[@]}" "$bootstrap_source" "$ssh_target:$remote_bootstrap"
if [[ "$gpu_mode" == true ]]; then
  run_ssh sudo bash "$remote_bootstrap" --install --gpu --cuda-image "$cuda_image"
else
  run_ssh sudo bash "$remote_bootstrap" --install
fi
run_ssh rm -f -- "$remote_bootstrap"
remote_cleanup=false

printf 'Remote host bootstrap completed for %s.\n' "$REMOTE_HOST"
