# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d
FROM busybox:1.37.0@sha256:2383baad1860bbe9d8a7a843775048fd07d8afe292b94bd876df64a69aae7cb1

WORKDIR /app

COPY entrypoint.sh /app/entrypoint.sh

RUN chmod 0755 /app/entrypoint.sh

ENV TARGET="" \
    WANT_UID="" \
    WANT_GID="" \
    WANT_MODE=""

ENTRYPOINT ["/app/entrypoint.sh"]
