#!/bin/sh
set -e

# Starts as root so it can fix up /data, then runs Dapple as PUID:PGID — the
# same convention as LinuxServer.io images. Docker creates a missing bind-mount
# folder as root, so anyone who skipped `mkdir data` would otherwise get a folder
# Dapple can't write to. Only root-owned files are claimed; anything the user
# owns is left alone.
if [ "$(id -u)" = 0 ]; then
    uid="${PUID:-1000}"
    gid="${PGID:-1000}"
    # Docker Desktop's shared folders don't always support chown, and there the
    # container can write regardless, so a failure here is only worth a warning.
    find /data -xdev -user 0 -exec chown "$uid:$gid" {} + 2>/dev/null \
        || echo "dapple: couldn't change ownership of /data to $uid:$gid; continuing" >&2
    exec setpriv --reuid="$uid" --regid="$gid" --clear-groups -- "$@"
fi

# Started as a non-root user (`user:`, runAsUser): no rights to fix anything up.
exec "$@"
