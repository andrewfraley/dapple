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

    # A PUID that doesn't match the folder's owner otherwise surfaces as a Python
    # traceback. Warn rather than exit: Docker Desktop's shared folders can answer
    # access checks unreliably, and a false alarm there would crash-loop.
    bad=$(setpriv --reuid="$uid" --regid="$gid" --clear-groups -- sh -c '
        for f in /data /data/*; do
            [ -e "$f" ] || continue
            { [ -r "$f" ] && [ -w "$f" ]; } || { echo "$f"; break; }
        done')
    if [ -n "$bad" ]; then
        owner=$(stat -c %u "$bad")
        if [ "$owner" = "$uid" ]; then
            echo "dapple: $bad isn't readable and writable by its owner (uid $uid). Check its permissions." >&2
        else
            echo "dapple: $bad belongs to uid $owner, but PUID is $uid, so Dapple can't use it." >&2
            echo "dapple: Set PUID and PGID in docker-compose.yml to the data folder's owner (ls -ln shows it), then run: docker compose up -d" >&2
        fi
    fi

    exec setpriv --reuid="$uid" --regid="$gid" --clear-groups -- "$@"
fi

# Started as a non-root user (`user:`, runAsUser): no rights to fix anything up.
exec "$@"
