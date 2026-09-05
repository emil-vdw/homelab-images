#!/bin/bash
set -euo pipefail
umask 077

# Utilities such as login and pairing use the same persistent home as the host.
if (( $# )); then
    exec "$@"
fi

mkdir -p "$CODEX_HOME/packages" "$HOME/workspaces"
# Runtime state stays writable, but the managed executable comes from the image.
# Refuse to replace a directory left by a separately managed installation.
ln -sfnT /opt/codex/packages/standalone "$CODEX_HOME/packages/standalone"

shutdown() {
    trap - TERM INT EXIT
    timeout 20 codex app-server daemon stop || true
}
trap 'exit 0' TERM INT
trap shutdown EXIT

# remote-control start/bootstrap also launches an updater. These lower-level
# commands enable the same managed host and pairing socket without the updater.
codex app-server daemon enable-remote-control
codex app-server daemon start

# version performs the initialize handshake on the control socket. Exiting on
# failure lets the container runtime restart the host, including a wedged daemon.
while timeout 10 codex app-server daemon version >/dev/null; do
    sleep 15 &
    wait "$!"
done
echo 'Codex daemon stopped responding; exiting for container restart.' >&2
exit 1
