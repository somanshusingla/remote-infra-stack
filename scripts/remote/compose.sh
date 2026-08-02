#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
release_dir=${STACK_RELEASE_DIR:-$(cd -- "$script_dir/../.." && pwd -P)}
stack_root=${STACK_ROOT:-$(cd -- "$release_dir/../.." && pwd -P)}

profiles=()
declare -A selected=()
separator=0
while (($#)); do
  if [[ "$1" == -- ]]; then
    separator=1
    shift
    break
  fi
  profile=$1
  case "$profile" in
    core|vector|search|observability|tools) ;;
    *) die "unknown profile: $profile" ;;
  esac
  [[ -z "${selected[$profile]+x}" ]] || die "duplicate profile: $profile"
  selected[$profile]=1
  profiles+=("$profile")
  shift
done
(( separator == 1 )) || die "usage: compose.sh profiles... -- compose-arguments..."
(($# > 0)) || die "at least one Compose argument is required"
if [[ -n "${selected[tools]+x}" && -z "${selected[core]+x}" ]]; then
  die "tools requires core"
fi

docker_bin=${DOCKER_BIN:-docker}
if [[ "${STACK_TEST_MODE:-0}" == 1 && -z "${DOCKER_BIN:-}" && -x "$release_dir/tests/fakes/docker" ]]; then
  docker_bin=$release_dir/tests/fakes/docker
fi

command=(
  "$docker_bin" compose
  --env-file "$release_dir/versions.env"
  --env-file "$stack_root/runtime/.env"
  --project-directory "$release_dir"
)
for profile in "${profiles[@]}"; do
  command+=(--profile "$profile")
done
command+=("$@")
exec "${command[@]}"
