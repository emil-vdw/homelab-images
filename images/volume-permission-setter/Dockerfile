# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d
FROM busybox:1.37.0@sha256:e226d6308690dbe282443c8c7e57365c96b5228f0fe7f40731b5d84d37a06839

WORKDIR /app

COPY entrypoint.sh /app/entrypoint.sh

RUN chmod 0755 /app/entrypoint.sh

ENV TARGET="" \
    WANT_UID="" \
    WANT_GID="" \
    WANT_MODE=""

ENTRYPOINT ["/app/entrypoint.sh"]
