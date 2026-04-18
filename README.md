# SentinelX Core MCP

MCP server that proxies tool calls to a running SentinelX Core instance.

This repository is the portable MCP/OAuth bridge layer. It is intended to remain separate from `sentinelx-core`, which provides the underlying HTTP agent.

## What it does

- exposes MCP tools backed by SentinelX Core
- validates bearer tokens through OIDC/JWKS
- forwards tool calls to the upstream SentinelX Core API
- adds a separate product boundary for MCP integrations

## Dependency on SentinelX Core

This project requires a running SentinelX Core instance.

Typical local development pairing:

- SentinelX Core: `http://127.0.0.1:8092`
- SentinelX Core MCP: `http://127.0.0.1:8099`

Typical installed pairing:

- SentinelX Core: `http://127.0.0.1:8091`
- SentinelX Core MCP: `http://127.0.0.1:8098`

## Quick start

```bash
git clone git@github.com:pensados/sentinelx-core-mcp.git
cd sentinelx-core-mcp
sudo bash install.sh
```

Then edit:

```bash
sudo nano /etc/sentinelx-core-mcp/sentinelx-core-mcp.env
```

Set at least:

```env
MCP_PORT=8098
SENTINELX_URL=http://127.0.0.1:8091
SENTINELX_TOKEN=changeme
OIDC_ISSUER=https://auth.example.com/realms/sentinelx
OIDC_JWKS_URI=https://auth.example.com/realms/sentinelx/protocol/openid-connect/certs
OIDC_EXPECTED_AUDIENCE=
RESOURCE_URL=https://sentinelx.example.com
AUTH_DEBUG=false
LOG_DIR=/var/log/sentinelx-mcp
LOG_FILE=/var/log/sentinelx-mcp/sentinelx-core-mcp.log
```

Restart and inspect:

```bash
sudo systemctl restart sentinelx-core-mcp
sudo systemctl status sentinelx-core-mcp
sudo journalctl -u sentinelx-core-mcp -n 100 --no-pager
```

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh
```

Local development defaults:

- MCP port: `8099`
- upstream SentinelX Core URL: `http://127.0.0.1:8092`

## Installed paths

- code: `/opt/sentinelx-core-mcp`
- env file: `/etc/sentinelx-core-mcp/sentinelx-core-mcp.env`
- logs: `/var/log/sentinelx-mcp`
- service: `sentinelx-core-mcp.service`

## Configuration

Installed example environment file:

```env
MCP_PORT=8098
MCP_TOKEN=
SENTINELX_URL=http://127.0.0.1:8091
SENTINELX_TOKEN=changeme
OIDC_ISSUER=https://auth.example.com/realms/sentinelx
OIDC_JWKS_URI=https://auth.example.com/realms/sentinelx/protocol/openid-connect/certs
OIDC_EXPECTED_AUDIENCE=
RESOURCE_URL=https://sentinelx.example.com
AUTH_DEBUG=false
LOG_DIR=/var/log/sentinelx-mcp
LOG_FILE=/var/log/sentinelx-mcp/sentinelx-core-mcp.log
```

## Notes on auth

- protected tools require bearer tokens
- tokens are validated against the configured JWKS endpoint
- the upstream SentinelX Core still enforces its own internal bearer token and allowlists

## Troubleshooting

### MCP starts but tools fail

Check:

- `SENTINELX_URL` points to a running SentinelX Core
- `SENTINELX_TOKEN` matches the upstream core
- OIDC issuer and JWKS URI are correct
- the token has the scopes expected by the MCP layer

### Check the upstream core directly

```bash
curl -s -H "Authorization: Bearer changeme" http://127.0.0.1:8091/state | jq
```

## Security notes

- keep the MCP service bound appropriately for your deployment
- prefer local-only binding with an explicit fronting layer when possible
- use a dedicated OIDC client and minimal scopes
- rotate credentials and review logs regularly
