#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

root=
archive=
checksum=
runtime_env=
profiles_csv=
while (($#)); do
  case "$1" in
    --root|--archive|--checksum|--env|--profiles)
      (($# >= 2)) || die "missing value for $1"
      option=$1
      value=$2
      shift 2
      case "$option" in
        --root) [[ -z "$root" ]] || die "duplicate option: --root"; root=$value ;;
        --archive) [[ -z "$archive" ]] || die "duplicate option: --archive"; archive=$value ;;
        --checksum) [[ -z "$checksum" ]] || die "duplicate option: --checksum"; checksum=$value ;;
        --env) [[ -z "$runtime_env" ]] || die "duplicate option: --env"; runtime_env=$value ;;
        --profiles) [[ -z "$profiles_csv" ]] || die "duplicate option: --profiles"; profiles_csv=$value ;;
      esac
      ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$root" && -n "$archive" && -n "$checksum" && -n "$runtime_env" && -n "$profiles_csv" ]] ||
  die "usage: deploy-release.sh --root PATH --archive PATH --checksum PATH --env PATH --profiles CSV"

IFS=, read -r -a profiles <<<"$profiles_csv"
((${#profiles[@]} > 0)) || die "at least one profile is required"
declare -A selected=()
for profile in "${profiles[@]}"; do
  [[ -n "$profile" ]] || die "profiles must not contain empty entries"
  case "$profile" in
    core|vector|search|observability|tools|dynamodb|inference) ;;
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
open_root_child incoming incoming_fd
runtime_path=/proc/self/fd/$runtime_fd
releases_path=/proc/self/fd/$releases_fd
incoming_path=/proc/self/fd/$incoming_fd
runtime_host=$(realpath -e -- "$runtime_path")
releases_host=$(realpath -e -- "$releases_path")
incoming_host=$(realpath -e -- "$incoming_path")
[[ "$runtime_host" == "$root/"* ]] || die "held runtime directory escaped the stack root"
[[ "$releases_host" == "$root/"* ]] || die "held releases directory escaped the stack root"
[[ "$incoming_host" == "$root/"* ]] || die "held incoming directory escaped the stack root"

# The validated runtime directory itself is the lock object. This cannot follow or
# truncate a caller-controlled deploy.lock path and the descriptor remains open
# until the complete transaction exits.
flock -n "$runtime_fd" || die "another deployment is already in progress"

runtime_env_entry=$runtime_path/.env
prior_runtime_env_existed=0
prior_runtime_env_fd=
prior_runtime_env_held=
if [[ -e "$runtime_env_entry" || -L "$runtime_env_entry" ]]; then
  [[ -f "$runtime_env_entry" && ! -L "$runtime_env_entry" ]] ||
    die "runtime/.env must be absent or a regular non-symlink file"
  prior_runtime_env_identity=$(stat -Lc '%d:%i' -- "$runtime_env_entry")
  exec {prior_runtime_env_fd}<"$runtime_env_entry"
  prior_runtime_env_held=/proc/self/fd/$prior_runtime_env_fd
  [[ -f "$prior_runtime_env_held" &&
     "$(stat -Lc '%d:%i' -- "$prior_runtime_env_held")" == "$prior_runtime_env_identity" ]] ||
    die "could not hold the previous runtime environment"
  prior_runtime_env_existed=1
fi

prior_current_present=0
prior_current_target=
prior_release_name=
current_entry=$root_path/current
if [[ -e "$current_entry" || -L "$current_entry" ]]; then
  [[ -L "$current_entry" ]] || die "current must be absent or a symbolic link"
  prior_current_target=$(readlink -- "$current_entry") ||
    die "could not read the current release link"
  [[ "$prior_current_target" == releases/* ]] ||
    die "current must name a direct child of releases"
  prior_release_name=${prior_current_target#releases/}
  [[ -n "$prior_release_name" && "$prior_release_name" != */* &&
     "$prior_release_name" != . && "$prior_release_name" != .. &&
     "$prior_release_name" != *$'\n'* ]] ||
    die "current must name a direct child of releases"
  prior_current_present=1
fi

hold_incoming_file() {
  local label=$1 source=$2 fd_variable=$3 name_variable=$4
  [[ -f "$source" && ! -L "$source" ]] ||
    die "$label must be a regular non-symlink file in incoming"
  local source_real source_parent source_name source_identity held_fd held_path
  source_real=$(realpath -e -- "$source")
  source_parent=$(dirname -- "$source_real")
  [[ "$source_parent" == "$incoming_host" ]] ||
    die "$label path must be a direct child of incoming"
  source_name=$(basename -- "$source_real")
  [[ -n "$source_name" && "$source_name" != . && "$source_name" != .. && "$source_name" != *$'\n'* ]] ||
    die "$label has an unsafe incoming name"
  source_identity=$(stat -Lc '%d:%i' -- "$source_real")
  exec {held_fd}<"$source_real"
  held_path=/proc/self/fd/$held_fd
  [[ -f "$held_path" && "$(stat -Lc '%d:%i' -- "$held_path")" == "$source_identity" ]] ||
    die "could not hold incoming $label"
  printf -v "$fd_variable" '%s' "$held_fd"
  printf -v "$name_variable" '%s' "$source_name"
}

hold_incoming_file archive "$archive" archive_fd archive_file
hold_incoming_file checksum "$checksum" checksum_fd checksum_file
hold_incoming_file environment "$runtime_env" runtime_env_fd runtime_env_file
archive_held=/proc/self/fd/$archive_fd
checksum_held=/proc/self/fd/$checksum_fd
runtime_env_held=/proc/self/fd/$runtime_env_fd
archive_identity=$(stat -Lc '%d:%i' -- "$archive_held")
checksum_identity=$(stat -Lc '%d:%i' -- "$checksum_held")
runtime_env_identity=$(stat -Lc '%d:%i' -- "$runtime_env_held")
[[ "$archive_identity" != "$checksum_identity" &&
   "$archive_identity" != "$runtime_env_identity" &&
   "$checksum_identity" != "$runtime_env_identity" ]] ||
  die "archive, checksum, and environment must be distinct incoming files"

[[ "$archive_file" == *.tar.gz ]] || die "archive name must end in .tar.gz"
release_name=${archive_file%.tar.gz}
[[ -n "$release_name" && "$release_name" != . && "$release_name" != .. && "$release_name" != *$'\n'* ]] ||
  die "unsafe release name"

staging=$(mktemp -d "$runtime_path/.deploy-XXXXXXXX")
chmod 0700 -- "$staging"
current_temp=
runtime_env_temp=
success_temp=
success_marker_installed=0
rollback_active=0
rollback_running=0
incoming_env_cleanup=$incoming_path/$runtime_env_file
cleanup() {
  local status=$?
  trap - ERR EXIT
  set +e
  if ((status != 0 && rollback_active == 1 && rollback_running == 0)); then
    rollback_deployment "$status"
  fi
  if [[ -n "${staging:-}" && -d "$staging" ]]; then
    rm -rf -- "$staging"
  fi
  if [[ -n "${current_temp:-}" ]]; then
    rm -f -- "$current_temp"
  fi
  if [[ -n "${runtime_env_temp:-}" ]]; then
    rm -f -- "$runtime_env_temp"
  fi
  if [[ -n "${success_temp:-}" ]]; then
    rm -f -- "$success_temp"
  fi
  if [[ -n "${incoming_env_cleanup:-}" ]]; then
    rm -f -- "$incoming_env_cleanup"
  fi
  exit "$status"
}
trap cleanup EXIT

# Snapshot each untrusted input exactly once while holding the deployment lock.
# Every subsequent operation uses only these private copies.
staged_archive=$staging/archive.tar.gz
staged_checksum=$staging/archive.sha256
staged_runtime_env=$staging/runtime.env
"${CP_BIN:-cp}" -- "$runtime_env_held" "$staged_runtime_env"
"${CP_BIN:-cp}" -- "$archive_held" "$staged_archive"
"${CP_BIN:-cp}" -- "$checksum_held" "$staged_checksum"
chmod 0600 -- "$staged_archive" "$staged_checksum" "$staged_runtime_env"
rm -f -- "$incoming_env_cleanup"
incoming_env_cleanup=

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
  compose.yaml versions.env scripts/remote/compose.sh scripts/remote/preflight.sh \
  scripts/remote/health.sh config/ollama/bootstrap.sh \
  images/chromadb-admin/Dockerfile .dockerignore \
  vendor/chromadb-admin/package.json vendor/chromadb-admin/package-lock.json \
  vendor/chromadb-admin/LICENSE.txt vendor/chromadb-admin/UPSTREAM.md \
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
open_release_leaf scripts/remote/preflight.sh preflight_script_fd
open_release_leaf scripts/remote/health.sh health_script_fd
open_release_leaf config/ollama/bootstrap.sh ollama_bootstrap_fd
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
release_identity_is_current() {
  local host_identity
  [[ -d "$release_host" && ! -L "$release_host" ]] || return 1
  host_identity=$(stat -Lc '%d:%i' -- "$release_host") || return 1
  [[ "$host_identity" == "$release_identity" ]]
}
verify_release_identity() {
  release_identity_is_current ||
    die "canonical release path no longer names the held verified release"
}

current_names_held_release() {
  local target target_real target_identity
  [[ -L "$current_entry" ]] || return 1
  target=$(readlink -- "$current_entry") || return 1
  [[ "$target" == "releases/$release_name" ]] || return 1
  release_identity_is_current || return 1
  target_real=$(realpath -e -- "$current_entry") || return 1
  [[ "$target_real" == "$release_host" ]] || return 1
  target_identity=$(stat -Lc '%d:%i' -- "$current_entry") || return 1
  [[ "$target_identity" == "$release_identity" ]]
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
preflight_script=/proc/self/fd/$preflight_script_fd
health_script=/proc/self/fd/$health_script_fd
export STACK_COMPOSE_SCRIPT=$compose_script

profile_services() {
  case "$1" in
    core) printf '%s\n' app-postgres app-redis ;;
    vector) printf '%s\n' chroma chroma-admin ;;
    search) printf '%s\n' opensearch opensearch-dashboards ;;
    observability)
      printf '%s\n' langfuse-postgres langfuse-redis clickhouse minio langfuse-worker langfuse-web
      ;;
    tools) printf '%s\n' pgadmin redisinsight ;;
    dynamodb) printf '%s\n' dynamodb-local dynamodb-admin ;;
    inference) printf '%s\n' ollama-llm ollama-embedding ;;
    *) return 1 ;;
  esac
}

