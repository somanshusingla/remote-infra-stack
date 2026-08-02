#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

root=
archive=
checksum=
profiles_csv=
while (($#)); do
  case "$1" in
    --root|--archive|--checksum|--profiles)
      (($# >= 2)) || die "missing value for $1"
      option=$1
      value=$2
      shift 2
      case "$option" in
        --root) [[ -z "$root" ]] || die "duplicate option: --root"; root=$value ;;
        --archive) [[ -z "$archive" ]] || die "duplicate option: --archive"; archive=$value ;;
        --checksum) [[ -z "$checksum" ]] || die "duplicate option: --checksum"; checksum=$value ;;
        --profiles) [[ -z "$profiles_csv" ]] || die "duplicate option: --profiles"; profiles_csv=$value ;;
      esac
      ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$root" && -n "$archive" && -n "$checksum" && -n "$profiles_csv" ]] ||
  die "usage: deploy-release.sh --root PATH --archive PATH --checksum PATH --profiles CSV"

IFS=, read -r -a profiles <<<"$profiles_csv"
((${#profiles[@]} > 0)) || die "at least one profile is required"
declare -A selected=()
for profile in "${profiles[@]}"; do
  [[ -n "$profile" ]] || die "profiles must not contain empty entries"
  case "$profile" in
    core|vector|search|observability|tools) ;;
    *) die "unknown profile: $profile" ;;
  esac
  [[ -z "${selected[$profile]+x}" ]] || die "duplicate profile: $profile"
  selected[$profile]=1
done
if [[ -n "${selected[tools]+x}" && -z "${selected[core]+x}" ]]; then
  die "tools requires core"
fi
python_bin=${PYTHON_BIN:-python3}
command -v "$python_bin" >/dev/null 2>&1 || die "python3 is required for atomic release exchange"

[[ -d "$root" ]] || die "stack root must already exist as a directory"
root=$(realpath -e -- "$root")
exec {root_fd}<"$root"
root_path=/proc/self/fd/$root_fd
[[ -d "$root_path" && "$(realpath -e -- "$root_path")" == "$root" ]] ||
  die "could not hold the canonical stack root"

open_root_child() {
  local child_name=$1
  local output_variable=$2
  local child_path=$root_path/$child_name
  [[ -d "$child_path" && ! -L "$child_path" ]] ||
    die "$child_name must be a real non-symlink directory"
  local child_real
  child_real=$(realpath -e -- "$child_path")
  [[ "$child_real" == "$root/"* ]] || die "$child_name must remain below the stack root"
  local child_fd
  exec {child_fd}<"$child_path"
  local held_path=/proc/self/fd/$child_fd
  [[ -d "$held_path" && "$(realpath -e -- "$held_path")" == "$child_real" ]] ||
    die "could not hold stack directory: $child_name"
  printf -v "$output_variable" '%s' "$child_fd"
}

open_root_child runtime runtime_fd
open_root_child releases releases_fd
runtime_path=/proc/self/fd/$runtime_fd
releases_path=/proc/self/fd/$releases_fd
runtime_host=$(realpath -e -- "$runtime_path")
releases_host=$(realpath -e -- "$releases_path")
[[ "$runtime_host" == "$root/"* ]] || die "held runtime directory escaped the stack root"
[[ "$releases_host" == "$root/"* ]] || die "held releases directory escaped the stack root"

# The validated runtime directory itself is the lock object. This cannot follow or
# truncate a caller-controlled deploy.lock path and the descriptor remains open
# until the complete transaction exits.
flock -n "$runtime_fd" || die "another deployment is already in progress"

runtime_env_entry=$runtime_path/.env
[[ -f "$runtime_env_entry" && ! -L "$runtime_env_entry" ]] ||
  die "runtime/.env must be a regular non-symlink file"
runtime_env_host=$(realpath -e -- "$runtime_env_entry")
[[ "$runtime_env_host" == "$runtime_host/.env" ]] ||
  die "runtime/.env escaped the held runtime directory"
exec {runtime_env_fd}<"$runtime_env_entry"
runtime_env_held=/proc/self/fd/$runtime_env_fd
[[ -f "$runtime_env_held" && "$(realpath -e -- "$runtime_env_held")" == "$runtime_env_host" ]] ||
  die "could not hold the validated runtime environment"
chmod 0600 -- "$runtime_env_held"

inside_root() {
  local candidate=$1
  [[ "$candidate" == "$root" || "$candidate" == "$root/"* ]]
}

[[ -f "$archive" && ! -L "$archive" ]] || die "archive must be a regular non-symlink file"
[[ -f "$checksum" && ! -L "$checksum" ]] || die "checksum must be a regular non-symlink file"
archive=$(realpath -e -- "$archive")
checksum=$(realpath -e -- "$checksum")
inside_root "$archive" || die "archive path must remain below the stack root"
inside_root "$checksum" || die "checksum path must remain below the stack root"

archive_file=$(basename -- "$archive")
[[ "$archive_file" == *.tar.gz ]] || die "archive name must end in .tar.gz"
release_name=${archive_file%.tar.gz}
[[ -n "$release_name" && "$release_name" != . && "$release_name" != .. && "$release_name" != *$'\n'* ]] ||
  die "unsafe release name"

staging=$(mktemp -d "$runtime_path/.deploy-XXXXXXXX")
chmod 0700 -- "$staging"
current_temp=
cleanup() {
  if [[ -n "${staging:-}" && -d "$staging" ]]; then
    rm -rf -- "$staging"
  fi
  if [[ -n "${current_temp:-}" ]]; then
    rm -f -- "$current_temp"
  fi
}
trap cleanup EXIT

# Snapshot each untrusted input exactly once while holding the deployment lock.
# Every subsequent operation uses only these private copies.
staged_archive=$staging/archive.tar.gz
staged_checksum=$staging/archive.sha256
staged_runtime_env=$staging/runtime.env
"${CP_BIN:-cp}" -- "$runtime_env_held" "$staged_runtime_env"
"${CP_BIN:-cp}" -- "$archive" "$staged_archive"
"${CP_BIN:-cp}" -- "$checksum" "$staged_checksum"
chmod 0600 -- "$staged_archive" "$staged_checksum" "$staged_runtime_env"

mapfile -t checksum_lines <"$staged_checksum"
((${#checksum_lines[@]} == 1)) || die "checksum file must contain exactly one record"
checksum_line=${checksum_lines[0]}
if [[ ! "$checksum_line" =~ ^([[:xdigit:]]{64})[[:space:]][[:space:]](.+)$ ]]; then
  die "checksum file has an invalid format"
fi
expected_digest=${BASH_REMATCH[1],,}
[[ "${BASH_REMATCH[2]}" == "$archive_file" ]] ||
  die "checksum record must name the archive basename exactly"
actual_digest=$("${SHA256SUM_BIN:-sha256sum}" -- "$staged_archive" | awk '{ print $1 }')
[[ "$actual_digest" =~ ^[[:xdigit:]]{64}$ ]] || die "could not calculate staged archive digest"
actual_digest=${actual_digest,,}
[[ "$actual_digest" == "$expected_digest" ]] || die "archive checksum verification failed"

member_list=$staging/members.list
member_types=$staging/members.verbose
tar --list --gzip --file "$staged_archive" --quoting-style=escape >"$member_list" ||
  die "archive listing failed"
tar --list --verbose --gzip --file "$staged_archive" --quoting-style=escape >"$member_types" ||
  die "archive metadata listing failed"
while IFS= read -r metadata; do
  case "${metadata:0:1}" in
    -|d) ;;
    *) die "archive contains a non-regular member" ;;
  esac
done <"$member_types"

while IFS= read -r member; do
  [[ -n "$member" && "$member" != /* ]] || die "archive contains an unsafe member path"
  [[ "$member" != *'\'* ]] || die "archive member names must not contain escapes or control characters"
  if printf '%s' "$member" | LC_ALL=C grep -q '[[:cntrl:]]'; then
    die "archive member names must not contain control characters"
  fi
  normalized=${member%/}
  [[ -n "$normalized" ]] || die "archive contains an empty member name"
  IFS=/ read -r -a components <<<"$normalized"
  for component in "${components[@]}"; do
    [[ "$component" != .. ]] || die "archive contains parent traversal"
  done
  basename_part=${normalized##*/}
  case "$basename_part" in
    .successful|.release-digest) die "archive contains a reserved deployer marker" ;;
  esac
done <"$member_list"

extracted=$staging/extracted
mkdir -- "$extracted"
tar --extract --gzip --file "$staged_archive" --directory "$extracted" \
  --no-same-owner --no-same-permissions --delay-directory-restore ||
  die "archive extraction failed"
for required in \
  compose.yaml versions.env scripts/remote/compose.sh scripts/remote/health.sh \
  config/opensearch/opensearch.yml config/opensearch/docker-entrypoint.sh; do
  [[ -f "$extracted/$required" && ! -L "$extracted/$required" ]] ||
    die "release is missing regular file: $required"
done

open_release_leaf() {
  local relative=$1
  local output_variable=$2
  local source=$extracted/$relative
  local source_identity
  source_identity=$(stat -Lc '%d:%i' -- "$source")
  local leaf_fd
  exec {leaf_fd}<"$source"
  local held=/proc/self/fd/$leaf_fd
  [[ -f "$held" && "$(stat -Lc '%d:%i' -- "$held")" == "$source_identity" ]] ||
    die "could not hold verified release file: $relative"
  printf -v "$output_variable" '%s' "$leaf_fd"
}

open_release_leaf scripts/remote/compose.sh compose_script_fd
open_release_leaf scripts/remote/health.sh health_script_fd
open_release_leaf compose.yaml compose_file_fd
open_release_leaf versions.env versions_env_fd
open_release_leaf config/opensearch/opensearch.yml opensearch_config_fd
open_release_leaf config/opensearch/docker-entrypoint.sh opensearch_entrypoint_fd
(umask 077; printf '%s\n' "$actual_digest" >"$extracted/.release-digest")

release_dir=$releases_path/$release_name
move_bin=${MV_BIN:-mv}
if [[ -e "$release_dir" || -L "$release_dir" ]]; then
  [[ -d "$release_dir" && ! -L "$release_dir" ]] || die "release collision is not a real directory"
  digest_marker=$release_dir/.release-digest
  [[ -f "$digest_marker" && ! -L "$digest_marker" ]] ||
    die "release collision lacks a trusted digest marker"
  stored_digest=$(<"$digest_marker")
  [[ "$stored_digest" == "$actual_digest" ]] || die "release collision has a different archive digest"
  if [[ -e "$release_dir/.successful" || -L "$release_dir/.successful" ]]; then
    [[ -f "$release_dir/.successful" && ! -L "$release_dir/.successful" ]] ||
      die "successful release collision has an unsafe marker"
    die "successful release collision is preserved and cannot be redeployed"
  fi
  if [[ -L "$root_path/current" ]] &&
    [[ "$(realpath -m -- "$root_path/current")" == "$(realpath -e -- "$release_dir")" ]]; then
    die "current release collision is preserved and cannot be replaced"
  fi
  retained_identity=$(stat -Lc '%d:%i' -- "$release_dir")
  fresh_identity=$(stat -Lc '%d:%i' -- "$extracted")
  if ! "$python_bin" -c '
import ctypes
import os
import sys

AT_FDCWD = -100
RENAME_EXCHANGE = 2
try:
    renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    renameat2.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        AT_FDCWD, os.fsencode(sys.argv[1]),
        AT_FDCWD, os.fsencode(sys.argv[2]),
        RENAME_EXCHANGE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
except (AttributeError, OSError) as error:
    print(f"atomic release exchange unavailable: {error}", file=sys.stderr)
    raise SystemExit(1)
' "$release_dir" "$extracted"; then
    die "could not atomically exchange the retained release"
  fi
  [[ "$(stat -Lc '%d:%i' -- "$release_dir")" == "$fresh_identity" ]] ||
    die "atomic exchange did not install the freshly verified release"
  [[ "$(stat -Lc '%d:%i' -- "$extracted")" == "$retained_identity" ]] ||
    die "atomic exchange did not preserve the displaced retained release"
  rm -rf -- "$extracted"
else
  "$move_bin" -T -- "$extracted" "$release_dir"
fi

if [[ -e "$root_path/current" && ! -L "$root_path/current" ]]; then
  die "current must be absent or a symbolic link"
fi
if [[ -e "$release_dir/.successful" || -L "$release_dir/.successful" ]]; then
  [[ -f "$release_dir/.successful" && ! -L "$release_dir/.successful" ]] ||
    die "successful marker must be a regular file"
fi

exec {release_fd}<"$release_dir"
release_held=/proc/self/fd/$release_fd
[[ -d "$release_held" ]] || die "could not hold the freshly verified release"
release_host=$(realpath -e -- "$release_held")
[[ "$release_host" == "$releases_host/$release_name" ]] ||
  die "release host path does not match the held releases directory"
release_identity=$(stat -Lc '%d:%i' -- "$release_held")
verify_release_identity() {
  local host_identity
  [[ -d "$release_host" && ! -L "$release_host" ]] ||
    die "canonical release path no longer names a real directory"
  host_identity=$(stat -Lc '%d:%i' -- "$release_host")
  [[ "$host_identity" == "$release_identity" ]] ||
    die "canonical release path no longer names the held verified release"
}

export STACK_ROOT=$root
export STACK_RELEASE_DIR=$release_host
export STACK_RELEASE_HELD_DIR=$release_held
export STACK_RUNTIME_ENV_FILE=$staged_runtime_env
export STACK_VERSIONS_ENV_FILE=/proc/self/fd/$versions_env_fd
export STACK_COMPOSE_FILE=/proc/self/fd/$compose_file_fd
export STACK_OPENSEARCH_CONFIG_FILE=/proc/self/fd/$opensearch_config_fd
export STACK_OPENSEARCH_ENTRYPOINT_FILE=/proc/self/fd/$opensearch_entrypoint_fd
compose_script=/proc/self/fd/$compose_script_fd
health_script=/proc/self/fd/$health_script_fd
export STACK_COMPOSE_SCRIPT=$compose_script
verify_release_identity
bash "$compose_script" "${profiles[@]}" -- config --quiet
verify_release_identity
bash "$compose_script" "${profiles[@]}" -- pull
verify_release_identity
bash "$compose_script" "${profiles[@]}" -- up -d --wait
verify_release_identity
bash "$health_script" "${profiles[@]}"
verify_release_identity

if [[ ! -e "$release_dir/.successful" ]]; then
  success_temp=$release_dir/.successful.$$
  (umask 077; : >"$success_temp")
  mv -T -- "$success_temp" "$release_dir/.successful"
fi
current_temp=$root_path/.current.$release_name.$$
ln -sfn -- "releases/$release_name" "$current_temp"
verify_release_identity
mv -Tf -- "$current_temp" "$root_path/current"
current_temp=

# A successful release is trusted only when both deployer-owned markers are
# regular files. Keep current even when its name sorts older, then keep the two
# newest other successes. Symlinks and unexpected entries are never traversed.
successful_names=()
while IFS= read -r -d '' candidate; do
  [[ -d "$candidate" && ! -L "$candidate" ]] || continue
  [[ -f "$candidate/.successful" && ! -L "$candidate/.successful" ]] || continue
  [[ -f "$candidate/.release-digest" && ! -L "$candidate/.release-digest" ]] || continue
  candidate_name=${candidate##*/}
  [[ "$candidate_name" != *$'\n'* ]] || continue
  successful_names+=("$candidate_name")
done < <(find "$releases_path/." -mindepth 1 -maxdepth 1 -type d -print0)

sorted_names=()
if ((${#successful_names[@]} > 0)); then
  mapfile -d '' -t sorted_names < <(
    printf '%s\0' "${successful_names[@]}" | LC_ALL=C sort -z -r
  )
fi
declare -A keep=(["$release_name"]=1)
keep_count=1
for candidate_name in "${sorted_names[@]}"; do
  [[ "$candidate_name" == "$release_name" ]] && continue
  if ((keep_count < 3)); then
    keep[$candidate_name]=1
    ((keep_count += 1))
  fi
done
for candidate_name in "${sorted_names[@]}"; do
  [[ -n "${keep[$candidate_name]+x}" ]] && continue
  old_release=$releases_path/$candidate_name
  [[ -d "$old_release" && ! -L "$old_release" ]] || continue
  [[ -f "$old_release/.successful" && ! -L "$old_release/.successful" ]] || continue
  [[ -f "$old_release/.release-digest" && ! -L "$old_release/.release-digest" ]] || continue
  rm -rf -- "$old_release"
done

printf 'Activated release %s for profiles:' "$release_name"
printf ' %s' "${profiles[@]}"
printf '\n'
