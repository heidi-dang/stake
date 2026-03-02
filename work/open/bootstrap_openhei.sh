#!/usr/bin/env bash
# Bootstrap script to clone and run repo checks for `openhei`.
# Usage:
#  WORKDIR="$HOME/work" FORCE=1 REPO="https://github.com/heidi-dang/openhei.git" ./work/open/bootstrap_openhei.sh

set -euo pipefail

WORKDIR="${WORKDIR:-$HOME/work}"
REPO="${REPO:-https://github.com/heidi-dang/openhei.git}"
REPO_DIR="${REPO_DIR:-openhei}"

echo "WORKDIR=$WORKDIR"
echo "REPO=$REPO"
echo "REPO_DIR=$REPO_DIR"

mkdir -p "$WORKDIR"
cd "$WORKDIR"

if [ -d "$REPO_DIR" ]; then
  if [ "${FORCE:-}" = "1" ]; then
    echo "Removing existing directory $WORKDIR/$REPO_DIR (FORCE=1)"
    rm -rf -- "$REPO_DIR"
  else
    echo "Directory $WORKDIR/$REPO_DIR already exists. To replace it set FORCE=1 and re-run. Exiting."
    exit 1
  fi
fi

echo "Cloning $REPO into $WORKDIR/$REPO_DIR"
git clone "$REPO" "$REPO_DIR"
cd "$REPO_DIR"

echo "Checking out main (if available)"
git checkout main || git checkout -b main || true
git pull --ff-only || true

echo "Installing dependencies (bun/npm)"
if command -v bun >/dev/null 2>&1; then
  bun -v || true
  bun install || true
else
  echo "bun not found; attempting npm install"
  command -v node >/dev/null 2>&1 && node -v || true
  npm install || true
fi

echo "Running repo checks (typecheck, test, build) if available"
if command -v bun >/dev/null 2>&1; then
  bun turbo typecheck || true
  bun turbo test || true
  bun turbo build || true
else
  echo "bun toolchain not available; skipping bun turbo commands"
fi

echo "Bootstrap finished. To run the dev server, run: bun run dev  (or) bun run start"
