# syntax=docker/dockerfile:1.10
FROM alpine:3.22.2

RUN apk add --no-cache rsync ca-certificates openssh-client \
    && update-ca-certificates

RUN getent group rsync >/dev/null || addgroup -S rsync \
 && getent passwd rsync >/dev/null || adduser -S -H -s /sbin/nologin -G rsync rsync

WORKDIR /app

USER rsync

ENTRYPOINT ["rsync"]
CMD ["--help"]
