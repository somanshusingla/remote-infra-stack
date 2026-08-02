#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
# shellcheck source=scripts/lib/common.sh
source "$script_dir/lib/common.sh"

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
run_ssh sudo bash "$remote_bootstrap" --install
run_ssh rm -f -- "$remote_bootstrap"
remote_cleanup=false

printf 'Remote host bootstrap completed for %s.\n' "$REMOTE_HOST"
