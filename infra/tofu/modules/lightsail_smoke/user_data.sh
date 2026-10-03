#!/bin/bash
# Bootstrap only: transfer prebuilt amd64 images and runtime configuration over SSH.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io curl ca-certificates
systemctl enable --now docker
usermod -aG docker ubuntu
install -d -m 0755 /usr/local/lib/docker/cli-plugins
install -d -o ubuntu -g ubuntu -m 0750 /opt/minsky

# Official v2.39.4 linux-x86_64 asset digest; verify before installing as root.
compose_version=2.39.4
compose_sha256=7af95166a730b87e172d4fc9aefea8725d3c6c7327d59149267b452114ddb7d4
compose_dir=$(mktemp -d)
trap 'rm -rf "$compose_dir"' EXIT
curl -fsSL "https://github.com/docker/compose/releases/download/v${compose_version}/docker-compose-linux-x86_64" \
  -o "$compose_dir/docker-compose"
printf '%s  %s\n' "$compose_sha256" "$compose_dir/docker-compose" | sha256sum -c -
install -m 0755 "$compose_dir/docker-compose" /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version
