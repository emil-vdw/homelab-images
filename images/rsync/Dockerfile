# syntax=docker/dockerfile:1.21@sha256:27f9262d43452075f3c410287a2c43f5ef1bf7ec2bb06e8c9eeb1b8d453087bc
FROM alpine:3.23.3@sha256:25109184c71bdad752c8312a8623239686a9a2071e8825f20acb8f2198c3f659

RUN apk add --no-cache rsync ca-certificates openssh-client \
    && update-ca-certificates

RUN getent group rsync >/dev/null || addgroup -S rsync \
 && getent passwd rsync >/dev/null || adduser -S -H -s /sbin/nologin -G rsync rsync

WORKDIR /app

USER rsync

ENTRYPOINT ["rsync"]
CMD ["--help"]