attempted_services=()
for profile in "${profiles[@]}"; do
  mapfile -t expanded_services < <(profile_services "$profile")
  attempted_services+=("${expanded_services[@]}")
done

# A previous release is executable during rollback only when current names one
# trusted direct release child and all Compose transport inputs can be held.
prior_release_available=0
prior_release_fd=
prior_release_held=
prior_release_host=
hold_prior_leaf() {
  local relative=$1 output_variable=$2
  local source=$prior_release_held/$relative
  [[ -f "$source" && ! -L "$source" ]] || return 1
  local source_real source_identity leaf_fd held
  source_real=$(realpath -e -- "$source") || return 1
  [[ "$source_real" == "$prior_release_host/$relative" ]] || return 1
  source_identity=$(stat -Lc '%d:%i' -- "$source") || return 1
  exec {leaf_fd}<"$source" || return 1
  held=/proc/self/fd/$leaf_fd
  [[ -f "$held" && "$(stat -Lc '%d:%i' -- "$held")" == "$source_identity" ]] ||
    return 1
  printf -v "$output_variable" '%s' "$leaf_fd"
}

if ((prior_current_present == 1)); then
  prior_candidate=$releases_path/$prior_release_name
  if [[ -d "$prior_candidate" && ! -L "$prior_candidate" &&
        -f "$prior_candidate/.successful" && ! -L "$prior_candidate/.successful" &&
        -f "$prior_candidate/.release-digest" && ! -L "$prior_candidate/.release-digest" ]]; then
    prior_stored_digest=$(<"$prior_candidate/.release-digest")
    if [[ "$prior_stored_digest" =~ ^[[:xdigit:]]{64}$ ]]; then
      prior_release_host=$(realpath -e -- "$prior_candidate")
      if [[ "$prior_release_host" == "$releases_host/$prior_release_name" ]]; then
        exec {prior_release_fd}<"$prior_candidate"
        prior_release_held=/proc/self/fd/$prior_release_fd
        if [[ -d "$prior_release_held" &&
              "$(realpath -e -- "$prior_release_held")" == "$prior_release_host" ]] &&
          hold_prior_leaf scripts/remote/compose.sh prior_compose_script_fd &&
          hold_prior_leaf compose.yaml prior_compose_file_fd &&
          hold_prior_leaf versions.env prior_versions_env_fd &&
          hold_prior_leaf config/opensearch/opensearch.yml prior_opensearch_config_fd &&
          hold_prior_leaf config/opensearch/docker-entrypoint.sh prior_opensearch_entrypoint_fd; then
          prior_release_available=1
        fi
      fi
    fi
  fi
