# syntax=docker/dockerfile:1.10
FROM alpine:3.22.2

RUN apk add --no-cache rsync ca-certificates openssh-client \
    && update-ca-certificates

RUN adduser -s -G rsync

WORKDIR /app

USER rsync

ENTRYPOINT ["rsync"]
CMD ["--help"]
