#!/usr/bin/env bash

common_die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

remote_key_is_allowed() {
  case "$1" in
    REMOTE_HOST|REMOTE_USER|REMOTE_PORT|REMOTE_IDENTITY_FILE|REMOTE_ROOT|\
      LOCAL_POSTGRES_PORT|LOCAL_REDIS_PORT|LOCAL_CHROMA_PORT|\
      LOCAL_OPENSEARCH_PORT|LOCAL_OPENSEARCH_DASHBOARDS_PORT|\
      LOCAL_LANGFUSE_PORT|LOCAL_PGADMIN_PORT|LOCAL_REDISINSIGHT_PORT|\
      LOCAL_MINIO_API_PORT|LOCAL_MINIO_CONSOLE_PORT)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

load_remote_env() {
  local env_file=$1
  [[ -f "$env_file" ]] || common_die "remote configuration file is missing: $env_file"

  REMOTE_HOST=
  REMOTE_USER=
  REMOTE_PORT=
  REMOTE_IDENTITY_FILE=
  REMOTE_ROOT=
  LOCAL_POSTGRES_PORT=
  LOCAL_REDIS_PORT=
  LOCAL_CHROMA_PORT=
  LOCAL_OPENSEARCH_PORT=
  LOCAL_OPENSEARCH_DASHBOARDS_PORT=
  LOCAL_LANGFUSE_PORT=
  LOCAL_PGADMIN_PORT=
  LOCAL_REDISINSIGHT_PORT=
  LOCAL_MINIO_API_PORT=
  LOCAL_MINIO_CONSOLE_PORT=

  local line line_number=0 key value seen_keys=$'\n'
  while IFS= read -r line || [[ -n "$line" ]]; do
    ((line_number += 1))
    line=${line%$'\r'}
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ ! "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      common_die "invalid remote.env line $line_number: expected KEY=VALUE"
    fi
    key=${BASH_REMATCH[1]}
    value=${BASH_REMATCH[2]}
    remote_key_is_allowed "$key" || common_die "unknown remote.env key: $key"
    case "$seen_keys" in
      *$'\n'"$key"$'\n'*) common_die "duplicate remote.env key: $key" ;;
    esac
    seen_keys+="$key"$'\n'
    printf -v "$key" '%s' "$value"
  done <"$env_file"

  local required_key
  local -a required_keys=(
    REMOTE_HOST REMOTE_USER REMOTE_PORT REMOTE_IDENTITY_FILE REMOTE_ROOT
    LOCAL_POSTGRES_PORT LOCAL_REDIS_PORT LOCAL_CHROMA_PORT
    LOCAL_OPENSEARCH_PORT LOCAL_OPENSEARCH_DASHBOARDS_PORT
    LOCAL_LANGFUSE_PORT LOCAL_PGADMIN_PORT LOCAL_REDISINSIGHT_PORT
    LOCAL_MINIO_API_PORT LOCAL_MINIO_CONSOLE_PORT
  )
  for required_key in "${required_keys[@]}"; do
    case "$seen_keys" in
      *$'\n'"$required_key"$'\n'*) ;;
      *) common_die "missing remote.env key: $required_key" ;;
    esac
  done

  [[ -n "$REMOTE_HOST" ]] || common_die "REMOTE_HOST is required"
  [[ "$REMOTE_HOST" != -* ]] || common_die "REMOTE_HOST must not begin with an option prefix"
  [[ "$REMOTE_HOST" != *:* ]] || common_die "REMOTE_HOST must not contain a colon; IPv6 targets are unsupported"
  [[ "$REMOTE_HOST" =~ ^[A-Za-z0-9_.-]+$ ]] ||
    common_die "REMOTE_HOST contains unsupported characters"
  [[ "$REMOTE_USER" != -* ]] || common_die "REMOTE_USER must not begin with an option prefix"
  [[ -z "$REMOTE_USER" || "$REMOTE_USER" =~ ^[A-Za-z0-9_.-]+$ ]] ||
    common_die "REMOTE_USER contains unsupported characters"
  if [[ -n "$REMOTE_PORT" ]]; then
    [[ "$REMOTE_PORT" =~ ^[0-9]+$ ]] || common_die "REMOTE_PORT must be an integer"
    local remote_port_digits=$REMOTE_PORT
    while [[ "$remote_port_digits" == 0* && ${#remote_port_digits} -gt 1 ]]; do
      remote_port_digits=${remote_port_digits#0}
    done
    ((${#remote_port_digits} <= 5)) || common_die "REMOTE_PORT must be between 1 and 65535"
    ((10#$remote_port_digits >= 1 && 10#$remote_port_digits <= 65535)) ||
      common_die "REMOTE_PORT must be between 1 and 65535"
    REMOTE_PORT=$((10#$remote_port_digits))
  fi
  [[ -n "$REMOTE_ROOT" && "$REMOTE_ROOT" != /* && "$REMOTE_ROOT" != ~* && ! "$REMOTE_ROOT" =~ ^[A-Za-z]: ]] ||
    common_die "REMOTE_ROOT must be a relative REMOTE_ROOT path"
  [[ ! "$REMOTE_ROOT" =~ (^|/)\.\.(/|$) ]] || common_die "REMOTE_ROOT must not contain .. path components"
  [[ "$REMOTE_ROOT" =~ ^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$ ]] ||
    common_die "REMOTE_ROOT contains unsupported REMOTE_ROOT characters"

  if [[ -n "$REMOTE_USER" ]]; then
    ssh_target="$REMOTE_USER@$REMOTE_HOST"
  else
    ssh_target=$REMOTE_HOST
  fi
  ssh_args=()
  scp_args=()
  if [[ -n "$REMOTE_PORT" ]]; then
    ssh_args=(-p "$REMOTE_PORT")
    scp_args=(-P "$REMOTE_PORT")
  fi
  if [[ -n "$REMOTE_IDENTITY_FILE" ]]; then
    ssh_args+=(-i "$REMOTE_IDENTITY_FILE")
    scp_args+=(-i "$REMOTE_IDENTITY_FILE")
  fi
}

posix_quote() {
  local value=$1 output="'" character
  while [[ -n "$value" ]]; do
    character=${value%"${value#?}"}
    value=${value#?}
    if [[ "$character" == "'" ]]; then
      output+="'"
      output+='\'
      output+="'"
      output+="'"
    else
      output+=$character
    fi
  done
  output+="'"
  printf '%s' "$output"
}

build_remote_command() {
  (($# > 0)) || common_die "remote command must not be empty"
  remote_command=
  local argument encoded
  for argument in "$@"; do
    encoded=$(posix_quote "$argument")
    [[ -z "$remote_command" ]] || remote_command+=' '
    remote_command+=$encoded
  done
}

run_ssh() {
  build_remote_command "$@"
  ssh "${ssh_args[@]}" -- "$ssh_target" "$remote_command"
}

base64_file_no_wrap() {
  local source=$1 encoded
  if encoded=$(base64 --wrap=0 <"$source" 2>/dev/null); then
    :
  else
    encoded=$(base64 <"$source" | tr -d '\r\n') || common_die "base64 encoding failed: $source"
  fi
  [[ "$encoded" != *$'\n'* && "$encoded" != *$'\r'* ]] ||
    common_die "base64 output unexpectedly contains line breaks: $source"
  printf '%s' "$encoded"
}

validate_profiles() {
  (($# > 0)) || common_die "at least one profile is required"
  local profile prior seen_tools=false seen_core=false
  local -a seen_profiles=()
  for profile in "$@"; do
    case "$profile" in
      core|vector|search|observability|tools) ;;
      *) common_die "unknown profile: $profile" ;;
    esac
    for prior in "${seen_profiles[@]}"; do
      [[ "$prior" != "$profile" ]] || common_die "duplicate profile: $profile"
    done
    seen_profiles+=("$profile")
    [[ "$profile" != tools ]] || seen_tools=true
    [[ "$profile" != core ]] || seen_core=true
  done
  if [[ "$seen_tools" == true && "$seen_core" != true ]]; then
    common_die "tools requires core"
  fi
}

require_clean_git_head() {
  local repository=$1
  git -C "$repository" rev-parse --verify HEAD >/dev/null 2>&1 ||
    common_die "operation requires a clean committed Git HEAD"
  git -C "$repository" diff --quiet -- || common_die "operation requires a clean committed Git HEAD"
  git -C "$repository" diff --cached --quiet -- || common_die "operation requires a clean committed Git HEAD"
  [[ -z "$(git -C "$repository" status --porcelain --untracked-files=normal)" ]] ||
    common_die "operation requires a clean committed Git HEAD"
}

reject_tracked_file() {
  local repository=$1 file=$2 label=$3 parent canonical relative
  parent=$(cd -- "$(dirname -- "$file")" && pwd -P) || common_die "cannot resolve $label parent"
  canonical=$parent/$(basename -- "$file")
  case "$canonical" in
    "$repository"/*)
      relative=${canonical#"$repository"/}
      if [[ -n "$(git -C "$repository" ls-files -- "$relative")" ]]; then
        common_die "$label must not be tracked by Git"
      fi
      ;;
  esac
}

validate_stack_env() {
  local env_file=$1
  local example_file=$2
  [[ -f "$env_file" ]] || common_die "stack secret file is missing: $env_file"
  [[ -f "$example_file" ]] || common_die "stack environment contract is missing: $example_file"

  local line key value prior line_number=0 found
  local -a actual_keys=() actual_values=() expected_keys=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    ((line_number += 1))
    line=${line%$'\r'}
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] ||
      common_die "invalid .env line $line_number: expected KEY=VALUE"
    key=${BASH_REMATCH[1]}
    value=${BASH_REMATCH[2]}
    for prior in "${actual_keys[@]}"; do
      [[ "$prior" != "$key" ]] || common_die "duplicate .env key: $key"
    done
    actual_keys+=("$key")
    actual_values+=("$value")
  done <"$env_file"

  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)= ]] || common_die "invalid .env.example contract"
    expected_keys+=("${BASH_REMATCH[1]}")
  done <"$example_file"

  for key in "${expected_keys[@]}"; do
    found=false
    for ((line_number = 0; line_number < ${#actual_keys[@]}; line_number++)); do
      if [[ "${actual_keys[$line_number]}" == "$key" ]]; then
        value=${actual_values[$line_number]}
        found=true
        break
      fi
    done
    [[ "$found" == true ]] || common_die "missing required .env key: $key"
    [[ -n "$value" ]] || common_die "empty required .env value: $key"
    [[ "$value" != *GENERATED_BY_INIT_ENV* ]] || common_die "placeholder remains in .env key: $key"
  done
  for key in "${actual_keys[@]}"; do
    found=false
    for prior in "${expected_keys[@]}"; do
      [[ "$prior" != "$key" ]] || found=true
    done
    [[ "$found" == true ]] || common_die "unknown .env key: $key"
  done

  local opensearch_password= encryption_key=
  for ((line_number = 0; line_number < ${#actual_keys[@]}; line_number++)); do
    case "${actual_keys[$line_number]}" in
      OPENSEARCH_INITIAL_ADMIN_PASSWORD) opensearch_password=${actual_values[$line_number]} ;;
      LANGFUSE_ENCRYPTION_KEY) encryption_key=${actual_values[$line_number]} ;;
    esac
  done
  ((${#opensearch_password} >= 12)) && [[ "$opensearch_password" == *[a-z]* ]] &&
    [[ "$opensearch_password" == *[A-Z]* ]] && [[ "$opensearch_password" == *[0-9]* ]] ||
    common_die "OPENSEARCH_INITIAL_ADMIN_PASSWORD does not meet the local strength contract"
  [[ "$encryption_key" =~ ^[0-9a-f]{64}$ ]] ||
    common_die "LANGFUSE_ENCRYPTION_KEY must be 64 lowercase hexadecimal characters"
}
