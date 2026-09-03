#!/usr/bin/env bash
# Startup script for Google Cloud Compute Engine e2-micro instance
# Configures 2GB swap space, installs Docker, Docker Compose, Git, and prepares the environment.

set -euo pipefail

echo "==> Starting VM bootstrap script..."

# 1. Configure 2GB Swap space (Crucial for 1GB RAM e2-micro instance)
if [ ! -f /swapfile ]; then
    echo "==> Setting up 2GB swapfile..."
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    if ! grep -q '/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
    echo "==> Swapfile configured successfully."
else
    echo "==> Swapfile already exists. Skipping."
fi

# 2. Update packages and install Docker + Docker Compose + Git
echo "==> Installing Docker, Docker Compose, and Git..."
apt-get update -y
apt-get install -y --no-install-recommends \
    docker.io \
    docker-compose-v2 \
    git \
    curl \
    ca-certificates \
    htop \
    python3 \
    python3-pip \
    python3-venv

# 3. Enable and start Docker service
echo "==> Enabling Docker service..."
systemctl enable --now docker

# 4. Add standard cloud users to the docker group
for user in debian ubuntu admin; do
    if id "$user" &>/dev/null; then
        echo "==> Adding user $user to docker group..."
        usermod -aG docker "$user"
    fi
done

# 5. Create deployment directory
DEPLOY_DIR="/opt/reddit-bot"
mkdir -p "$DEPLOY_DIR"
chmod 755 "$DEPLOY_DIR"

echo "==> VM bootstrap completed successfully at $(date)."
