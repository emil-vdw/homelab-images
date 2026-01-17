#!/bin/sh
set -eu

log() { printf '%s\n' "$*"; }
die() { log "error: $*"; exit 1; }

# Required env vars
: "${TARGET:?set TARGET (single dir or comma-separated list, e.g. /data or /config,/data)}"
: "${WANT_UID:?set WANT_UID (e.g. 10001)}"
: "${WANT_GID:?set WANT_GID (e.g. 10001)}"
: "${WANT_MODE:?set WANT_MODE (octal, e.g. 2775)}"

# Basic validation
case "$WANT_UID" in (''|*[!0-9]*) die "WANT_UID must be numeric (got: $WANT_UID)";; esac
case "$WANT_GID" in (''|*[!0-9]*) die "WANT_GID must be numeric (got: $WANT_GID)";; esac
case "$WANT_MODE" in (''|*[!0-7]*) die "WANT_MODE must be octal digits only (e.g. 2775) (got: $WANT_MODE)";; esac

# Iterate TARGET as either a single path or comma-separated list.
# Note: no spaces supported; keep it simple and explicit.
OLD_IFS="$IFS"
IFS=','

for path in $TARGET; do
  [ -n "$path" ] || die "empty path in TARGET list"
  [ -d "$path" ] || die "TARGET path is not a directory or not accessible: $path"

  have_uid="$(stat -c '%u' "$path")"
  have_gid="$(stat -c '%g' "$path")"
  have_mode="$(stat -c '%a' "$path")"

  log "perm-check: path=$path have_uid=$have_uid have_gid=$have_gid have_mode=$have_mode want_uid=$WANT_UID want_gid=$WANT_GID want_mode=$WANT_MODE"

  need_fix=0
  [ "$have_uid" -ne "$WANT_UID" ] && need_fix=1
  [ "$have_gid" -ne "$WANT_GID" ] && need_fix=1
  [ "$have_mode" != "$WANT_MODE" ] && need_fix=1

  if [ "$need_fix" -eq 1 ]; then
    log "perm-fix: base dir mismatch; applying recursive ownership + directory chmod..."
    chown -R "${WANT_UID}:${WANT_GID}" "$path"
    find "$path" -type d -exec chmod "$WANT_MODE" {} +
    log "perm-fix: done: $path"
  else
    log "perm-fix: base dir already correct; skipping: $path"
  fi
done

# Restore original IFS
IFS="$OLD_IFS"
