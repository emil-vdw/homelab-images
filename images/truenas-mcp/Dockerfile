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

# Fetched straight from the GitHub release, since upstream ships no PyPI
# package or Docker image of its own.
#
# The checksum is FETCHED from that release's checksums.txt at build time
# rather than pinned here, matching images/claude-code/Dockerfile - see the
# kubectl block there for the full reasoning and the trade being accepted.
# Standard sha256sum format (hash, two spaces, filename).
#
# Guarded on TARGETARCH even though the platforms file restricts this image
# to amd64-only builds - fail loudly rather than silently fetch the wrong
# (nonexistent) asset if that restriction is ever dropped by mistake.
#
# TARGETARCH is normally supplied by BuildKit and needs no fallback there,
# but a plain non-BuildKit `docker build` (as used for local verification
# in this repo, since this machine's docker has no buildx plugin) leaves
# it unset, and `set -u` then turns every reference into a hard-to-parse
# "TARGETARCH: parameter not set" error instead of the guard below's
# actual message. Defaulting to amd64 only fills that gap for a local,
# single-arch build - it does not skip or weaken the guard itself: an
# explicitly-passed non-amd64 value still overrides the default and still
# fails the check.
ARG TARGETARCH
RUN set -eu; \
    : "${TARGETARCH:=amd64}"; \
    if [ "$TARGETARCH" != "amd64" ]; then \
      echo "truenas-mcp has no linux-${TARGETARCH} release asset upstream - amd64 only" >&2; \
      exit 1; \
    fi; \
    TRUENAS_MCP_VERSION=v0.0.6; \
    tarball="truenas-mcp-linux-amd64.tar.gz"; \
    base="https://github.com/truenas/truenas-mcp/releases/download/${TRUENAS_MCP_VERSION}"; \
    cd /tmp; \
    curl -fsSLO "${base}/${tarball}"; \
    curl -fsSLO "${base}/checksums.txt"; \
    line="$(grep "  ${tarball}" checksums.txt)"; \
    echo "${line}" | sha256sum -c -; \
    tar -xzf "${tarball}"; \
    install -m 0755 truenas-mcp-linux-amd64 /usr/local/bin/truenas-mcp; \
    rm -f "${tarball}" checksums.txt truenas-mcp-linux-amd64

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

# Investigated the two Python (not Go) Trivy findings on this image -
# setuptools 70.3.0 (CVE-2025-47273) and msgpack 1.1.2
# (GHSA-6v7p-g79w-8964) - on the assumption they'd be reachable from the
# pip install above. They are not: `pip list` in this image has no
# top-level setuptools or msgpack package at all. Both versions come from
# pip's OWN vendored copies bundled inside pip itself (see
# /usr/local/lib/python3.13/site-packages/pip/_vendor/vendor.txt and its
# bom.cdx.json), and are present, byte-for-byte, in the untouched
# python:3.13.15-slim-bookworm base image before this Dockerfile does
# anything - confirmed by scanning the bare base image directly. pip
# 26.2.1, what this base image ships, is also the current latest pip
# release on PyPI, so there is no newer pip to pull in with patched
# vendored copies either.
# Tried `pip install --upgrade setuptools "msgpack>=1.2.1"` anyway (no
# conflict with mcp-proxy/mcp - checked with `pip check`): it installs
# real top-level packages alongside pip's vendored ones but cannot
# override pip's internal, separately-namespaced copies, so it changed
# nothing - rebuilt and rescanned, still 16/16 findings, 0 removed. Not
# worth carrying dead packages that fix nothing, so left out. Both
# findings are tracked as not-ours-to-fix in .trivyignore.

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
