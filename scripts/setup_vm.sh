#!/bin/bash
# Set up a fresh Ubuntu 24.04 machine to run errata-bench's tasks under Harbor
# and grade them: Docker with its Compose and buildx plugins (Docker's own
# packages), uv, Harbor 0.23.0 in its own environment, and errata-bench at the
# given tag in another. Idempotent. No key is read or written here: the model
# provider's settings go in <checkout>/.env afterwards, copied by hand.
#
#   sudo bash scripts/setup_vm.sh [tag] [home]      # default: v1.0.4, /home/azureuser
#
# The run's own machine, not a shared one: Harbor builds each task's image and
# Docker's build cache grows with every task, which a shared server's disk
# could not hold (09-28).
set -euo pipefail
TAG="${1:-v1.0.4}"; HOME_DIR="${2:-/home/azureuser}"; USER_NAME="$(basename "$HOME_DIR")"
export DEBIAN_FRONTEND=noninteractive

# Docker, from Docker's own repository: the engine, and the Compose and buildx
# plugins Harbor needs (the distribution's docker.io has neither plugin).
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  apt-get update -q
  apt-get install -yq ca-certificates curl gnupg git jq rsync python3-venv nftables
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
usermod -aG docker "$USER_NAME"
# Harbor's network rule is an nftables sidecar: the kernel must have it.
modprobe nf_tables 2>/dev/null || true
lsmod | grep -q nf_tables || { echo "nf_tables is not available: Harbor's network rule cannot run" >&2; exit 1; }

# uv, pinned, from PyPI into its own environment; Python 3.14 for Harbor.
if [ ! -x /opt/uv/bin/uv ]; then
  python3 -m venv /opt/uv && /opt/uv/bin/pip install -q uv==0.8.22
fi
UV=/opt/uv/bin/uv
sudo -u "$USER_NAME" -H bash -s -- "$TAG" "$HOME_DIR" "$UV" <<'AS_USER'
set -euo pipefail
TAG="$1"; HOME_DIR="$2"; UV="$3"
cd "$HOME_DIR"
[ -d errata-bench ] || git clone -q https://github.com/zanwenfu/errata-bench.git
cd errata-bench && git fetch -q --tags && git checkout -q "$TAG"
# errata-bench's own environment, from its lock, to grade.
[ -x .venv/bin/python ] || "$UV" venv -q --python 3.12 .venv
"$UV" pip install -q --python .venv/bin/python -r requirements-lock.txt
"$UV" pip install -q --python .venv/bin/python --no-deps -e .
# Harbor's, beside it: Harbor needs openai below 3, errata-bench's lock pins 3.13.
[ -x "$HOME_DIR/harbor-env/bin/python" ] || "$UV" venv -q --python 3.14 "$HOME_DIR/harbor-env"
"$UV" pip install -q --python "$HOME_DIR/harbor-env/bin/python" harbor==0.23.0
"$UV" pip install -q --python "$HOME_DIR/harbor-env/bin/python" --no-deps -e .
echo "errata-bench $(git describe --tags --always) in $HOME_DIR/errata-bench; Harbor in $HOME_DIR/harbor-env"
AS_USER
docker --version; docker compose version; docker buildx version
echo "done: log out and in again for the docker group; put the provider's settings in the checkout's .env"
