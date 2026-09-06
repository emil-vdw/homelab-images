#!/bin/bash
# Runs locally or in CI without account credentials or model requests.
set -euo pipefail
image="${1:-local/codex:ci}"
name="codex-smoke-$$"
volume="$name-home"
cleanup() {
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT
start() {
    docker run -d --name "$name" --hostname codex-smoke \
        --cap-drop ALL --security-opt no-new-privileges --read-only \
        --tmpfs /tmp:rw,nosuid,nodev,mode=1777 \
        --mount "type=volume,source=$volume,target=/home/codex" \
        "$image" >/dev/null
    for ((attempt = 0; attempt < 30; attempt++)); do
        if docker exec "$name" timeout 5 codex app-server daemon version >/dev/null 2>&1; then
            return
        fi
        sleep 1
    done
    docker logs "$name"
    return 1
}

start
docker exec "$name" bash -euc '
    test "$(id -u)" = 20213
    test ! -e "$CODEX_HOME/app-server-daemon/app-server-updater.pid"
    test ! -w /opt/codex/packages/standalone
    printf preserved > "$HOME/workspaces/marker"
    if touch /etc/codex-write-test; then
        echo "Container allowed a write to the image filesystem" >&2
        exit 1
    fi
    git --version
    gh --version
    node --version
    python3 --version
    kubectl version --client
    flux --version
    helm version --short
'

# A dead daemon must make the probe fail and the supervisor exit nonzero.
docker exec "$name" bash -euc 'kill -KILL "$(pgrep -f "^/home/codex/.codex/packages/standalone/current/codex app-server")"'
if docker exec "$name" timeout 5 codex app-server daemon version >/dev/null 2>&1; then
    echo 'Health probe accepted a dead daemon' >&2
    exit 1
fi
test "$(timeout 35 docker wait "$name")" = 1
docker rm "$name" >/dev/null

# Recreate on the existing home, including stale daemon state after a crash.
start
docker exec "$name" test -s /home/codex/workspaces/marker
docker stop --time 30 "$name" >/dev/null
test "$(docker inspect --format '{{.State.ExitCode}}' "$name")" = 0
echo 'Passed: daemon health/failure, read-only image, crash recovery, persistence, graceful shutdown.'
