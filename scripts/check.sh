#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
# shellcheck source=scripts/lib/common.sh
source "$script_dir/lib/common.sh"

load_remote_env "${STACK_REMOTE_ENV:-$repo_root/remote.env}"
validate_profiles "$@"
for command_name in \
  bash git ssh scp date awk mktemp base64 tr wc cp chmod mkdir rm rmdir basename dirname uname; do
  command -v "$command_name" >/dev/null 2>&1 || common_die "required command is unavailable: $command_name"
done
if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
  common_die "required command is unavailable: sha256sum or shasum"
fi
if [[ -n "$REMOTE_IDENTITY_FILE" && ! -r "$REMOTE_IDENTITY_FILE" ]]; then
  common_die "configured SSH identity file is not readable: $REMOTE_IDENTITY_FILE"
fi

validate_stack_env "$repo_root/.env" "$repo_root/.env.example"
require_clean_git_head "$repo_root"

syntax_targets=("$repo_root"/scripts/*.sh "$repo_root"/scripts/lib/*.sh "$repo_root"/scripts/remote/*.sh)
bash -n "${syntax_targets[@]}"

opensearch_config=$repo_root/config/opensearch/opensearch.yml
opensearch_entrypoint=$repo_root/config/opensearch/docker-entrypoint.sh
for transport_file in "$opensearch_config" "$opensearch_entrypoint"; do
  [[ -f "$transport_file" ]] || common_die "verified OpenSearch input is unavailable: $transport_file"
  transport_size=$(wc -c <"$transport_file")
  ((transport_size > 0 && transport_size <= 65536)) ||
    common_die "verified OpenSearch input exceeds the 64 KiB transport boundary"
done
STACK_OPENSEARCH_CONFIG_B64=$(base64_file_no_wrap "$opensearch_config")
STACK_OPENSEARCH_ENTRYPOINT_B64=$(base64_file_no_wrap "$opensearch_entrypoint")
export STACK_OPENSEARCH_CONFIG_B64 STACK_OPENSEARCH_ENTRYPOINT_B64

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  compose_args=(
    compose
    --env-file "$repo_root/versions.env"
    --env-file "$repo_root/.env"
    --project-directory "$repo_root"
    --file "$repo_root/compose.yaml"
  )
  for profile in "$@"; do
    compose_args+=(--profile "$profile")
  done
  docker "${compose_args[@]}" config --quiet || common_die "local Docker Compose configuration rendering failed"
  if ! docker info >/dev/null 2>&1; then
    printf 'WARNING: local Docker daemon is unavailable; remote validation remains authoritative.\n' >&2
  fi
else
  printf 'WARNING: local Docker Compose is unavailable; remote validation remains authoritative.\n' >&2
fi

printf 'Local checks passed for profiles:'
printf ' %s' "$@"
printf '\n'
