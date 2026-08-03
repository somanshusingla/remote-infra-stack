#!/bin/sh
set -eu

npm_bin=${NPM_BIN:-npm}
sleep_bin=${SLEEP_BIN:-sleep}
attempts=${NPM_INSTALL_ATTEMPTS:-3}
retry_delay=${NPM_INSTALL_RETRY_DELAY_SECONDS:-2}
expected_npm_version=${EXPECTED_NPM_VERSION:?EXPECTED_NPM_VERSION is required}

case "$attempts" in
  1|2|3) ;;
  *)
    printf 'ERROR: NPM_INSTALL_ATTEMPTS must be between 1 and 3\n' >&2
    exit 2
    ;;
esac

actual_npm_version=$("$npm_bin" --version) || {
  printf 'ERROR: could not determine npm version\n' >&2
  exit 1
}
if [ "$actual_npm_version" != "$expected_npm_version" ]; then
  printf 'ERROR: expected npm %s but found %s\n' \
    "$expected_npm_version" "$actual_npm_version" >&2
  exit 1
fi

log_file=
cleanup_log() {
  if [ -n "$log_file" ]; then
    rm -f "$log_file"
  fi
}
trap cleanup_log 0

attempt=1
while [ "$attempt" -le "$attempts" ]; do
  log_file=$(mktemp "${TMPDIR:-/tmp}/chroma-admin-npm-ci.XXXXXX")
  if "$npm_bin" ci --no-audit --no-fund >"$log_file" 2>&1; then
    install_status=0
  else
    install_status=$?
  fi
  cat "$log_file"

  if [ "$install_status" -eq 0 ] \
    && ! grep -F 'Exit handler never called!' "$log_file" >/dev/null 2>&1 \
    && "$npm_bin" ls --all >/dev/null 2>&1 \
    && [ -x node_modules/.bin/next ] \
    && [ -x node_modules/.bin/tsc ] \
    && [ -s node_modules/@next/swc-linux-x64-gnu/next-swc.linux-x64-gnu.node ]; then
    rm -f "$log_file"
    log_file=
    exit 0
  fi

  printf 'WARNING: npm dependency install attempt %s of %s failed validation\n' \
    "$attempt" "$attempts" >&2
  rm -f "$log_file"
  log_file=
  rm -rf node_modules

  if [ "$attempt" -ge "$attempts" ]; then
    printf 'ERROR: npm dependency install failed after %s attempts\n' "$attempts" >&2
    exit 1
  fi
  "$sleep_bin" "$retry_delay"
  attempt=$((attempt + 1))
done