fi

prior_compose() {
  STACK_ROOT="$root" \
  STACK_RELEASE_DIR="$prior_release_host" \
  STACK_RELEASE_HELD_DIR="$prior_release_held" \
  STACK_RUNTIME_ENV_FILE="$prior_runtime_env_held" \
  STACK_VERSIONS_ENV_FILE="/proc/self/fd/$prior_versions_env_fd" \
  STACK_COMPOSE_FILE="/proc/self/fd/$prior_compose_file_fd" \
  STACK_OPENSEARCH_CONFIG_FILE="/proc/self/fd/$prior_opensearch_config_fd" \
  STACK_OPENSEARCH_ENTRYPOINT_FILE="/proc/self/fd/$prior_opensearch_entrypoint_fd" \
  STACK_COMPOSE_SCRIPT="/proc/self/fd/$prior_compose_script_fd" \
    bash "/proc/self/fd/$prior_compose_script_fd" "$@"
}

restore_previous_current() {
  if [[ -n "${current_temp:-}" ]]; then
    rm -f -- "$current_temp"
    current_temp=
  fi
  if ((prior_current_present == 1)); then
    current_temp=$root_path/.current.rollback.$$
    rm -f -- "$current_temp"
    if ln -s -- "$prior_current_target" "$current_temp" &&
       mv -Tf -- "$current_temp" "$current_entry"; then
      current_temp=
    fi
  elif [[ -L "$current_entry" ]]; then
    rm -f -- "$current_entry"
  fi
}

