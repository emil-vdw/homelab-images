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
# distro package manager happens to carry.
#
# The checksum is FETCHED at build time from the .sha256 Kubernetes
# publishes next to the binary, not pinned as a constant here. That is a
# deliberate trade: a pinned hash is reviewed by a human at pin time and
# would catch an already-published artifact changing underneath us, but
# Renovate cannot compute one (its regex manager only rewrites the version
# string, and no datasource exposes an artifact sha256), so every version
# bump would arrive as a red PR needing hand-edited hex. Fetching keeps the
# verification meaningful against truncation and mirror corruption while
# letting a bump be a one-line change. Note this trusts dl.k8s.io for both
# the binary and its hash, which we already do for the binary itself.
#
# The .sha256 file is bare hex with no filename and no trailing newline,
# unlike the sha256sum-format checksums.txt files the two other downloads
# in this repo use.
ARG TARGETARCH
RUN set -eu; \
    KUBECTL_VERSION=v1.35.8; \
    base="https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}"; \
    curl -fsSLo /tmp/kubectl "${base}/kubectl"; \
    checksum="$(curl -fsSL "${base}/kubectl.sha256")"; \
    echo "${checksum}  /tmp/kubectl" | sha256sum -c -; \
    install -m 0755 /tmp/kubectl /usr/local/bin/kubectl; \
    rm -f /tmp/kubectl

# flux CLI. Same fetch-at-build-time reasoning as kubectl above.
#
# The checksum asset is named flux_<version>_checksums.txt, NOT
# checksums.txt - verified against the v2.9.5 release's asset list. It is
# standard sha256sum format (hash, two spaces, filename), so the matching
# line is fed to `sha256sum -c` directly. Capturing the grep into a
# variable rather than piping it means a missing line fails the build here
# instead of being masked by the pipeline's exit status.
RUN set -eu; \
    FLUX_VERSION=v2.9.5; \
    v="${FLUX_VERSION#v}"; \
    tarball="flux_${v}_linux_${TARGETARCH}.tar.gz"; \
    sums="flux_${v}_checksums.txt"; \
    base="https://github.com/fluxcd/flux2/releases/download/${FLUX_VERSION}"; \
    cd /tmp; \
    curl -fsSLO "${base}/${tarball}"; \
    curl -fsSLO "${base}/${sums}"; \
    line="$(grep "  ${tarball}" "${sums}")"; \
    echo "${line}" | sha256sum -c -; \
    tar -xzf "${tarball}" -C /usr/local/bin flux; \
    chmod 0755 /usr/local/bin/flux; \
    rm -f "${tarball}" "${sums}"

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
