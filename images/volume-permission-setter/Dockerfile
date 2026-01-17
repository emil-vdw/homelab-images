# syntax=docker/dockerfile:1.10
FROM busybox:1.36.1

WORKDIR /app

COPY entrypoint.sh /app/entrypoint.sh

RUN chmod 0755 /app/entrypoint.sh

ENV TARGET="" \
    WANT_UID="" \
    WANT_GID="" \
    WANT_MODE=""

ENTRYPOINT ["/app/entrypoint.sh"]
