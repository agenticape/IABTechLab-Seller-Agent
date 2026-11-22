#!/bin/sh
# Container entry point for the hosted mock-only sandbox.
#
# Fly mounts a fresh volume at /data owned by root, so this starts as root,
# hands /data to the unprivileged "seller" user, then runs
# sandbox/fly_server.py as that user. Arguments are passed through:
#
#   entrypoint.sh            serve (default)
#   entrypoint.sh rekey      revoke + re-mint the sandbox API keys
#   entrypoint.sh check      print the effective mock-only config
set -eu

DATA_DIR="${DATA_DIR:-/data}"
export DATA_DIR

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown -R seller:seller "$DATA_DIR"
  chmod 700 "$DATA_DIR"
  export HOME=/home/seller
  exec setpriv --reuid=seller --regid=seller --init-groups \
    /app/.venv/bin/python /app/sandbox/fly_server.py "$@"
fi

exec /app/.venv/bin/python /app/sandbox/fly_server.py "$@"
