#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.cargo/bin:$PATH"
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
build_root=${MAP_BUILD_DIR:-/home/ydl/map-build}
image_tag=${MAP_IMAGE_TAG:-china-road-map:1.1.1}
export CARGO_TARGET_DIR="$build_root/target"
export CC_x86_64_unknown_linux_musl=musl-gcc
mkdir -p "$build_root/image/web" "$build_root/artifacts"
cargo test --locked --manifest-path "$project_root/backend/Cargo.toml"
cargo build --locked --release --target x86_64-unknown-linux-musl --manifest-path "$project_root/backend/Cargo.toml"
install -m 755 "$CARGO_TARGET_DIR/x86_64-unknown-linux-musl/release/map-server" "$build_root/image/map-server"
rsync -a --delete "$project_root/web/" "$build_root/image/web/"
python3 "$project_root/scripts/precompress.py" "$build_root/image/web"
cp "$project_root/deploy/Dockerfile" "$build_root/image/Dockerfile"
docker build --network=none --platform linux/amd64 -t "$image_tag" "$build_root/image"
docker save "$image_tag" | gzip -1 > "$build_root/artifacts/map-image.tar.gz"
sha256sum "$build_root/artifacts/map-image.tar.gz" > "$build_root/artifacts/map-image.tar.gz.sha256"
docker image inspect "$image_tag" --format 'Image bytes: {{.Size}}'
ls -lh "$build_root/artifacts/map-image.tar.gz"
