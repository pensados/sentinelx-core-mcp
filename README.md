# SentinelX Core MCP

**MCP/OAuth bridge for SentinelX Core. Exposes your server agent as MCP tools with OIDC token validation.**

SentinelX Core MCP sits between MCP clients (Claude, ChatGPT, Cursor, or any MCP-compatible agent) and a running [SentinelX Core](https://github.com/pensados/sentinelx-core) instance. It validates incoming OAuth Bearer tokens against a JWKS endpoint, then forwards tool calls to the upstream agent.

---

## Architecture

```
Claude / ChatGPT / Cursor / any MCP client
        │
        │  MCP  +  OAuth Bearer token
        ▼
  sentinelx-core-mcp   (public, port 8098)
        │  validates token via OIDC/JWKS
        │  HTTP  +  internal Bearer token
        ▼
  sentinelx-core        (local only, port 8091)
        │
        └─ command allowlist, structured editing, uploads, services
```

**Two separate auth layers:**

| Layer | What validates it | Token type |
|-------|------------------|-----------|
| External (MCP) | `sentinelx-core-mcp` via OIDC/JWKS | OAuth access token (from your identity provider) |
| Internal (agent) | `sentinelx-core` | Static bearer token (`SENTINELX_TOKEN`) |

---

## Exposed MCP tools

| Tool | What it does | Required scope |
|------|-------------|----------------|
| `ping` | Health check | public |
| `sentinel_state` | Agent runtime state | `sentinelx:state` |
| `sentinel_exec` | Execute an allowed command | `sentinelx:exec` |
| `sentinel_service` | Service action (start/stop/restart/reload/status) | `sentinelx:service` |
| `sentinel_restart` | Restart a registered service | `sentinelx:restart` |
| `sentinel_edit` | Structured file edit (no shell quoting) | `sentinelx:edit` |
| `sentinel_edit_upload_init` | Initialize large edit upload | `sentinelx:edit` |
| `sentinel_edit_upload_file` | Upload role file for editing | `sentinelx:edit` |
| `sentinel_edit_upload_complete` | Finalize large edit | `sentinelx:edit` |
| `sentinel_upload_file` | Upload a file (URL or base64) | `sentinelx:upload` |
| `sentinel_upload_init` | Initialize chunked upload | `sentinelx:upload` |
| `sentinel_upload_chunk` | Upload one chunk | `sentinelx:upload` |
| `sentinel_upload_complete` | Finalize chunked upload | `sentinelx:upload` |
| `sentinel_script_run` | Run a temporary bash/python3 script | `sentinelx:script` |
| `sentinel_capabilities` | Allowed commands, services, locations, playbooks | `sentinelx:capabilities` |
| `sentinel_help` | Embedded help from the agent | `sentinelx:capabilities` |

---

## Requirements

- A running [SentinelX Core](https://github.com/pensados/sentinelx-core) instance
- An OIDC-compatible identity provider (Keycloak, Auth0, Authentik, Zitadel, or any provider with a JWKS endpoint)
- Python 3.11+

---

## Quick start

### Install on a server

```bash
git clone https://github.com/pensados/sentinelx-core-mcp.git
cd sentinelx-core-mcp
sudo bash install.sh
```

Then configure:

```bash
sudo nano /etc/sentinelx-core-mcp/sentinelx-core-mcp.env
```

Minimum required:

```env
MCP_PORT=8098
SENTINELX_URL=http://127.0.0.1:8091
SENTINELX_TOKEN=your_internal_agent_token

OIDC_ISSUER=https://auth.example.com/realms/sentinelx
OIDC_JWKS_URI=https://auth.example.com/realms/sentinelx/protocol/openid-connect/certs
OIDC_EXPECTED_AUDIENCE=

RESOURCE_URL=https://sentinelx.example.com
AUTH_DEBUG=false
```

Restart and verify:

```bash
sudo systemctl restart sentinelx-core-mcp
sudo systemctl status sentinelx-core-mcp
sudo journalctl -u sentinelx-core-mcp -n 50 --no-pager
```

### Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh
```

Local defaults:

- MCP port: **8099**
- Upstream SentinelX Core: `http://127.0.0.1:8092`

---

## Installed paths

| Path | Content |
|------|---------|
| `/opt/sentinelx-core-mcp` | Application code |
| `/etc/sentinelx-core-mcp/sentinelx-core-mcp.env` | Environment configuration |
| `/var/log/sentinelx-mcp` | Logs |
| `sentinelx-core-mcp.service` | systemd unit |

---

## Connecting a reverse proxy

The MCP endpoint at `/mcp` should be exposed via HTTPS. Example Nginx config:

```nginx
server {
    listen 443 ssl http2;
    server_name sentinelx.example.com;

    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    location = /mcp {
        proxy_pass http://127.0.0.1:8098/mcp;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        add_header Cache-Control "no-cache";
    }
}
```

---

## Connecting to Claude

Add the MCP server in Claude's settings:

```
https://sentinelx.example.com/mcp
```

Claude will prompt for OAuth login on first use. After authorization it will have access to all tools your token's scopes allow.

---

## Connecting to ChatGPT

Register the MCP server URL as a GPT Action or in your ChatGPT connector configuration. The OAuth flow works with any OIDC provider that supports the Authorization Code flow.

---

## MCP smoke test (curl)

The MCP endpoint uses JSON-RPC over HTTP. A minimal session:

### 1. Initialize

```bash
SESSION=$(curl -si -X POST https://sentinelx.example.com/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0","id":"1","method":"initialize",
    "params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0.1"}}
  }' | grep -i mcp-session-id | awk '{print $2}' | tr -d '\r')
```

### 2. Notify initialized

```bash
curl -s -X POST https://sentinelx.example.com/mcp \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
```

### 3. Call ping (public)

```bash
curl -s -X POST https://sentinelx.example.com/mcp \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc":"2.0","id":"2","method":"tools/call","params":{"name":"ping","arguments":{}}}' \
  | sed -n 's/^data: //p' | jq
```

### 4. Call a protected tool

```bash
curl -s -X POST https://sentinelx.example.com/mcp \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: $SESSION" \
  -H "Authorization: Bearer YOUR_OAUTH_ACCESS_TOKEN" \
  -d '{"jsonrpc":"2.0","id":"3","method":"tools/call","params":{"name":"sentinel_exec","arguments":{"cmd":"uptime"}}}' \
  | sed -n 's/^data: //p' | jq
```

---

## Identity provider setup

Any OIDC-compatible provider works: Keycloak, Auth0, Authentik, Zitadel, or your own. You need:

1. A **client** configured for Authorization Code flow (interactive) or Client Credentials (machine-to-machine)
2. **Custom scopes** matching the tools you want to expose (`sentinelx:exec`, `sentinelx:edit`, etc.)
3. The **JWKS URI** of your provider
4. For Claude and ChatGPT: the correct **redirect URIs** registered in the client

Set these in the env file:

```env
OIDC_ISSUER=https://your-provider.example.com/realms/your-realm
OIDC_JWKS_URI=https://your-provider.example.com/realms/your-realm/protocol/openid-connect/certs
OIDC_EXPECTED_AUDIENCE=   # set to your client ID, or leave empty to skip audience validation
```

### About `OIDC_EXPECTED_AUDIENCE`

- Set to your **client ID** if your provider includes it in the `aud` claim (common with confidential clients)
- Leave **empty** if unsure — the server skips audience validation
- If tokens are rejected, decode the token (`echo $TOKEN | cut -d. -f2 | base64 -d | jq`) and check the `aud` claim

### Connecting Claude

Add the MCP server in Claude's settings:

```
https://sentinelx.example.com/mcp
```

Claude will redirect to your identity provider on first use. Make sure:

- The redirect URI `https://claude.ai/api/mcp/auth_callback` is registered in your OIDC client
- Your server exposes `/.well-known/oauth-protected-resource` with the correct `authorization_servers` value

### Connecting ChatGPT

Register the MCP URL as a GPT Action. Add `https://chatgpt.com/aip/g-*/oauth/callback` to your client's redirect URIs.

For a complete end-to-end walkthrough with Keycloak — including token acquisition, Claude setup, smoke tests and troubleshooting — see [`docs/keycloak-example.md`](docs/keycloak-example.md).

---

## Troubleshooting

**Tools fail with `Missing Authorization header`**
The MCP client is not sending the OAuth token. Verify the authorization flow completed successfully.

**`Invalid access token`**
Check `OIDC_ISSUER` and `OIDC_JWKS_URI` match your identity provider exactly. Enable `AUTH_DEBUG=true` temporarily to see token validation details in the logs.

**`Missing required scope`**
The token does not include the scope required by that tool. Add the scope to your OIDC client configuration and re-authorize.

**`ping` works but all other tools fail**
Usually an auth issue. `ping` is public; every other tool requires a valid token with the right scope.

**MCP starts but cannot reach SentinelX Core**
Check `SENTINELX_URL` points to a running core instance and `SENTINELX_TOKEN` matches the core's `SENTINEL_TOKEN`.

---

## Security notes

- Keep the MCP service behind HTTPS and a reverse proxy
- Use a dedicated OIDC client with only the scopes you need
- Rotate `SENTINELX_TOKEN` and OIDC client credentials periodically
- Review the exec audit log (`/var/log/sentinelx/exec.log`) regularly
- `AUTH_DEBUG=true` logs token claims — disable in production

---

## Related

- **[sentinelx-core](https://github.com/pensados/sentinelx-core)** — The underlying HTTP agent: command execution, structured editing, uploads, and service management.

---

## License

MIT
