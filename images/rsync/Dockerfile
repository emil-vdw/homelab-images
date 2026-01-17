# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d
FROM alpine:3.23.2@sha256:865b95f46d98cf867a156fe4a135ad3fe50d2056aa3f25ed31662dff6da4eb62

RUN apk add --no-cache rsync ca-certificates openssh-client \
    && update-ca-certificates

RUN getent group rsync >/dev/null || addgroup -S rsync \
 && getent passwd rsync >/dev/null || adduser -S -H -s /sbin/nologin -G rsync rsync

WORKDIR /app

USER rsync

ENTRYPOINT ["rsync"]
CMD ["--help"]
