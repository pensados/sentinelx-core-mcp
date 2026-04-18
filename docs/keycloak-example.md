# Keycloak example for SentinelX Core MCP

This document shows one practical way to configure Keycloak as the OIDC provider for `sentinelx-core-mcp`.

Keycloak is a **recommended example**, not a hard requirement. Any compatible OIDC provider should work if it exposes a valid issuer and JWKS endpoint.

## Goal

The goal is to protect the MCP layer with real bearer access tokens while keeping the upstream `sentinelx-core` protected separately by its own internal token.

That gives you two boundaries:

- MCP client -> `sentinelx-core-mcp` using OAuth/OIDC
- `sentinelx-core-mcp` -> `sentinelx-core` using `SENTINELX_TOKEN`

## High-level model

- Keycloak realm: `sentinelx`
- MCP OIDC issuer example: `https://auth.example.com/realms/sentinelx`
- MCP JWKS example: `https://auth.example.com/realms/sentinelx/protocol/openid-connect/certs`
- MCP resource URL example: `https://sentinelx.example.com`

## 1. Create a realm

In Keycloak, create a realm such as:

```text
sentinelx
```

## 2. Create a client for MCP access

Create a client dedicated to the MCP layer.

Suggested ideas:

- client id: `sentinelx-mcp`
- protocol: `openid-connect`
- keep the client purpose narrow

Exactly whether the client is public or confidential depends on your use case and token flow.
For machine-to-machine or controlled backend integrations, confidential clients are often the cleaner option.

## 3. Define scopes used by the MCP layer

The MCP server currently expects scopes such as these:

```text
sentinelx:state
sentinelx:exec
sentinelx:restart
sentinelx:service
sentinelx:upload
sentinelx:edit
sentinelx:script
sentinelx:capabilities
```

You should only grant the scopes you actually want the client to use.

## 4. Configure the MCP environment

Edit:

```text
/etc/sentinelx-core-mcp/sentinelx-core-mcp.env
```

Example:

```env
MCP_PORT=8098
MCP_TOKEN=
SENTINELX_URL=http://127.0.0.1:8091
SENTINELX_TOKEN=changeme
OIDC_ISSUER=https://auth.example.com/realms/sentinelx
OIDC_JWKS_URI=https://auth.example.com/realms/sentinelx/protocol/openid-connect/certs
OIDC_EXPECTED_AUDIENCE=sentinelx-mcp
RESOURCE_URL=https://sentinelx.example.com
AUTH_DEBUG=false
LOG_DIR=/var/log/sentinelx-mcp
LOG_FILE=/var/log/sentinelx-mcp/sentinelx-core-mcp.log
```

Restart the service after changes:

```bash
sudo systemctl restart sentinelx-core-mcp
sudo systemctl status sentinelx-core-mcp
```

## 5. Obtain a token

The exact way you obtain a token depends on your Keycloak client type and flow.

For example, with a confidential client and client credentials flow, you typically request a token from Keycloak's token endpoint and then use that bearer token against the MCP endpoint.

## 6. MCP handshake with curl

Initialize a session:

```bash
curl -i -X POST http://127.0.0.1:8098/mcp \
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

Take the returned `mcp-session-id`, then notify initialized:

```bash
curl -i -X POST http://127.0.0.1:8098/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: TU_SESSION_ID" \
  -d '{
    "jsonrpc":"2.0",
    "method":"notifications/initialized"
  }'
```

## 7. Call a protected tool with a real access token

Once you have a real access token from Keycloak, you can call a protected tool such as `sentinel_state`.

```bash
curl -s -X POST http://127.0.0.1:8098/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer TU_ACCESS_TOKEN" \
  -H "mcp-session-id: TU_SESSION_ID" \
  -d '{
    "jsonrpc":"2.0",
    "id":"call-state-1",
    "method":"tools/call",
    "params":{
      "name":"sentinel_state",
      "arguments":{}
    }
  }' | sed -n 's/^data: //p' | jq
```

If the token is valid and includes the required scope, the call should succeed.
If the scope is missing, the MCP layer should reject the request.

## 8. Troubleshooting

### Token rejected

Check:

- `OIDC_ISSUER` is correct
- `OIDC_JWKS_URI` is correct
- `OIDC_EXPECTED_AUDIENCE` matches what Keycloak emits
- the token includes the expected scope
- the token is not expired

### MCP works for `ping` but protected tools fail

That usually means:

- MCP transport is fine
- session handling is fine
- the problem is auth or scope enforcement

### Protected MCP call works but the upstream action fails

That usually means the problem is no longer OIDC. Instead check:

- `SENTINELX_URL`
- `SENTINELX_TOKEN`
- the upstream `sentinelx-core` status
- allowlists or permissions enforced by the upstream core

## Final recommendation

Use Keycloak as a known-good example if you already run it or want a tested reference.
If you prefer another OIDC provider, keep the same mental model:

- valid issuer
- valid JWKS endpoint
- minimal scopes
- narrow client purpose
- separate upstream SentinelX Core token
