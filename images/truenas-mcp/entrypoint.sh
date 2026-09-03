#!/bin/sh
set -eu

# --pass-environment forwards TRUENAS_URL / TRUENAS_API_KEY / TRUENAS_TLS_CA
# etc. straight through to the wrapped truenas-mcp binary - same variables
# apps/hermes/truenas-mcp.yaml (main homelab repo) already sets on the
# container, just against a baked-in binary instead of one fetched fresh
# on every pod start.
exec mcp-proxy \
  --host "${MCP_PROXY_HOST:-0.0.0.0}" \
  --port "${MCP_PROXY_PORT:-8080}" \
  --pass-environment \
  -- /usr/local/bin/truenas-mcp
