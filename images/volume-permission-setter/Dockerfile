# syntax=docker/dockerfile:1.21@sha256:27f9262d43452075f3c410287a2c43f5ef1bf7ec2bb06e8c9eeb1b8d453087bc
FROM busybox:1.37.0@sha256:e226d6308690dbe282443c8c7e57365c96b5228f0fe7f40731b5d84d37a06839

WORKDIR /app

COPY entrypoint.sh /app/entrypoint.sh

RUN chmod 0755 /app/entrypoint.sh

ENV TARGET="" \
    WANT_UID="" \
    WANT_GID="" \
    WANT_MODE=""

ENTRYPOINT ["/app/entrypoint.sh"]
