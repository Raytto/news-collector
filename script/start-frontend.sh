#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/frontend"

NODE_BIN=""
if command -v node >/dev/null 2>&1; then
  NODE_BIN="$(command -v node)"
else
  # Bootstrap a local Node.js (LTS v20) if not installed
  OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
  ARCH_RAW="$(uname -m)"
  case "$ARCH_RAW" in
    x86_64|amd64) ARCH="x64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "Unsupported arch: $ARCH_RAW" >&2; exit 1 ;;
  esac
  NODE_VER="v20.18.0"
  NODE_DIR="$ROOT_DIR/.cache/node-$NODE_VER-$OS-$ARCH"
  NODE_TGZ="node-$NODE_VER-$OS-$ARCH.tar.xz"
  if [ ! -x "$NODE_DIR/bin/node" ]; then
    mkdir -p "$ROOT_DIR/.cache"
    curl -fsSLo "$ROOT_DIR/.cache/$NODE_TGZ" "https://nodejs.org/dist/$NODE_VER/$NODE_TGZ"
    rm -rf "$NODE_DIR"
    tar -xJf "$ROOT_DIR/.cache/$NODE_TGZ" -C "$ROOT_DIR/.cache"
  fi
  export PATH="$NODE_DIR/bin:$PATH"
  NODE_BIN="$NODE_DIR/bin/node"
fi

if [ -z "$NODE_BIN" ]; then
  echo "Failed to find or install Node.js" >&2
  exit 1
fi

# Pick package manager
if command -v pnpm >/dev/null 2>&1; then
  PKG=pnpm
elif command -v yarn >/dev/null 2>&1; then
  PKG=yarn
else
  PKG=npm
fi

if [ "$PKG" = npm ]; then
  npm install --silent
  npm run dev
else
  $PKG install --silent
  $PKG run dev
fi
