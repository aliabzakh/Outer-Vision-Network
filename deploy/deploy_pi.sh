#!/usr/bin/env bash
# Run on the Mac: copy the code to the Pi and set it up.
#   deploy/deploy_pi.sh <user>@192.168.2.2 [remote_dir]
# Needs key-based SSH first:  ssh-copy-id <user>@192.168.2.2
set -euo pipefail
TARGET=${1:?usage: deploy/deploy_pi.sh user@192.168.2.2 [remote_dir]}
DEST=${2:-outer-vision}
cd "$(dirname "$0")/.."

if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$TARGET" true 2>/dev/null; then
  echo "Can't log in to $TARGET with a key. Check the cable/IP, then run: ssh-copy-id $TARGET" >&2
  exit 1
fi

echo "==> copying code to $TARGET:$DEST"
rsync -az --delete \
  --exclude .git --exclude '.venv*' --exclude recordings --exclude data --exclude __pycache__ --exclude deploy/wheels \
  ./ "$TARGET:$DEST/"

# A Pi on a direct cable usually has no internet: bring the one pip package it needs along.
if ! ssh "$TARGET" "curl -s -m 5 -o /dev/null https://pypi.org/simple/" 2>/dev/null; then
  echo "==> Pi has no internet; downloading wheels here"
  PYV=$(ssh "$TARGET" "python3 -c 'import sys; print(f\"{sys.version_info[0]}.{sys.version_info[1]}\")'")
  PIP=$( [ -x .venv/bin/pip ] && echo .venv/bin/pip || echo pip3 )
  rm -rf deploy/wheels && mkdir -p deploy/wheels
  "$PIP" download -q -d deploy/wheels --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 \
    --python-version "$PYV" --only-binary=:all: --no-deps "opencv-python-headless<4.11"
  rsync -az deploy/wheels "$TARGET:$DEST/deploy/"
fi

echo "==> setting up on the Pi"
ssh -t "$TARGET" "cd $DEST && bash deploy/setup_pi.sh"
