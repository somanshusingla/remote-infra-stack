#!/bin/sh
set -eu

: "${OLLAMA_MODEL:?OLLAMA_MODEL is required}"
ollama_bin=${OLLAMA_BIN:-/bin/ollama}
ready_file=${OLLAMA_READY_FILE:-/tmp/remote-infra-model-ready}
startup_attempts=${OLLAMA_STARTUP_ATTEMPTS:-300}
pull_attempts=${OLLAMA_PULL_ATTEMPTS:-3}
retry_seconds=${OLLAMA_RETRY_SECONDS:-2}
sleep_bin=${SLEEP_BIN:-sleep}
server_pid=
forward_signal=TERM

cleanup() {
  signal=${1:-TERM}
  if [ -n "$server_pid" ] && kill -0 "$server_pid" 2>/dev/null; then
    kill -"$signal" "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
}

on_exit() {
  status=$?
  trap - 0
  cleanup "$forward_signal"
  exit "$status"
}

trap on_exit 0
trap 'forward_signal=TERM; exit 143' TERM
trap 'forward_signal=INT; exit 130' INT
trap 'forward_signal=HUP; exit 129' HUP

rm -f "$ready_file"
OLLAMA_HOST=0.0.0.0:11434 "$ollama_bin" serve &
server_pid=$!

attempt=1
while ! OLLAMA_HOST=127.0.0.1:11434 "$ollama_bin" list >/dev/null 2>&1; do
  kill -0 "$server_pid" 2>/dev/null || exit 1
  [ "$attempt" -lt "$startup_attempts" ] || exit 1
  attempt=$((attempt + 1))
  "$sleep_bin" "$retry_seconds"
done

if ! OLLAMA_HOST=127.0.0.1:11434 "$ollama_bin" show "$OLLAMA_MODEL" >/dev/null 2>&1; then
  attempt=1
  until OLLAMA_HOST=127.0.0.1:11434 "$ollama_bin" pull "$OLLAMA_MODEL"; do
    [ "$attempt" -lt "$pull_attempts" ] || exit 1
    attempt=$((attempt + 1))
    "$sleep_bin" "$retry_seconds"
  done
fi

OLLAMA_HOST=127.0.0.1:11434 "$ollama_bin" show "$OLLAMA_MODEL" >/dev/null
: >"$ready_file"
wait "$server_pid"
