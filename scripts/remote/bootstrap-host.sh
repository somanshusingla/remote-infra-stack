#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

case "${1:-}" in
  --check|--install)
    mode=$1
    ;;
  *)
    die "usage: bootstrap-host.sh --check|--install"
    ;;
esac

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

if [[ "$mode" == --check ]]; then
  printf 'Host supports Docker bootstrap: Ubuntu %s (%s), amd64.\n' "${VERSION_ID:-unknown}" "$codename"
  exit 0
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
  printf 'Dry run complete; no privileged changes were executed.\n'
  exit 0
fi

run_root docker version
run_root docker compose version
run_root systemctl is-active --quiet docker || die "Docker service is not active"
verify_sysctl_setting vm.max_map_count 262144
verify_sysctl_setting net.ipv4.ip_forward 1
printf 'Docker bootstrap complete for Ubuntu %s (%s), amd64; log out and back in for Docker group access.\n' \
  "${VERSION_ID:-unknown}" "$codename"
