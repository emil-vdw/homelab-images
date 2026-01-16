#!/bin/sh
set -eu

log() { printf '%s\n' "$*"; }
die() { log "error: $*"; exit 1; }

# Required env vars
: "${TARGET:?set TARGET (e.g. /data)}"
: "${WANT_UID:?set WANT_UID (e.g. 10001)}"
: "${WANT_GID:?set WANT_GID (e.g. 10001)}"
: "${WANT_MODE:?set WANT_MODE (octal, e.g. 2775)}"

# Basic validation (numeric / octal-ish)
case "$WANT_UID" in (''|*[!0-9]*) die "WANT_UID must be numeric (got: $WANT_UID)";; esac
case "$WANT_GID" in (''|*[!0-9]*) die "WANT_GID must be numeric (got: $WANT_GID)";; esac
case "$WANT_MODE" in
  (''|*[!0-7]*) die "WANT_MODE must be octal digits only (e.g. 2775) (got: $WANT_MODE)";;
esac

[ -d "$TARGET" ] || die "TARGET is not a directory or not accessible: $TARGET"

have_uid="$(stat -c '%u' "$TARGET")"
have_gid="$(stat -c '%g' "$TARGET")"
have_mode="$(stat -c '%a' "$TARGET")"

log "perm-check: path=$TARGET have_uid=$have_uid have_gid=$have_gid have_mode=$have_mode want_uid=$WANT_UID want_gid=$WANT_GID want_mode=$WANT_MODE"

need_fix=0
[ "$have_uid" -ne "$WANT_UID" ] && need_fix=1
[ "$have_gid" -ne "$WANT_GID" ] && need_fix=1
[ "$have_mode" != "$WANT_MODE" ] && need_fix=1

if [ "$need_fix" -eq 0 ]; then
  log "perm-set: base dir already correct; skipping"
  exit 0
fi

log "perm-set: base dir mismatch; applying recursive ownership + directory chmod..."

# Ownership: recursive (can be slow on large trees / NFS)
chown -R "${WANT_UID}:${WANT_GID}" "$TARGET"

# Enforce desired mode
chmod "$WANT_MODE" "$TARGET"

log "perm-set: done"