restore_previous_runtime_env() {
  if ((prior_runtime_env_existed == 1)); then
    runtime_env_temp=$(mktemp "$runtime_path/.env.rollback.XXXXXXXX") || return 1
    if "${CP_BIN:-cp}" -- "$prior_runtime_env_held" "$runtime_env_temp" &&
       chmod 0600 -- "$runtime_env_temp" &&
       mv -T -- "$runtime_env_temp" "$runtime_env_entry"; then
      runtime_env_temp=
      return 0
    fi
    return 1
  fi
  if [[ -e "$runtime_env_entry" || -L "$runtime_env_entry" ]]; then
    [[ -f "$runtime_env_entry" && ! -L "$runtime_env_entry" ]] || return 1
    rm -f -- "$runtime_env_entry"
  fi
}

restart_previous_services() {
  ((prior_release_available == 1 && prior_runtime_env_existed == 1)) || return 0
  local profile
  local -a supported_profiles=() probe_profiles=()
  declare -A prior_supported=()
  for profile in "${profiles[@]}"; do
    probe_profiles=("$profile")
    if [[ "$profile" == tools ]]; then
      [[ -n "${selected[core]+x}" ]] || continue
      probe_profiles=(core tools)
    fi
    if prior_compose "${probe_profiles[@]}" -- config --quiet >/dev/null 2>&1; then
      prior_supported[$profile]=1
    fi
  done
  for profile in "${profiles[@]}"; do
    [[ -n "${prior_supported[$profile]+x}" ]] || continue
    if [[ "$profile" == tools && -z "${prior_supported[core]+x}" ]]; then
      continue
    fi
    supported_profiles+=("$profile")
  done
  ((${#supported_profiles[@]} > 0)) || return 0
  prior_compose "${supported_profiles[@]}" -- up -d --wait >/dev/null 2>&1
}

rollback_deployment() {
  local original_status=$1
  rollback_running=1
  rollback_active=0
  trap - ERR
  set +e
  bash "$compose_script" "${profiles[@]}" -- rm -sf "${attempted_services[@]}" ||
    printf 'WARNING: failed-release container cleanup did not complete\n' >&2
  if ((success_marker_installed == 1)); then
    if [[ -f "$release_held/.successful" && ! -L "$release_held/.successful" ]]; then
      rm -f -- "$release_held/.successful" ||
        printf 'WARNING: failed-release success marker could not be removed\n' >&2
    fi
    success_marker_installed=0
  fi
  restore_previous_runtime_env ||
    printf 'WARNING: previous runtime environment could not be restored\n' >&2
  restore_previous_current ||
    printf 'WARNING: previous current release link could not be restored\n' >&2
  restart_previous_services ||
    printf 'WARNING: previous selected services could not be restarted\n' >&2
  rollback_running=0
  return 0
}

on_transaction_error() {
  local original_status=$?
  trap - ERR
  set +e
  rollback_deployment "$original_status"
  exit "$original_status"
}

# From this point, every non-successful exit rolls back the runtime transaction.
rollback_active=1
trap on_transaction_error ERR

# Install the exact lock-held snapshot as the durable runtime environment. The
# Compose transaction continues to read the private snapshot so later pathname
# replacement cannot mix this release with another deployment's environment.
runtime_env_temp=$(mktemp "$runtime_path/.env.XXXXXXXX")
"${CP_BIN:-cp}" -- "$staged_runtime_env" "$runtime_env_temp"
chmod 0600 -- "$runtime_env_temp"
mv -T -- "$runtime_env_temp" "$runtime_env_entry"
runtime_env_temp=
[[ -f "$runtime_env_entry" && ! -L "$runtime_env_entry" ]] ||
  die "could not atomically install runtime/.env"

verify_release_identity
bash "$compose_script" "${profiles[@]}" -- config --quiet
verify_release_identity
bash "$preflight_script" "${profiles[@]}"
verify_release_identity
bash "$compose_script" "${profiles[@]}" -- pull --ignore-buildable
verify_release_identity
if [[ -n "${selected[vector]+x}" ]]; then
  # Docker reads the repository-owned build context by canonical pathname. The
  # held descriptors above authenticate the control scripts and Compose inputs;
  # same-user mutation of build-context or bind-mount pathname bytes is outside
  # that descriptor guarantee.
  bash "$compose_script" "${profiles[@]}" -- build --pull chroma-admin
  verify_release_identity
fi
bash "$compose_script" "${profiles[@]}" -- up -d --wait
verify_release_identity
bash "$health_script" "${profiles[@]}"
verify_release_identity

current_temp=$root_path/.current.$release_name.$$
ln -sfn -- "releases/$release_name" "$current_temp"
verify_release_identity
"$move_bin" -Tf -- "$current_temp" "$root_path/current"
current_temp=
current_names_held_release || die "current activation did not name the held verified release"
trap - ERR
[[ ! -e "$release_held/.successful" && ! -L "$release_held/.successful" ]] ||
  die "successful marker appeared before transaction commit"
success_temp=$release_held/.successful.$$
(umask 077; : >"$success_temp")
mv -T -- "$success_temp" "$release_held/.successful"
success_marker_installed=1
success_temp=
current_names_held_release || die "current activation changed before commit"
rollback_active=0

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
