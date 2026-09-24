#!/bin/sh
set -e

# Docker creates a missing bind-mount source as root, so anyone who skipped
# `mkdir data` gets a folder Dapple can't write to. Starting as root lets us hand
# anything root-owned in /data to DAPPLE_UID:DAPPLE_GID before dropping to it.
# Files owned by anyone else are left alone.
if [ "$(id -u)" = 0 ]; then
    uid="${DAPPLE_UID:-1000}"
    gid="${DAPPLE_GID:-1000}"
    find /data -xdev -user 0 -exec chown "$uid:$gid" {} +
    exec setpriv --reuid="$uid" --regid="$gid" --clear-groups -- "$@"
fi

# Started with an explicit `user:` (older compose files did this) — nothing to fix.
exec "$@"
