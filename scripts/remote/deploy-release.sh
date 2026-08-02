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

mkdir -p -- "$root"
root=$(realpath -e -- "$root")
mkdir -p -- "$root/runtime" "$root/releases"

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

exec 9>"$root/runtime/deploy.lock"
flock -n 9 || die "another deployment is already in progress"

archive_file=$(basename -- "$archive")
[[ "$archive_file" == *.tar.gz ]] || die "archive name must end in .tar.gz"
release_name=${archive_file%.tar.gz}
[[ -n "$release_name" && "$release_name" != . && "$release_name" != .. && "$release_name" != *$'\n'* ]] ||
  die "unsafe release name"
release_dir=$root/releases/$release_name
inside_root "$release_dir" || die "release path must remain below the stack root"
[[ ! -e "$release_dir" && ! -L "$release_dir" ]] || die "release already exists: $release_name"
if [[ -e "$root/current" && ! -L "$root/current" ]]; then
  die "current must be absent or a symbolic link"
fi

mapfile -t checksum_lines <"$checksum"
((${#checksum_lines[@]} == 1)) || die "checksum file must contain exactly one record"
checksum_line=${checksum_lines[0]}
if [[ ! "$checksum_line" =~ ^([[:xdigit:]]{64})[[:space:]][[:space:]](.+)$ ]]; then
  die "checksum file has an invalid format"
fi
[[ "${BASH_REMATCH[2]}" == "$archive_file" ]] || die "checksum record must name the archive basename exactly"
(cd -- "$(dirname -- "$archive")" && sha256sum -c -- "$checksum" >/dev/null) ||
  die "archive checksum verification failed"

runtime_env=$root/runtime/.env
[[ -f "$runtime_env" && ! -L "$runtime_env" ]] || die "runtime/.env must be a regular non-symlink file"
chmod 0600 -- "$runtime_env"

archive_list=$(mktemp "$root/runtime/archive-list.XXXXXX")
current_temp=
cleanup() {
  rm -f -- "$archive_list"
  if [[ -n "$current_temp" ]]; then
    rm -f -- "$current_temp"
  fi
}
trap cleanup EXIT
tar -tzf "$archive" >"$archive_list" || die "archive listing failed"
while IFS= read -r member; do
  [[ -n "$member" && "$member" != /* && "$member" != *$'\n'* ]] || die "archive contains an unsafe member path"
  IFS=/ read -r -a components <<<"$member"
  for component in "${components[@]}"; do
    [[ "$component" != .. ]] || die "archive contains parent traversal"
  done
done <"$archive_list"

mkdir -- "$release_dir"
tar --extract --gzip --file "$archive" --directory "$release_dir" \
  --no-same-owner --no-same-permissions || die "archive extraction failed"
while IFS= read -r -d '' link; do
  resolved=$(realpath -m -- "$link")
  [[ "$resolved" == "$release_dir" || "$resolved" == "$release_dir/"* ]] ||
    die "archive symbolic link escapes the release directory"
done < <(find "$release_dir" -type l -print0)

for required in compose.yaml versions.env scripts/remote/compose.sh scripts/remote/health.sh; do
  [[ -f "$release_dir/$required" && ! -L "$release_dir/$required" ]] ||
    die "release is missing regular file: $required"
done

export STACK_ROOT=$root
export STACK_RELEASE_DIR=$release_dir
compose_script=$release_dir/scripts/remote/compose.sh
health_script=$release_dir/scripts/remote/health.sh
bash "$compose_script" "${profiles[@]}" -- config --quiet
bash "$compose_script" "${profiles[@]}" -- pull
bash "$compose_script" "${profiles[@]}" -- up -d --wait
bash "$health_script" "${profiles[@]}"

touch -- "$release_dir/.successful"
current_temp=$root/.current.$release_name.$$
ln -sfn -- "releases/$release_name" "$current_temp"
mv -Tf -- "$current_temp" "$root/current"
current_temp=

mapfile -t successful_releases < <(
  find "$root/releases" -mindepth 2 -maxdepth 2 -type f -name .successful -printf '%h\n' |
    while IFS= read -r path; do basename -- "$path"; done |
    LC_ALL=C sort
)
if ((${#successful_releases[@]} > 3)); then
  prune_count=$((${#successful_releases[@]} - 3))
  for ((index = 0; index < prune_count; index++)); do
    old_release=$root/releases/${successful_releases[$index]}
    old_release=$(realpath -e -- "$old_release")
    inside_root "$old_release" || die "refusing to prune a path outside the stack root"
    [[ -f "$old_release/.successful" ]] || die "refusing to prune an unsuccessful release"
    rm -rf -- "$old_release"
  done
fi

printf 'Activated release %s for profiles:' "$release_name"
printf ' %s' "${profiles[@]}"
printf '\n'
