#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

is_t4_gpu_name() {
  case "$1" in
    "Tesla T4"|"NVIDIA T4") return 0 ;;
    *) return 1 ;;
  esac
}

case "${1:-}" in
  --check|--install)
    mode=$1
    shift
    ;;
  *)
    die "usage: bootstrap-host.sh --check|--install"
    ;;
esac

gpu_mode=0
cuda_image=
cuda_image_seen=0
while (( $# > 0 )); do
  case "$1" in
    --gpu)
      (( gpu_mode == 0 )) || die "usage: bootstrap-host.sh --check|--install [--gpu --cuda-image IMAGE]"
      gpu_mode=1
      shift
      ;;
    --cuda-image)
      (( cuda_image_seen == 0 && $# >= 2 )) ||
        die "usage: bootstrap-host.sh --check|--install [--gpu --cuda-image IMAGE]"
      [[ "$2" != --* ]] ||
        die "usage: bootstrap-host.sh --check|--install [--gpu --cuda-image IMAGE]"
      cuda_image=$2
      cuda_image_seen=1
      shift 2
      ;;
    *)
      die "usage: bootstrap-host.sh --check|--install [--gpu --cuda-image IMAGE]"
      ;;
  esac
done

(( gpu_mode == 0 || cuda_image_seen == 1 )) || die "--gpu requires --cuda-image IMAGE"
(( cuda_image_seen == 0 || gpu_mode == 1 )) || die "--cuda-image requires --gpu"
if (( cuda_image_seen == 1 )); then
  [[ "$cuda_image" != -* ]] || die "CUDA image must not begin with '-'"
  [[ "$cuda_image" != *:latest* &&
     "$cuda_image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] ||
    die "CUDA image must be a single non-latest digest-pinned reference"
fi

os_release_file=${STACK_OS_RELEASE_FILE:-/etc/os-release}
[[ -r "$os_release_file" ]] || die "cannot read OS release file: $os_release_file"

# shellcheck disable=SC1090
source "$os_release_file"
[[ "${ID:-}" == ubuntu ]] || die "unsupported operating system: expected ID=ubuntu, found ID=${ID:-unknown}"

machine_arch=$(uname -m)
dpkg_arch=$(dpkg --print-architecture 2>/dev/null || true)
[[ "$machine_arch" == x86_64 && "$dpkg_arch" == amd64 ]] ||
  die "unsupported architecture: require Ubuntu amd64 (uname=$machine_arch, dpkg=${dpkg_arch:-unknown})"

for command_name in systemctl apt-get sudo curl; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command is unavailable: $command_name"
done
systemctl show-environment >/dev/null 2>&1 ||
  die "systemd is not operational; run this bootstrap on an Ubuntu systemd host"
sudo -n true >/dev/null 2>&1 || die "passwordless sudo is required; configure sudo and retry"

if (( gpu_mode == 1 )); then
  command -v nvidia-smi >/dev/null 2>&1 ||
    die "GPU mode requires the NVIDIA driver and nvidia-smi from the Deep Learning VM image"
  command -v mktemp >/dev/null 2>&1 || die "GPU mode requires mktemp"
  nvidia_smi_output_temp=$(mktemp) || die "could not create host GPU validation temporary file"
  trap 'rm -f -- "$nvidia_smi_output_temp"' EXIT
  if ! nvidia-smi --query-gpu=name --format=csv,noheader >"$nvidia_smi_output_temp"; then
    die "GPU mode requires a working NVIDIA driver from the Deep Learning VM image"
  fi
  mapfile -t gpu_names <"$nvidia_smi_output_temp"
  rm -f -- "$nvidia_smi_output_temp"
  trap - EXIT
  [[ ${#gpu_names[@]} -eq 1 ]] ||
    die "GPU mode requires exactly one NVIDIA T4; nvidia-smi may label it Tesla T4"
  is_t4_gpu_name "${gpu_names[0]}" ||
    die "GPU mode requires exactly one NVIDIA T4; nvidia-smi may label it Tesla T4"
fi

codename=${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}
[[ -n "$codename" ]] || die "Ubuntu VERSION_ID=${VERSION_ID:-unknown} has no repository codename"
docker_repo_base=${STACK_DOCKER_REPO_BASE:-https://download.docker.com/linux/ubuntu}
docker_packages=(
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
)
docker_release_url="${docker_repo_base%/}/dists/${codename}/Release"
if ! curl --fail --silent --show-error --location "$docker_release_url" >/dev/null; then
  die "Docker repository unavailable for Ubuntu VERSION_ID=${VERSION_ID:-unknown}, codename=$codename; wait for Docker support or use a supported Ubuntu LTS"
fi
docker_packages_url="${docker_repo_base%/}/dists/${codename}/stable/binary-amd64/Packages"
if ! docker_package_index=$(curl --fail --silent --show-error --location "$docker_packages_url"); then
  die "Docker package index unavailable for Ubuntu VERSION_ID=${VERSION_ID:-unknown}, codename=$codename; wait for Docker support before modifying the host"
fi
for package in "${docker_packages[@]}"; do
  if ! grep --fixed-strings --line-regexp --quiet "Package: $package" <<<"$docker_package_index"; then
    die "Docker repository lacks required package $package for Ubuntu VERSION_ID=${VERSION_ID:-unknown}, codename=$codename; wait for Docker support before modifying the host"
  fi
done

nvidia_gpg_url=https://nvidia.github.io/libnvidia-container/gpgkey
nvidia_repository_url=https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list
nvidia_keyring_path=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
nvidia_sources_path=/etc/apt/sources.list.d/nvidia-container-toolkit.list
nvidia_gpg_key=
nvidia_repository_list=
nvidia_signed_repository_list=
if (( gpu_mode == 1 )); then
  if ! nvidia_gpg_key=$(curl --fail --silent --show-error --location "$nvidia_gpg_url"); then
    die "NVIDIA container toolkit signing key is unavailable"
  fi
  [[ -n "$nvidia_gpg_key" ]] || die "NVIDIA container toolkit signing key is empty"
  if ! nvidia_repository_list=$(curl --fail --silent --show-error --location "$nvidia_repository_url"); then
    die "NVIDIA container toolkit stable repository is unavailable"
  fi
  while IFS= read -r repository_line || [[ -n "$repository_line" ]]; do
    if [[ "$repository_line" == "deb https://"* ]]; then
      repository_line="deb [signed-by=$nvidia_keyring_path] https://${repository_line#deb https://}"
    fi
    nvidia_signed_repository_list+="$repository_line"$'\n'
  done <<<"$nvidia_repository_list"
  nvidia_signed_repository_list=${nvidia_signed_repository_list%$'\n'}
  [[ "$nvidia_signed_repository_list" == *"deb [signed-by=$nvidia_keyring_path] https://"* ]] ||
    die "NVIDIA container toolkit stable repository has no supported deb entry"
  command -v gpg >/dev/null 2>&1 ||
    die "GPU mode requires gpg to validate the NVIDIA container toolkit signing key"
  nvidia_keyring_temp=$(mktemp) || die "could not create NVIDIA keyring temporary file"
  trap 'rm -f -- "$nvidia_keyring_temp"' EXIT
  if ! printf '%s\n' "$nvidia_gpg_key" | gpg --dearmor >"$nvidia_keyring_temp"; then
    die "NVIDIA container toolkit signing key validation failed"
  fi
  [[ -s "$nvidia_keyring_temp" ]] ||
    die "NVIDIA container toolkit signing key validation produced an empty keyring"
fi

if [[ "$mode" == --check ]]; then
  if (( gpu_mode == 1 )); then
    rm -f -- "$nvidia_keyring_temp"
    trap - EXIT
  fi
  printf 'Host supports Docker bootstrap: Ubuntu %s (%s), amd64.\n' "${VERSION_ID:-unknown}" "$codename"
  exit 0
fi

nvidia_toolkit_packages=(
  nvidia-container-toolkit
  nvidia-container-toolkit-base
  libnvidia-container-tools
  libnvidia-container1
)
nvidia_toolkit_action=
if (( gpu_mode == 1 )); then
  command -v dpkg-query >/dev/null 2>&1 ||
    die "GPU mode requires dpkg-query to classify the NVIDIA container toolkit package state"
  nvidia_toolkit_query_temp=$(mktemp) ||
    die "could not create NVIDIA container toolkit query temporary file"
  trap 'rm -f -- "$nvidia_keyring_temp" "$nvidia_toolkit_query_temp"' EXIT
  nvidia_toolkit_records=()
  for package in "${nvidia_toolkit_packages[@]}"; do
    : >"$nvidia_toolkit_query_temp"
    if dpkg-query -W -f='${Package}\t${Status}\t${Version}\n' "$package" \
      >"$nvidia_toolkit_query_temp" 2>/dev/null; then
      query_status=0
    else
      query_status=$?
    fi
    if (( query_status == 0 )); then
      mapfile -t package_records <"$nvidia_toolkit_query_temp"
      [[ ${#package_records[@]} -eq 1 && -n "${package_records[0]}" ]] ||
        die "NVIDIA container toolkit package state is malformed for $package; repair the official four-package set and retry"
      nvidia_toolkit_records+=("${package_records[0]}")
    elif (( query_status == 1 )); then
      [[ ! -s "$nvidia_toolkit_query_temp" ]] ||
        die "NVIDIA container toolkit package state is ambiguous for $package; repair the official four-package set and retry"
      nvidia_toolkit_records+=("$package"$'\t''absent'$'\t''-')
    else
      die "NVIDIA container toolkit package query failed for $package with status $query_status; repair dpkg-query and retry"
    fi
  done
  rm -f -- "$nvidia_toolkit_query_temp"
  trap 'rm -f -- "$nvidia_keyring_temp"' EXIT

  [[ ${#nvidia_toolkit_records[@]} -eq ${#nvidia_toolkit_packages[@]} ]] ||
    die "NVIDIA container toolkit package state is incomplete; repair the official four-package set and retry"
  nvidia_toolkit_versions=()
  nvidia_toolkit_absent=0
  for index in "${!nvidia_toolkit_packages[@]}"; do
    IFS=$'\t' read -r query_package query_status query_version query_extra \
      <<<"${nvidia_toolkit_records[index]}"
    [[ "$query_package" == "${nvidia_toolkit_packages[index]}" &&
       -z "${query_extra:-}" ]] ||
      die "NVIDIA container toolkit package state has an unexpected record; repair the official four-package set and retry"
    if [[ "$query_status" == "install ok installed" ||
          "$query_status" == "hold ok installed" ]]; then
      [[ -n "$query_version" && "$query_version" =~ ^[^[:space:]]+$ ]] ||
        die "NVIDIA container toolkit package state has an invalid version for $query_package; repair the official four-package set and retry"
      nvidia_toolkit_versions+=("$query_version")
    elif [[ "$query_status" == absent && "$query_version" == - ]]; then
      (( nvidia_toolkit_absent += 1 ))
    else
      die "NVIDIA container toolkit package state is unsupported for $query_package; repair the official four-package set and retry"
    fi
  done

  if (( ${#nvidia_toolkit_versions[@]} == ${#nvidia_toolkit_packages[@]} )); then
    for version in "${nvidia_toolkit_versions[@]:1}"; do
      [[ "$version" == "${nvidia_toolkit_versions[0]}" ]] ||
        die "NVIDIA container toolkit package state is version-skewed; align the official four-package set and retry"
    done
    command -v nvidia-ctk >/dev/null 2>&1 ||
      die "NVIDIA container toolkit package state is coherent but nvidia-ctk is unavailable; repair the official four-package set and retry"
    nvidia_toolkit_action=reuse
  elif (( nvidia_toolkit_absent == ${#nvidia_toolkit_packages[@]} )); then
    nvidia_toolkit_action=install
  else
    die "NVIDIA container toolkit package state is partial; install or remove the complete official four-package set and retry"
  fi
fi

dry_run=${STACK_BOOTSTRAP_DRY_RUN:-0}
[[ "$dry_run" == 0 || "$dry_run" == 1 ]] ||
  die "STACK_BOOTSTRAP_DRY_RUN must be 0 or 1"

if (( EUID == 0 )); then
  root_command=()
  target_user=${SUDO_USER:-$(id -un)}
else
  root_command=(sudo)
  target_user=$(id -un)
fi

print_command() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
}

run_root() {
  if [[ "$dry_run" == 1 ]]; then
    print_command "${root_command[@]}" "$@"
  else
    "${root_command[@]}" "$@"
  fi
}

write_root_file() {
  local destination=$1
  local content=$2
  if [[ "$dry_run" == 1 ]]; then
    printf '+ write %s\n%s\n' "$destination" "$content"
  elif (( ${#root_command[@]} == 0 )); then
    printf '%s\n' "$content" >"$destination"
  else
    printf '%s\n' "$content" | "${root_command[@]}" tee "$destination" >/dev/null
  fi
}

write_root_file_from_path() {
  local destination=$1
  local source=$2
  if [[ "$dry_run" == 1 ]]; then
    printf '+ write %s from %s\n' "$destination" "$source"
  elif (( ${#root_command[@]} == 0 )); then
    cat -- "$source" >"$destination"
  else
    "${root_command[@]}" tee "$destination" <"$source" >/dev/null
  fi
}

daemon_json_fingerprint() {
  local digest
  if run_root test -e /etc/docker/daemon.json; then
    digest=$(run_root sha256sum /etc/docker/daemon.json) ||
      die "could not hash /etc/docker/daemon.json"
    printf 'present:%s\n' "${digest%%[[:space:]]*}"
  else
    printf 'absent\n'
  fi
}

prerequisite_packages=(
  ca-certificates curl gnupg tar gzip openssl util-linux coreutils jq python3 procps
)
conflicting_packages=(
  docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc
)

installed_conflicts=()
for package in "${conflicting_packages[@]}"; do
  if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" == "install ok installed" ]]; then
    installed_conflicts+=("$package")
  fi
done
if (( ${#installed_conflicts[@]} > 0 )); then
  run_root apt-get remove --yes "${installed_conflicts[@]}"
fi

run_root apt-get update
run_root apt-get install --yes "${prerequisite_packages[@]}"
run_root install -m 0755 -d /etc/apt/keyrings
run_root curl --fail --silent --show-error --location \
  "${docker_repo_base%/}/gpg" -o /etc/apt/keyrings/docker.asc
run_root chmod a+r /etc/apt/keyrings/docker.asc

docker_sources=$(cat <<EOF
Types: deb
URIs: ${docker_repo_base%/}
Suites: ${codename}
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
)
write_root_file /etc/apt/sources.list.d/docker.sources "$docker_sources"

run_root apt-get update
run_root apt-get install --yes "${docker_packages[@]}"
run_root systemctl enable --now docker

if (( gpu_mode == 1 )); then
  if [[ "$nvidia_toolkit_action" == install ]]; then
    run_root install -m 0755 -d /usr/share/keyrings /etc/apt/sources.list.d
    if [[ "$dry_run" == 1 ]]; then
      printf '+ curl --fail --silent --show-error --location %q | gpg --dearmor | write %s\n' \
        "$nvidia_gpg_url" "$nvidia_keyring_path"
    else
      write_root_file_from_path "$nvidia_keyring_path" "$nvidia_keyring_temp"
      rm -f -- "$nvidia_keyring_temp"
      trap - EXIT
    fi
    run_root chmod a+r "$nvidia_keyring_path"
    printf '+ fetched %s\n' "$nvidia_repository_url"
    write_root_file "$nvidia_sources_path" "$nvidia_signed_repository_list"
    run_root apt-get update
    run_root apt-get install --yes --no-install-recommends \
      "${nvidia_toolkit_packages[@]}"
  else
    rm -f -- "$nvidia_keyring_temp"
    trap - EXIT
  fi

  if [[ "$dry_run" == 1 ]]; then
    run_root nvidia-ctk runtime configure --runtime=docker
    run_root systemctl restart docker
  else
    daemon_json_before=$(daemon_json_fingerprint)
    run_root nvidia-ctk runtime configure --runtime=docker ||
      die "NVIDIA container runtime configuration failed"
    daemon_json_after=$(daemon_json_fingerprint)
    if [[ "$daemon_json_before" != "$daemon_json_after" ]]; then
      run_root systemctl restart docker
    fi
  fi
fi

if ! getent group docker >/dev/null 2>&1; then
  run_root groupadd docker
fi
[[ -n "$target_user" ]] || die "could not determine the login user for Docker group membership"
run_root usermod -aG docker "$target_user"
if [[ "$dry_run" == 1 ]]; then
  printf '+ verify Docker group membership for %q\n' "$target_user"
elif ! id -nG "$target_user" | tr ' ' '\n' | grep --fixed-strings --line-regexp --quiet docker; then
  die "Docker group membership verification failed for user $target_user"
fi

host_sysctl_config=$(cat <<'EOF'
vm.max_map_count=262144
net.ipv4.ip_forward=1
EOF
)
write_root_file /etc/sysctl.d/99-remote-infra-stack.conf "$host_sysctl_config"
run_root sysctl --system

verify_sysctl_setting() {
  local key=$1
  local expected=$2
  local actual

  if [[ "$dry_run" == 1 ]]; then
    printf '+ verify sysctl %q equals %q\n' "$key" "$expected"
    run_root sysctl -n "$key"
    return
  fi
  actual=$(run_root sysctl -n "$key") || die "could not read $key after applying host kernel settings"
  [[ "$actual" == "$expected" ]] ||
    die "$key verification failed; expected $expected, found ${actual:-empty}"
}

if [[ "$dry_run" == 1 ]]; then
  run_root docker version
  run_root docker compose version
  run_root systemctl is-active docker
  verify_sysctl_setting vm.max_map_count 262144
  verify_sysctl_setting net.ipv4.ip_forward 1
  if (( gpu_mode == 1 )); then
    run_root docker run --rm --gpus all "$cuda_image" \
      nvidia-smi --query-gpu=name --format=csv,noheader
    printf '+ verify GPU container output is exactly one NVIDIA T4 (NVIDIA T4 or Tesla T4)\n'
  fi
  printf 'Dry run complete; no privileged changes were executed.\n'
  exit 0
fi

run_root docker version
run_root docker compose version
run_root systemctl is-active --quiet docker || die "Docker service is not active"
verify_sysctl_setting vm.max_map_count 262144
verify_sysctl_setting net.ipv4.ip_forward 1
if (( gpu_mode == 1 )); then
  container_gpu_output_temp=$(mktemp) || die "could not create GPU validation temporary file"
  trap 'rm -f -- "$container_gpu_output_temp"' EXIT
  if ! run_root docker run --rm --gpus all "$cuda_image" \
    nvidia-smi --query-gpu=name --format=csv,noheader >"$container_gpu_output_temp"; then
    die "GPU container validation command failed"
  fi
  mapfile -t container_gpu_names <"$container_gpu_output_temp"
  rm -f -- "$container_gpu_output_temp"
  trap - EXIT
  [[ ${#container_gpu_names[@]} -eq 1 ]] ||
    die "GPU container validation requires exactly one NVIDIA T4; nvidia-smi may label it Tesla T4"
  is_t4_gpu_name "${container_gpu_names[0]}" ||
    die "GPU container validation requires exactly one NVIDIA T4; nvidia-smi may label it Tesla T4"
fi
printf 'Docker bootstrap complete for Ubuntu %s (%s), amd64; log out and back in for Docker group access.\n' \
  "${VERSION_ID:-unknown}" "$codename"
