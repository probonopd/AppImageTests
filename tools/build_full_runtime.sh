#!/bin/sh
# Runs inside an alpine:3.21 container (see .github/workflows/build-runtime.yml).
# Builds the AppImage type2-runtime exactly like upstream, but with squashfuse linked against
# every squashfs codec (zlib, zstd, xz/lzma, lzo, lz4), so all variants can be mounted and
# measured. Output: /out/runtime-x86_64 (plus build info).
set -eux
TYPE2_COMMIT="${TYPE2_COMMIT:-8f39b89e2ac31e1640b3d3f7e9a5108e6ce805fa}"

apk add --no-cache bash alpine-sdk util-linux strace file autoconf automake libtool xz git \
    eudev-dev gettext-dev linux-headers meson wget \
    zstd-dev zstd-static zlib-dev zlib-static clang musl-dev mimalloc-dev \
    xz-dev lz4-dev lz4-static lzo-dev
apk add --no-cache xz-static || true
apk add --no-cache lzo-static || true

cd /tmp
git clone https://github.com/AppImage/type2-runtime.git
cd type2-runtime
git checkout "$TYPE2_COMMIT"

# 1) squashfuse: enable every codec
sed -i 's|./configure LDFLAGS="-static"|./configure LDFLAGS="-static" --with-zlib=/usr --with-zstd=/usr --with-xz=/usr --with-lzo=/usr --with-lz4=/usr|' \
    scripts/common/install-dependencies.sh
grep -q -- '--with-xz' scripts/common/install-dependencies.sh
# 2) runtime: link the extra codec libraries
sed -i 's|-lzstd -lz |-lzstd -lz -llzma -llzo2 -llz4 |' src/runtime/Makefile
grep -q -- '-llzma' src/runtime/Makefile

bash scripts/common/install-dependencies.sh
# show which codecs squashfuse really got
grep -hE 'define SQFS_.*(ZLIB|ZSTD|LZMA|LZO|LZ4)|define HAVE_.*(LZMA|LZO|LZ4|ZSTD|ZLIB)' /tmp/*/squashfuse*/config.h 2>/dev/null || true

bash scripts/build-runtime.sh
mkdir -p /out
cp out/runtime-x86_64 /out/runtime-x86_64
{
  echo "type2-runtime commit: $TYPE2_COMMIT"
  echo "codecs: zlib zstd xz lzo lz4"
  sha256sum /out/runtime-x86_64
} > /out/build-info.txt
cat /out/build-info.txt
