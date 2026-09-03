#!/bin/sh
set -eu

# Container args are the full command to run inside tmux (e.g. `claude
# remote-control ...`) - the image itself hardcodes none, see Dockerfile.
if [ "$#" -eq 0 ]; then
  echo "error: no command given (container args must be the command to run, e.g. 'claude remote-control ...')" >&2
  exit 1
fi

# tmux refuses to start without a writable socket dir. This must be
# EPHEMERAL, not on the PVC: a tmux socket is meaningless once its server
# is gone, and an unclean pod termination leaves the file behind. tmux
# normally unlinks a dead socket and starts a fresh server, but the
# recovery is not guaranteed and the failure mode is a pod that will not
# start. The container's own writable layer dies with the container, which
# is exactly the lifetime a socket dir should have.
#
# Overridable so a caller that does set readOnlyRootFilesystem can point
# this at an emptyDir instead.
: "${TMUX_TMPDIR:=/tmp/tmux-claude}"
export TMUX_TMPDIR
rm -rf "$TMUX_TMPDIR"
mkdir -p "$TMUX_TMPDIR"

# "$@" is passed as separate argv entries rather than one joined string.
# tmux >= 3.0 execs argv directly when given more than one argument and
# falls back to running a single argument through the shell; the former is
# what we want, since it needs no quoting and no shell in between. Verify
# this on the first real build - it is the single load-bearing line here.
tmux new-session -d -s claude "$@"

# `tmux new-session` can return 0 even though the wrapped command exits
# immediately afterwards (bad flags, missing auth, etc.). Without this
# check, the poll loop below would find the session already gone on its
# very first look and that's indistinguishable from a clean shutdown.
sleep 1
if ! tmux has-session -t claude 2>/dev/null; then
  echo "error: tmux session 'claude' died within 1s of starting - command failed immediately" >&2
  exit 1
fi

# Deliberately not piping the pane to stdout (tmux pipe-pane or similar):
# the Ink TUI that Claude Code renders redraws the whole screen
# continuously, which would flood the container log with ANSI escapes
# instead of anything an operator could read. Print the attach command
# once instead.
echo "claude remote-control is running in tmux - attach with: kubectl exec -it <pod> -- tmux attach -t claude"

while tmux has-session -t claude 2>/dev/null; do
  sleep 5
done

# Session is gone - exit non-zero so Kubernetes restarts the pod rather
# than leaving a container alive with nothing running inside it.
echo "error: tmux session 'claude' exited - restarting pod" >&2
exit 1
