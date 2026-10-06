#!/bin/bash
# Install benchmark tooling on a GitHub-hosted Ubuntu 24.04 runner (x86_64 or arm64).
set -euo pipefail
DWARFS_VERSION="${DWARFS_VERSION:-0.12.4}"
ARCH="$(uname -m)"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  squashfs-tools zsync nginx-light time xdelta3 fuse3 libfuse2t64 xvfb xdotool strace \
  zstd xz-utils brotli bzip3 python3-psutil python3-yaml python3-matplotlib \
  || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  squashfs-tools zsync nginx time xdelta3 fuse3 libfuse2t64 xvfb xdotool strace \
  zstd xz-utils brotli python3-psutil python3-yaml python3-matplotlib
sudo systemctl stop nginx 2>/dev/null || true       # we run our own instance
sudo sh -c 'echo user_allow_other >> /etc/fuse.conf' || true
# DwarFS universal binary acts as mkdwarfs/dwarfs when invoked via symlink
mkdir -p "$HOME/bin"
curl -fsSL -o "$HOME/bin/dwarfs-universal" \
  "https://github.com/mhx/dwarfs/releases/download/v${DWARFS_VERSION}/dwarfs-universal-${DWARFS_VERSION}-Linux-${ARCH}"
chmod +x "$HOME/bin/dwarfs-universal"
for t in mkdwarfs dwarfs dwarfsck; do ln -sf dwarfs-universal "$HOME/bin/$t"; done
echo "$HOME/bin" >> "${GITHUB_PATH:-/dev/null}"
export PATH="$HOME/bin:$PATH"
# bench scratch space on the big /mnt disk
sudo mkdir -p /mnt/bench && sudo chown "$USER" /mnt/bench
mksquashfs -version | head -1 || true
mkdwarfs --help >/dev/null 2>&1 || echo "WARNING: mkdwarfs unavailable, dwarfs variants will fail"
