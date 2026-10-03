#!/bin/bash
# Bootstrap only: transfer prebuilt amd64 images and runtime configuration over SSH.
set -euo pipefail
apt-get update
apt-get install -y docker.io curl
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins /opt/minsky
curl -fsSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
