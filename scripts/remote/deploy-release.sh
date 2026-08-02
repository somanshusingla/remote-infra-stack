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

# The validated runtime directory itself is the lock object. This cannot follow or
# truncate a caller-controlled deploy.lock path and the descriptor remains open
# until the complete transaction exits.
flock -n "$runtime_fd" || die "another deployment is already in progress"

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
"${CP_BIN:-cp}" -- "$archive" "$staged_archive"
"${CP_BIN:-cp}" -- "$checksum" "$staged_checksum"
chmod 0600 -- "$staged_archive" "$staged_checksum"

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

runtime_env=$runtime_path/.env
[[ -f "$runtime_env" && ! -L "$runtime_env" ]] ||
  die "runtime/.env must be a regular non-symlink file"
chmod 0600 -- "$runtime_env"

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
for required in compose.yaml versions.env scripts/remote/compose.sh scripts/remote/health.sh; do
  [[ -f "$extracted/$required" && ! -L "$extracted/$required" ]] ||
    die "release is missing regular file: $required"
done

release_dir=$releases_path/$release_name
if [[ -e "$release_dir" || -L "$release_dir" ]]; then
  [[ -d "$release_dir" && ! -L "$release_dir" ]] || die "release collision is not a real directory"
  digest_marker=$release_dir/.release-digest
  [[ -f "$digest_marker" && ! -L "$digest_marker" ]] ||
    die "release collision lacks a trusted digest marker"
  stored_digest=$(<"$digest_marker")
  [[ "$stored_digest" == "$actual_digest" ]] || die "release collision has a different archive digest"
  rm -rf -- "$extracted"
else
  mv -T -- "$extracted" "$release_dir"
  digest_temp=$release_dir/.release-digest.$$
  (umask 077; printf '%s\n' "$actual_digest" >"$digest_temp")
  mv -T -- "$digest_temp" "$release_dir/.release-digest"
fi

if [[ -e "$root_path/current" && ! -L "$root_path/current" ]]; then
  die "current must be absent or a symbolic link"
fi
if [[ -e "$release_dir/.successful" || -L "$release_dir/.successful" ]]; then
  [[ -f "$release_dir/.successful" && ! -L "$release_dir/.successful" ]] ||
    die "successful marker must be a regular file"
fi

export STACK_ROOT=$root_path
export STACK_RELEASE_DIR=$release_dir
compose_script=$release_dir/scripts/remote/compose.sh
health_script=$release_dir/scripts/remote/health.sh
bash "$compose_script" "${profiles[@]}" -- config --quiet
bash "$compose_script" "${profiles[@]}" -- pull
bash "$compose_script" "${profiles[@]}" -- up -d --wait
bash "$health_script" "${profiles[@]}"

if [[ ! -e "$release_dir/.successful" ]]; then
  success_temp=$release_dir/.successful.$$
  (umask 077; : >"$success_temp")
  mv -T -- "$success_temp" "$release_dir/.successful"
fi
current_temp=$root_path/.current.$release_name.$$
ln -sfn -- "releases/$release_name" "$current_temp"
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
