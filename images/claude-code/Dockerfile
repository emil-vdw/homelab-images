# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d
# bookworm-slim, not alpine: Claude Code has no headless mode (it always
# renders an Ink TUI, hence the tmux wrapper below) and kubectl/flux are
# fetched as glibc-linked binaries - musl would need a compat shim.
FROM node:24.20.0-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e

RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      tmux \
      ripgrep \
      curl \
      jq \
      less \
      ca-certificates \
      openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Exact version, not @latest: `claude remote-control` run unattended in a
# pod has no one watching for a breaking release to explain a sudden
# failure. Matches (or exceeds) the version already confirmed working on
# the operator's own machine.
RUN npm install -g @anthropic-ai/claude-code@2.1.252

# kubectl, matched to the cluster's own k3s v1.35.7 rather than whatever a
# distro package manager happens to carry. Downloaded per-arch and
# sha256-verified against the checksum Kubernetes itself publishes next to
# the binary, since this build is multi-platform (see
# .github/workflows/publish-ghcr.yaml).
#
# renovate.json's custom regex manager bumps KUBECTL_VERSION on its own,
# but it cannot know the new checksums - a version bump PR needs both
# CHECKSUM values below updated by hand from the matching
# https://dl.k8s.io/release/<version>/bin/linux/<arch>/kubectl.sha256
# files, or the build fails closed at the sha256sum -c step (intentional:
# a stale checksum should break the build, not silently pass).
ARG TARGETARCH
RUN set -eu; \
    KUBECTL_VERSION=v1.35.8; \
    curl -fsSLo /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl"; \
    case "$TARGETARCH" in \
      amd64) CHECKSUM=874d5e72dbb819f43cff16bcd1e4f8bac5b7f2361fe1e55049b0a6c676fb0cbf ;; \
      arm64) CHECKSUM=cc749967b62f4422260bc9c0aa7a7c55f45175ae38cb8d95767b5d2b7e04c1fd ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    echo "${CHECKSUM}  /usr/local/bin/kubectl" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/kubectl

# flux CLI, same reasoning and same source of truth as kubectl above: the
# release tarball and flux2's own published checksums.txt, not an
# unverified third-party repo. Same renovate.json caveat as kubectl above:
# a FLUX_VERSION bump needs the two CHECKSUM values updated by hand from
# that release's own checksums.txt.
RUN set -eu; \
    FLUX_VERSION=v2.9.5; \
    curl -fsSLo /tmp/flux.tar.gz \
      "https://github.com/fluxcd/flux2/releases/download/${FLUX_VERSION}/flux_${FLUX_VERSION#v}_linux_${TARGETARCH}.tar.gz"; \
    case "$TARGETARCH" in \
      amd64) CHECKSUM=b853df82adfd7736f580692f9f734473d571606307139f8fd20c2a80dd1ff473 ;; \
      arm64) CHECKSUM=f3e159af616ec0b9bd0a405c2185cf09d06b74652c1de3c7f377e8166826651a ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    echo "${CHECKSUM}  /tmp/flux.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/flux.tar.gz -C /usr/local/bin flux; \
    chmod 0755 /usr/local/bin/flux; \
    rm -f /tmp/flux.tar.gz

# Claude Code otherwise checks npm for a newer release and rewrites its own
# install on every start - undesirable here since the pinned version above
# is the intended source of truth, and nothing guarantees the install path
# stays writable by the non-root user set below.
ENV DISABLE_AUTOUPDATER=1

# 20209: next free UID/GID in the homelab's 202xx allocation scheme, after
# truenas-mcp's 20208 (see docs/uid-gid.org in the main homelab repo). Home
# is /opt/data because that's where the k8s manifest mounts a PVC for
# Claude Code's own state/config - NOT chowned here (-M skips creating and
# populating it) because an initContainer in that manifest chowns the PVC
# at pod start, the same pattern the hermes deployment's data volume uses.
RUN groupadd -g 20209 claude \
    && useradd -u 20209 -g 20209 -d /opt/data -M -s /usr/sbin/nologin claude

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

USER 20209

# No CMD: the k8s manifest supplies the real `claude remote-control ...`
# invocation via `args`, so the image hardcodes no CLI flags of its own.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
