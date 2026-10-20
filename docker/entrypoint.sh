#!/bin/sh
# Start the panel as PUID:PGID instead of root. /data is handed to that user the first time (installs from before
# this image ran as root, so their data is root-owned). Started as a non-root user already (docker run --user):
# run as that user and leave /data alone.
set -e
if [ "$(id -u)" = 0 ]; then
  uid=${PUID:-1000}
  gid=${PGID:-1000}
  mkdir -p "${DATA_DIR:-/data}"
  if [ "$(stat -c %u "${DATA_DIR:-/data}")" != "$uid" ]; then
    echo "entrypoint: giving ${DATA_DIR:-/data} to $uid:$gid"
    chown -R "$uid:$gid" "${DATA_DIR:-/data}"
  fi
  export HOME="${DATA_DIR:-/data}"            # root's home is not writable once privileges drop
  exec setpriv --reuid="$uid" --regid="$gid" --clear-groups "$@"
fi
exec "$@"
