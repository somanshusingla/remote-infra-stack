#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
# shellcheck source=scripts/lib/common.sh
source "$script_dir/lib/common.sh"

load_remote_env "${STACK_REMOTE_ENV:-$repo_root/remote.env}"
command -v ssh >/dev/null 2>&1 || common_die "required command is unavailable: ssh"
command -v scp >/dev/null 2>&1 || common_die "required command is unavailable: scp"

bootstrap_source=$script_dir/remote/bootstrap-host.sh
[[ -f "$bootstrap_source" ]] || common_die "remote bootstrap script is missing: $bootstrap_source"
incoming_name="remote-infra-stack-bootstrap-$(date -u +%Y%m%dT%H%M%SZ)-$$.sh"

scp "${scp_args[@]}" "$bootstrap_source" "$ssh_target:$incoming_name"
ssh "${ssh_args[@]}" "$ssh_target" sudo bash "$incoming_name" --install
ssh "${ssh_args[@]}" "$ssh_target" mkdir -p \
  "$REMOTE_ROOT/incoming" "$REMOTE_ROOT/releases" "$REMOTE_ROOT/runtime"

printf 'Remote host bootstrap completed for %s.\n' "$REMOTE_HOST"
