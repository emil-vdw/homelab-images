# syntax=docker/dockerfile:1.20@sha256:26147acbda4f14c5add9946e2fd2ed543fc402884fd75146bd342a7f6271dc1d
FROM node:24.20.0-alpine@sha256:e67514e5d0f6c46656005e1b693b2ec9d52e80b641307de684d4a015ba7a4eaf

# Bakes in @cyanheads/seerr-mcp-server, which the hermes deployment
# previously installed fresh via `npm install --prefix` on every pod
# (re)start (see apps/hermes/seerr-mcp.yaml in the main homelab repo) -
# that re-resolves and downloads the dependency tree from the registry on
# every restart, a network dependency none of this namespace's other MCP
# servers have.
# Pure JS with no native bindings (confirmed via its package.json), so a
# plain global install on the stock node:alpine image is all it needs -
# no separate build stage. engines.node requires >=24, hence node:24
# rather than an older or slimmer base.
RUN npm install -g @cyanheads/seerr-mcp-server@0.1.3 \
    && npm cache clean --force

# Deliberately NOT applying the ConfirmSchema `.default(true)` patch that
# apps/hermes/seerr-mcp.yaml carries (see that file's long comment). That
# patch works around a bug in Hermes' *own* MCP elicitation client - it
# always answers form-mode elicitations with empty content, which this
# server's seerr_request_media confirmation step then rejects - not a bug
# in seerr-mcp-server itself. Whether Claude Code's MCP client populates
# elicitation content correctly is untested here. Baking the patch into
# this image would silently apply a Hermes-specific workaround to every
# client that pulls it. If the same confirmation failure shows up against
# Claude Code, investigate and patch deliberately - don't re-add this
# blindly on the assumption it's the same root cause.

# 20206: same UID/GID the hermes seerr-mcp deployment already assigns this
# server (see docs/uid-gid.org in the main homelab repo) - keeping it
# stable means no securityContext.runAsUser change needed for a manifest
# that switches from the startup npm-install command to this image.
# Two statements, not one `A || B && C || D` chain: that parses as
# ((A||B) && C) || D, which happens to produce the right result here but
# only by accident of precedence.
#
# The passwd entry is the point of this, not the group. node:alpine has no
# entry for 20206, and the hermes deployment had to set HOME explicitly so
# npm's cache-path resolution never called os.userInfo() (see
# apps/hermes/seerr-mcp.yaml). A real entry removes that constraint.
# Confirm on the first build that busybox honours -u alongside -S; if it
# assigns a system-range UID instead, the entry lands on the wrong number
# and this is back to where it started. The container still RUNS as 20206
# either way, because the manifest pins runAsUser.
RUN addgroup -g 20206 -S seerr-mcp
RUN adduser -u 20206 -S -H -s /sbin/nologin -G seerr-mcp seerr-mcp

USER 20206

# Runtime configuration (MCP_TRANSPORT_TYPE, MCP_HTTP_HOST, MCP_HTTP_PORT,
# MCP_SESSION_MODE, LOGS_DIR, SEERR_BASE_URL, SEERR_API_KEY, HOME) is
# deliberately left unset here - it stays in the k8s manifest's env:
# block, same as every other MCP server in this namespace, since it's
# deployment-specific rather than image-specific.
ENTRYPOINT ["seerr-mcp-server"]
