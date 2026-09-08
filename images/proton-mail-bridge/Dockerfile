# syntax=docker/dockerfile:1
FROM debian:trixie-slim AS package
ARG TARGETARCH
ARG BRIDGE_VERSION=3.26.0
ARG BRIDGE_SHA256=c076872522ce2f0facd0e64764d7d588b3a1ed213ff3acd25386b51e8a1f02e8
ARG KEY_SHA256=d5279ed2dc52ffad8875569df0bcca45bd7448bf737fe79722762337e204c221
RUN test "$TARGETARCH" = amd64
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl gnupg debsig-verify \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /package
RUN curl -fsSLO "https://proton.me/download/bridge/protonmail-bridge_${BRIDGE_VERSION}-1_amd64.deb" \
    && echo "$BRIDGE_SHA256  protonmail-bridge_${BRIDGE_VERSION}-1_amd64.deb" | sha256sum -c - \
    && curl -fsSLo bridge_pubkey.gpg https://proton.me/download/bridge/bridge_pubkey.gpg \
    && echo "$KEY_SHA256  bridge_pubkey.gpg" | sha256sum -c - \
    && mkdir -p /usr/share/debsig/keyrings/E2C75D68E6234B07 /etc/debsig/policies/E2C75D68E6234B07 \
    && gpg --batch --dearmor --output /usr/share/debsig/keyrings/E2C75D68E6234B07/debsig.gpg bridge_pubkey.gpg
COPY bridge.pol /etc/debsig/policies/E2C75D68E6234B07/bridge.pol
RUN debsig-verify "protonmail-bridge_${BRIDGE_VERSION}-1_amd64.deb" \
    && dpkg-deb -x "protonmail-bridge_${BRIDGE_VERSION}-1_amd64.deb" extracted

FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libsecret-1-0 libglib2.0-0t64 libfido2-1 \
    pass gnupg python3 haproxy tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 20214 bridge && useradd --uid 20214 --gid 20214 --home-dir /data bridge
# Official headless executable, without the GUI or self-updating launcher.
COPY --from=package /package/extracted/usr/lib/protonmail/bridge/bridge /usr/local/lib/proton-bridge/bridge
COPY --from=package /package/extracted/usr/share/doc/protonmail-bridge/ /usr/share/doc/protonmail-bridge/
COPY runtime.py /usr/local/bin/mail-runtime
RUN chmod 0755 /usr/local/bin/mail-runtime \
    && /usr/local/lib/proton-bridge/bridge --version
ENV HOME=/data XDG_CONFIG_HOME=/data/config XDG_DATA_HOME=/data/share XDG_CACHE_HOME=/data/cache \
    GNUPGHOME=/run/bridge/gnupg PASSWORD_STORE_DIR=/data/password-store
USER 20214:20214
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/mail-runtime"]
CMD ["bridge"]
