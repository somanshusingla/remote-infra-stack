#!/usr/bin/env bash
set -euo pipefail
umask 077

output=.env
force=false

while (($#)); do
  case "$1" in
    --output)
      if (($# < 2)); then
        printf '%s\n' 'Missing path after --output' >&2
        exit 2
      fi
      output=$2
      shift 2
      ;;
    --force)
      force=true
      shift
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

if [[ "$output" == -* ]]; then
  output=./$output
fi

if [[ ( -e "$output" || -L "$output" ) && "$force" != true ]]; then
  printf 'Refusing to overwrite %s without --force\n' "$output" >&2
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  printf '%s\n' 'openssl is required to generate secrets' >&2
  exit 1
fi

secret() {
  openssl rand -hex "$1"
}

template=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env.example
output_dir=$(dirname "$output")
temporary=$(mktemp "$output_dir/.init-env.XXXXXX")

cleanup() {
  rm -f "$temporary"
}
trap cleanup EXIT

while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    *=GENERATED_BY_INIT_ENV)
      key=${line%%=*}
      case "$key" in
        LANGFUSE_ENCRYPTION_KEY)
          value=$(secret 32)
          ;;
        OPENSEARCH_INITIAL_ADMIN_PASSWORD)
          value="aA0!$(secret 14)"
          ;;
        *)
          value=$(secret 32)
          ;;
      esac
      printf '%s=%s\n' "$key" "$value" >>"$temporary"
      ;;
    *)
      printf '%s\n' "$line" >>"$temporary"
      ;;
  esac
done <"$template"

if [[ "$force" == true ]]; then
  mv -f "$temporary" "$output"
elif ln "$temporary" "$output"; then
  rm -f "$temporary"
else
  if [[ -e "$output" || -L "$output" ]]; then
    printf 'Refusing to overwrite %s without --force\n' "$output" >&2
  else
    printf 'Unable to publish %s\n' "$output" >&2
  fi
  exit 1
fi

trap - EXIT
