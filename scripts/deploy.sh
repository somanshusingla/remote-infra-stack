#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
git_root=$(git -C "$script_dir/.." rev-parse --show-toplevel 2>/dev/null) || {
  printf 'ERROR: deploy.sh must run from a Git checkout\n' >&2
  exit 1
}
repo_root=$(cd -- "$git_root" && pwd -P) || {
  printf 'ERROR: cannot resolve the Git checkout root\n' >&2
  exit 1
}
# shellcheck source=scripts/lib/common.sh
source "$script_dir/lib/common.sh"

remote_env_file=${STACK_REMOTE_ENV:-$repo_root/remote.env}
load_remote_env "$remote_env_file"
validate_profiles "$@"
for command_name in git ssh scp date awk mktemp cp chmod mkdir rm rmdir basename uname; do
  command -v "$command_name" >/dev/null 2>&1 || common_die "required command is unavailable: $command_name"
done
if command -v sha256sum >/dev/null 2>&1; then
  checksum_command=sha256sum
elif command -v shasum >/dev/null 2>&1; then
  checksum_command=shasum
else
  common_die "required command is unavailable: sha256sum or shasum"
fi

# A symlink at this ignored workspace boundary is otherwise reported by Git as
# generic dirt. Reject it explicitly before the clean-tree gate, then recheck it
# immediately before creating the private staging directory below.
artifact_parent=$repo_root/.artifacts
if [[ -e "$artifact_parent" || -L "$artifact_parent" ]]; then
  [[ -d "$artifact_parent" && ! -L "$artifact_parent" && -O "$artifact_parent" ]] ||
    common_die ".artifacts must be a real non-symlink directory owned by the current user"
fi

require_clean_git_head "$repo_root"
reject_tracked_file "$repo_root" "$repo_root/.env" ".env"
reject_tracked_file "$repo_root" "$remote_env_file" "remote.env"
validate_stack_env "$repo_root/.env" "$repo_root/.env.example"
head_oid=$(git -C "$repo_root" rev-parse --verify 'HEAD^{commit}')
[[ "$head_oid" =~ ^[0-9a-fA-F]{40,64}$ ]] || common_die "could not capture the full Git HEAD object ID"

private_chmod() {
  local mode=$1 path=$2
  if ! chmod "$mode" -- "$path"; then
    case "$(uname -s)" in
      MINGW*|MSYS*|CYGWIN*) ;;
      *) common_die "could not apply private mode $mode to $path" ;;
    esac
  fi
}

created_artifact_parent=false
if [[ -e "$artifact_parent" || -L "$artifact_parent" ]]; then
  [[ -d "$artifact_parent" && ! -L "$artifact_parent" && -O "$artifact_parent" ]] ||
    common_die ".artifacts must be a real non-symlink directory owned by the current user"
else
  mkdir -- "$artifact_parent"
  created_artifact_parent=true
fi
private_chmod 0700 "$artifact_parent"
staging=$(mktemp -d "$artifact_parent/deploy.XXXXXXXX") || common_die "could not create private deployment staging"
[[ -d "$staging" && ! -L "$staging" && -O "$staging" ]] ||
  common_die "deployment staging must be a real non-symlink directory owned by the current user"
private_chmod 0700 "$staging"
remote_cleanup_paths=()
cleanup() {
  local status=$?
  trap - EXIT
  if ((${#remote_cleanup_paths[@]} > 0)); then
    run_ssh rm -f -- "${remote_cleanup_paths[@]}" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$staging"
  if [[ "$created_artifact_parent" == true ]]; then
    rmdir -- "$artifact_parent" 2>/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT

operation_id=${staging##*/deploy.}
short_sha=${head_oid:0:12}
release_name="$(date -u +%Y%m%dT%H%M%SZ)-$short_sha-$operation_id"
archive=$staging/$release_name.tar.gz
checksum=$archive.sha256
runtime_env_snapshot=$staging/runtime-env-$operation_id.env
receiver_snapshot=$staging/deploy-release-$operation_id.sh

cp -- "$repo_root/.env" "$runtime_env_snapshot"
private_chmod 0600 "$runtime_env_snapshot"
git -C "$repo_root" archive --format=tar.gz --output="$archive" "$head_oid"
git -C "$repo_root" cat-file blob "$head_oid:scripts/remote/deploy-release.sh" >"$receiver_snapshot"
private_chmod 0700 "$receiver_snapshot"
if [[ "$checksum_command" == sha256sum ]]; then
  digest=$(sha256sum -- "$archive" | awk '{ print $1 }')
else
  digest=$(shasum -a 256 -- "$archive" | awk '{ print $1 }')
fi
printf '%s  %s\n' "$digest" "$(basename -- "$archive")" >"$checksum"
private_chmod 0600 "$archive"
private_chmod 0600 "$checksum"

current_head=$(git -C "$repo_root" rev-parse --verify 'HEAD^{commit}')
[[ "$current_head" == "$head_oid" ]] || common_die "HEAD changed during deployment preparation"
require_clean_git_head "$repo_root"
reject_tracked_file "$repo_root" "$repo_root/.env" ".env"
reject_tracked_file "$repo_root" "$remote_env_file" "remote.env"

remote_incoming=$REMOTE_ROOT/incoming
remote_archive=$remote_incoming/$(basename -- "$archive")
remote_checksum=$remote_incoming/$(basename -- "$checksum")
remote_runtime_env=$remote_incoming/$(basename -- "$runtime_env_snapshot")
remote_receiver=$remote_incoming/$(basename -- "$receiver_snapshot")
remote_cleanup_paths=(
  "$remote_archive" "$remote_checksum" "$remote_runtime_env" "$remote_receiver"
)
scp "${scp_args[@]}" "$archive" "$checksum" "$runtime_env_snapshot" "$receiver_snapshot" \
  "$ssh_target:$remote_incoming/"

profiles_csv=$(IFS=,; printf '%s' "$*")
run_ssh bash "$remote_receiver" \
  --root "$REMOTE_ROOT" \
  --archive "$remote_archive" \
  --checksum "$remote_checksum" \
  --env "$remote_runtime_env" \
  --profiles "$profiles_csv"

printf 'Deployment completed for release %s.\n' "$release_name"
