# syntax=docker/dockerfile:1.21@sha256:27f9262d43452075f3c410287a2c43f5ef1bf7ec2bb06e8c9eeb1b8d453087bc
FROM alpine:3.23.3@sha256:25109184c71bdad752c8312a8623239686a9a2071e8825f20acb8f2198c3f659

# `apk add` already resolves rsync/ca-certificates/openssh-client to
# whatever is newest in the repo at build time - it is not pinned, so it
# is not reproducible today regardless. Running `apk upgrade` first to
# also pull latest patches for the *base image's own* packages
# (libcrypto3, libssl3, musl, musl-utils, zlib) does not weaken that
# reproducibility any further: it is the same "whatever's newest right
# now" contract already implied by the unpinned `apk add` below.
# This is needed because Alpine publishes patched packages to its repo
# before the alpine base image itself is rebuilt and republished with
# them baked in, so the pinned base digest above can lag a fixed CVE by
# days even though the fix is already available via `apk`.
RUN apk --no-cache upgrade \
    && apk add --no-cache rsync ca-certificates openssh-client \
    && update-ca-certificates

RUN getent group rsync >/dev/null || addgroup -S rsync \
 && getent passwd rsync >/dev/null || adduser -S -H -s /sbin/nologin -G rsync rsync

WORKDIR /app

USER rsync

ENTRYPOINT ["rsync"]
CMD ["--help"]
