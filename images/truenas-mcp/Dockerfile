# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d
# python:3.13-slim (glibc), not -alpine: truenas-mcp's release binary is
# dynamically linked against glibc (confirmed live via ldd against a
# running pod - see apps/hermes/truenas-mcp.yaml in the main homelab repo;
# it fails to relocate __vfprintf_chk/__fprintf_chk without it) and there
# is no musl-compatible build upstream. There is also no linux-arm64
# release asset (only darwin-arm64, linux-amd64, windows-amd64 - confirmed
# against the GitHub releases API for this tag), so this image is
# amd64-only; see images/truenas-mcp/platforms.
FROM python:3.13.15-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Fetched straight from the GitHub release and sha256-verified against the
# value published in that release's checksums.txt, since upstream ships no
# PyPI package or Docker image of its own. Guarded on TARGETARCH even
# though the platforms file restricts this image to amd64-only builds -
# fail loudly rather than silently fetch the wrong (nonexistent) asset if
# that restriction is ever dropped by mistake.
#
# renovate.json's custom regex manager bumps TRUENAS_MCP_VERSION on its
# own, but it cannot know the new checksum - a version bump PR needs the
# CHECKSUM value below updated by hand from that release's own
# checksums.txt, or the build fails closed at the sha256sum -c step
# (intentional: a stale checksum should break the build, not silently
# pass).
ARG TARGETARCH
RUN set -eu; \
    if [ "$TARGETARCH" != "amd64" ]; then \
      echo "truenas-mcp has no linux-${TARGETARCH} release asset upstream - amd64 only" >&2; \
      exit 1; \
    fi; \
    TRUENAS_MCP_VERSION=v0.0.6; \
    CHECKSUM=920a1042763f2ed31937c5cf3072fbd2defdd863dbfb3e1078aeed0275f5fa1d; \
    curl -fsSLo /tmp/truenas-mcp.tar.gz \
      "https://github.com/truenas/truenas-mcp/releases/download/${TRUENAS_MCP_VERSION}/truenas-mcp-linux-amd64.tar.gz"; \
    echo "${CHECKSUM}  /tmp/truenas-mcp.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/truenas-mcp.tar.gz -C /tmp; \
    install -m 0755 /tmp/truenas-mcp-linux-amd64 /usr/local/bin/truenas-mcp; \
    rm -f /tmp/truenas-mcp.tar.gz /tmp/truenas-mcp-linux-amd64

# mcp-proxy's own dependency declaration is "mcp>=1.17.0" with no upper
# bound, so an unpinned install pulls whatever is newest on the day this
# builds - mcp 2.x's server.lowlevel module removed request_ctx, which
# mcp-proxy 0.12.0 still imports (confirmed live: a bare
# `pip install mcp-proxy==0.12.0` grabs mcp 2.1.1 and crashes with
# ImportError before ever binding the port - see truenas-mcp.yaml).
# mcp-proxy 0.12.0 shipped before mcp 2.0.0 existed, so pinning to the 1.x
# line it was actually built against is what upstream intended even though
# their own constraint doesn't enforce it.
RUN pip install --no-cache-dir "mcp-proxy==0.12.0" "mcp<2.0.0"

# 20208: same UID/GID the hermes truenas-mcp deployment already forces via
# securityContext.runAsUser (see docs/uid-gid.org in the main homelab
# repo) - keeping it stable means no securityContext change needed for a
# manifest that switches from the startup curl+pip-install command to this
# image.
RUN groupadd -g 20208 truenas-mcp \
    && useradd -u 20208 -g 20208 -M -s /usr/sbin/nologin truenas-mcp

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

USER 20208

# Default to 0.0.0.0:8080, matching the Service the hermes manifest
# already points at, but stay overridable via env for whatever consumes
# this image next.
ENV MCP_PROXY_HOST=0.0.0.0 \
    MCP_PROXY_PORT=8080

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
