#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
# shellcheck source=scripts/lib/common.sh
source "$script_dir/lib/common.sh"

load_remote_env "${STACK_REMOTE_ENV:-$repo_root/remote.env}"
command -v ssh >/dev/null 2>&1 || common_die "required command is unavailable: ssh"

action=${1:-}
[[ -n "$action" ]] || common_die "usage: stack.sh up|stop profiles... | down | status | logs target | destroy"
shift
case "$action" in
  up|stop)
    validate_profiles "$@"
    ;;
  down|status)
    (($# == 0)) || common_die "$action does not accept arguments"
    ;;
  logs)
    (($# == 1)) || common_die "logs requires one profile or service target"
    case "$1" in
      core|vector|search|observability|tools|\
        app-postgres|app-redis|chroma|opensearch|opensearch-dashboards|\
        langfuse-postgres|langfuse-redis|clickhouse|minio|langfuse-worker|\
        langfuse-web|pgadmin|redisinsight)
        ;;
      *) common_die "unknown log target: $1" ;;
    esac
    ;;
  destroy)
    (($# == 0)) || common_die "destroy does not accept command-line confirmation tokens"
    printf 'Type the configured remote target %s to continue: ' "$REMOTE_HOST" >&2
    IFS= read -r confirmed_host || common_die "destroy confirmation was cancelled"
    confirmed_host=${confirmed_host%$'\r'}
    [[ "$confirmed_host" == "$REMOTE_HOST" ]] || common_die "remote target confirmation did not match"
    printf 'Permanent data loss: type DESTROY-remote-infra-stack to continue: ' >&2
    IFS= read -r destroy_token || common_die "destroy confirmation was cancelled"
    destroy_token=${destroy_token%$'\r'}
    [[ "$destroy_token" == DESTROY-remote-infra-stack ]] || common_die "destroy token did not match"
    set -- remote-infra-stack "$destroy_token"
    ;;
  *)
    common_die "unsupported stack action: $action"
    ;;
esac

run_ssh bash "$REMOTE_ROOT/current/scripts/remote/stack.sh" "$action" "$@"
