#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(git -C "$script_dir/.." rev-parse --show-toplevel 2>/dev/null) || {
  printf 'ERROR: deploy.sh must run from a Git checkout\n' >&2
  exit 1
}
# shellcheck source=scripts/lib/common.sh
source "$script_dir/lib/common.sh"

load_remote_env "${STACK_REMOTE_ENV:-$repo_root/remote.env}"
validate_profiles "$@"
for command_name in git ssh scp date awk; do
  command -v "$command_name" >/dev/null 2>&1 || common_die "required command is unavailable: $command_name"
done
if command -v sha256sum >/dev/null 2>&1; then
  checksum_command=sha256sum
elif command -v shasum >/dev/null 2>&1; then
  checksum_command=shasum
else
  common_die "required command is unavailable: sha256sum or shasum"
fi

require_clean_git_head "$repo_root"
validate_stack_env "$repo_root/.env" "$repo_root/.env.example"

short_sha=$(git -C "$repo_root" rev-parse --short=12 HEAD)
release_name="$(date -u +%Y%m%dT%H%M%SZ)-$short_sha"
artifact_dir=$repo_root/.artifacts
archive=$artifact_dir/$release_name.tar.gz
checksum=$archive.sha256
created_artifact_dir=false
if [[ ! -d "$artifact_dir" ]]; then
  mkdir -- "$artifact_dir"
  created_artifact_dir=true
fi
cleanup() {
  rm -f -- "$archive" "$checksum"
  if [[ "$created_artifact_dir" == true ]]; then
    rmdir -- "$artifact_dir" 2>/dev/null || true
  fi
}
trap cleanup EXIT

git -C "$repo_root" archive --format=tar.gz --output="$archive" HEAD
if [[ "$checksum_command" == sha256sum ]]; then
  digest=$(sha256sum -- "$archive" | awk '{ print $1 }')
else
  digest=$(shasum -a 256 -- "$archive" | awk '{ print $1 }')
fi
printf '%s  %s\n' "$digest" "$(basename -- "$archive")" >"$checksum"

remote_incoming=$REMOTE_ROOT/incoming
scp "${scp_args[@]}" "$archive" "$checksum" "$script_dir/remote/deploy-release.sh" \
  "$ssh_target:$remote_incoming/"
scp "${scp_args[@]}" "$repo_root/.env" "$ssh_target:$REMOTE_ROOT/runtime/.env"

profiles_csv=$(IFS=,; printf '%s' "$*")
ssh "${ssh_args[@]}" "$ssh_target" bash "$remote_incoming/deploy-release.sh" \
  --root "$REMOTE_ROOT" \
  --archive "$remote_incoming/$(basename -- "$archive")" \
  --checksum "$remote_incoming/$(basename -- "$checksum")" \
  --profiles "$profiles_csv"

printf 'Deployment completed for release %s.\n' "$release_name"
