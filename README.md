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

## Manual MCP smoke test

The MCP endpoint is not a plain REST endpoint. A minimal manual test with `curl` requires:

1. initialize a session
2. send `notifications/initialized`
3. call `tools/list` or a public tool such as `ping`

### 1. Initialize a session

```bash
curl -i -X POST http://127.0.0.1:8099/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "id":"init-1",
    "method":"initialize",
    "params":{
      "protocolVersion":"2025-03-26",
      "capabilities":{},
      "clientInfo":{
        "name":"curl",
        "version":"0.1"
      }
    }
  }'
```

This should return `200 OK` and a response header like:

```text
mcp-session-id: ...
```

### 2. Notify initialized

Replace `TU_SESSION_ID` with the real value returned by the server:

```bash
curl -i -X POST http://127.0.0.1:8099/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: TU_SESSION_ID" \
  -d '{
    "jsonrpc":"2.0",
    "method":"notifications/initialized"
  }'
```

This should return `202 Accepted`.

### 3. List tools

Because the response is delivered as SSE (`event: message` + `data: ...`), strip the `data:` prefix before piping to `jq`:

```bash
curl -s -X POST http://127.0.0.1:8099/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: TU_SESSION_ID" \
  -d '{
    "jsonrpc":"2.0",
    "id":"tools-1",
    "method":"tools/list",
    "params":{}
  }' | sed -n 's/^data: //p' | jq
```

### 4. Call the public `ping` tool

```bash
curl -s -X POST http://127.0.0.1:8099/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: TU_SESSION_ID" \
  -d '{
    "jsonrpc":"2.0",
    "id":"call-1",
    "method":"tools/call",
    "params":{
      "name":"ping",
      "arguments":{}
    }
  }' | sed -n 's/^data: //p' | jq
```

### What this validates

These manual tests validate that:

- the MCP server is listening
- MCP session initialization works
- the protocol framing is correct
- the server exposes tools correctly
- at least one public tool call works end-to-end

### Important auth note

Protected MCP tools still require a real OAuth/OIDC access token accepted by the MCP layer.

The internal SentinelX Core token is **not** the same as the external MCP access token.


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
- Keycloak is a recommended and tested option, but it is not the only valid choice
- any compatible OIDC provider should work if it exposes a valid issuer and JWKS endpoint

## Authentication model

SentinelX Core MCP is designed for OIDC/OAuth bearer tokens on the MCP layer.

That means there are **two different auth layers** in a typical deployment:

1. **External MCP auth**
   - validated by `sentinelx-core-mcp`
   - based on `OIDC_ISSUER`, `OIDC_JWKS_URI` and optional `OIDC_EXPECTED_AUDIENCE`
   - intended for MCP clients such as ChatGPT or another MCP consumer

2. **Internal SentinelX Core auth**
   - validated by `sentinelx-core`
   - based on `SENTINELX_TOKEN`
   - used only by the MCP bridge when it forwards requests upstream

### Recommendation

For production-like deployments, use a real OIDC provider.

Keycloak is a good reference implementation because it is well understood and was the original reference used for this project.
However, the repository should stay portable, so the documentation treats Keycloak as an example, not as a hard dependency.

### Where to start

- for a generic overview, use this README
- for a concrete Keycloak walkthrough, see [`docs/keycloak-example.md`](docs/keycloak-example.md)

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
