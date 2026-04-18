# Keycloak example for SentinelX Core MCP

This document shows a practical, end-to-end walkthrough for configuring Keycloak as the OIDC provider for `sentinelx-core-mcp`.

Keycloak is a **recommended example**, not a hard requirement. Any OIDC-compatible provider with a valid issuer and JWKS endpoint works. The mental model applies regardless of provider.

---

## What you'll end up with

```
Claude / ChatGPT / curl
        │
        │  OAuth Bearer token  (issued by Keycloak)
        ▼
sentinelx-core-mcp          ← validates JWT via Keycloak JWKS
        │
        │  internal Bearer token  (SENTINELX_TOKEN)
        ▼
sentinelx-core              ← local agent, allowlisted commands
```

Two independent auth boundaries. The OIDC layer is for external clients. The internal token never leaves the server.

---

## Prerequisites

- A running Keycloak instance (self-hosted or cloud)
- `sentinelx-core` installed and running
- `sentinelx-core-mcp` installed
- A domain with HTTPS for the MCP endpoint (e.g. `https://sentinelx.example.com`)

---

## 1. Create a realm

In the Keycloak admin console, create a dedicated realm:

```
sentinelx
```

Using a dedicated realm keeps the MCP client isolated from other applications in your Keycloak instance.

Your OIDC issuer will be:

```
https://auth.example.com/realms/sentinelx
```

Your JWKS URI will be:

```
https://auth.example.com/realms/sentinelx/protocol/openid-connect/certs
```

---

## 2. Create a client

Create a new client in the `sentinelx` realm.

| Field | Value |
|-------|-------|
| Client ID | `sentinelx-mcp` |
| Protocol | `openid-connect` |
| Client authentication | On (confidential client) |
| Authorization | Off |

### 2.1 Access settings and redirect URIs

Configure redirect URIs to match the MCP clients you want to support.

![Keycloak client access settings example](images/keycloak-client-access-settings.png)

Common redirect URIs by client:

| Client | Redirect URI |
|--------|-------------|
| Claude (claude.ai) | `https://claude.ai/api/mcp/auth_callback` |
| ChatGPT | `https://chatgpt.com/aip/g-*/oauth/callback` |
| Cursor | `http://localhost:*/callback`, `cursor://anysphere.cursor-deeplink/mcp/auth_callback` |
| curl / testing | `http://localhost:9999/callback` |

**Keep the list minimal.** Only include URIs for clients you actually use. Wildcards should be avoided unless required.

Also set **Web origins** to `+` (mirrors valid redirect URIs) or list them explicitly if you prefer stricter control.

### 2.2 Client credentials

In the **Credentials** tab, note the client secret. You will need it to obtain tokens via the client credentials flow.

![Keycloak client token settings example](images/keycloak-token-settings.png)

Use **Client Id and Secret** as the authenticator for confidential client flows.

---

## 3. Define client scopes

The MCP layer enforces per-tool scopes. Create the following as **Client Scopes** in Keycloak (type: `None`, not default):

```
sentinelx:state
sentinelx:exec
sentinelx:restart
sentinelx:service
sentinelx:upload
sentinelx:edit
sentinelx:script
sentinelx:capabilities
```

![Keycloak client scopes example](images/keycloak-client-scopes.png)

Then assign the scopes you want to the `sentinelx-mcp` client under **Client Scopes → Add client scope** (set as Optional or Default depending on whether you want them included automatically).

**Recommendation:** add all as **Optional** so they are only included when explicitly requested. This gives you the most control over what each token can do.

### 3.1 Roles (optional)

If your setup uses role-based access in addition to scopes, you can define client roles and map them to scope claims via mappers.

![Keycloak client roles example](images/keycloak-client-roles.png)

For most SentinelX deployments, plain scopes are sufficient. Only add roles if you have a specific reason.

---

## 4. Configure `sentinelx-core-mcp`

Edit the environment file:

```bash
sudo nano /etc/sentinelx-core-mcp/sentinelx-core-mcp.env
```

```env
MCP_PORT=8098
SENTINELX_URL=http://127.0.0.1:8091
SENTINELX_TOKEN=your_internal_sentinelx_token

OIDC_ISSUER=https://auth.example.com/realms/sentinelx
OIDC_JWKS_URI=https://auth.example.com/realms/sentinelx/protocol/openid-connect/certs
OIDC_EXPECTED_AUDIENCE=sentinelx-mcp

RESOURCE_URL=https://sentinelx.example.com
AUTH_DEBUG=false
LOG_DIR=/var/log/sentinelx-mcp
LOG_FILE=/var/log/sentinelx-mcp/sentinelx-core-mcp.log
```

### About `OIDC_EXPECTED_AUDIENCE`

This field tells the MCP server what value to expect in the `aud` claim of incoming tokens.

- Set it to your **client ID** (`sentinelx-mcp`) if Keycloak includes the client ID in the `aud` claim (common with confidential clients and the `account` audience mapper).
- Leave it **empty** if your tokens don't include a specific audience or if you're unsure — the MCP server will skip audience validation.
- If protected tools fail with `Invalid access token`, try toggling this value and check the token claims with `AUTH_DEBUG=true`.

Restart after any change:

```bash
sudo systemctl restart sentinelx-core-mcp
sudo systemctl status sentinelx-core-mcp
```

---

## 5. Obtain an access token

### Option A — Client credentials flow (machine-to-machine)

Use this for scripts, CI jobs, or any non-interactive integration.

```bash
TOKEN=$(curl -s -X POST \
  https://auth.example.com/realms/sentinelx/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=sentinelx-mcp" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "scope=sentinelx:exec sentinelx:state sentinelx:capabilities" \
  | jq -r '.access_token')

echo $TOKEN
```

Inspect the token claims to verify scopes and audience:

```bash
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq
```

### Option B — Authorization Code flow (interactive clients)

Use this for Claude, ChatGPT, Cursor and other interactive MCP clients. The client handles the OAuth flow automatically — you just need Keycloak configured correctly (realm, client, scopes, redirect URIs from section 2).

When the MCP client connects for the first time it will redirect to Keycloak's login page. After authentication it receives a token and uses it automatically.

---

## 6. Connect Claude

In Claude's settings, add a new MCP server:

```
https://sentinelx.example.com/mcp
```

Claude will redirect to Keycloak for login on first use. After you authenticate, Claude will receive a token scoped to whatever you authorized and will have access to the corresponding tools.

**Make sure** your Keycloak client includes this redirect URI:

```
https://claude.ai/api/mcp/auth_callback
```

And that `sentinelx.example.com` exposes the OAuth protected resource metadata at:

```
https://sentinelx.example.com/.well-known/oauth-protected-resource
```

Example nginx config for that endpoint:

```nginx
location = /.well-known/oauth-protected-resource {
    default_type application/json;
    return 200 '{
      "resource": "https://sentinelx.example.com",
      "authorization_servers": [
        "https://auth.example.com/realms/sentinelx"
      ],
      "scopes_supported": [
        "sentinelx:exec",
        "sentinelx:edit",
        "sentinelx:state",
        "sentinelx:service",
        "sentinelx:upload",
        "sentinelx:script",
        "sentinelx:capabilities"
      ]
    }';
}
```

---

## 7. Smoke test with curl

A full end-to-end test from token acquisition to protected tool call.

### Step 1 — Get a token

```bash
TOKEN=$(curl -s -X POST \
  https://auth.example.com/realms/sentinelx/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=sentinelx-mcp" \
  -d "client_secret=YOUR_CLIENT_SECRET" \
  -d "scope=sentinelx:exec sentinelx:state" \
  | jq -r '.access_token')
```

### Step 2 — Initialize an MCP session

```bash
SESSION=$(curl -si -X POST https://sentinelx.example.com/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "init-1",
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-03-26",
      "capabilities": {},
      "clientInfo": {"name": "curl", "version": "0.1"}
    }
  }' | grep -i mcp-session-id | awk '{print $2}' | tr -d '\r')

echo "Session: $SESSION"
```

### Step 3 — Notify initialized

```bash
curl -s -X POST https://sentinelx.example.com/mcp \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: $SESSION" \
  -d '{"jsonrpc": "2.0", "method": "notifications/initialized"}'
```

### Step 4 — Call a public tool (no token needed)

```bash
curl -s -X POST https://sentinelx.example.com/mcp \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: $SESSION" \
  -d '{
    "jsonrpc": "2.0",
    "id": "ping-1",
    "method": "tools/call",
    "params": {"name": "ping", "arguments": {}}
  }' | sed -n 's/^data: //p' | jq
```

### Step 5 — Call a protected tool with the token

```bash
curl -s -X POST https://sentinelx.example.com/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -H "mcp-session-id: $SESSION" \
  -d '{
    "jsonrpc": "2.0",
    "id": "exec-1",
    "method": "tools/call",
    "params": {"name": "sentinel_exec", "arguments": {"cmd": "uptime"}}
  }' | sed -n 's/^data: //p' | jq
```

Expected: `{"output": "...", "returncode": 0, ...}`

If the token is missing the required scope, the MCP layer returns a `PermissionError`.

---

## 8. Troubleshooting

### Token rejected — `Invalid access token`

```bash
# 1. Enable debug logging temporarily
sudo sed -i 's/AUTH_DEBUG=false/AUTH_DEBUG=true/' /etc/sentinelx-core-mcp/sentinelx-core-mcp.env
sudo systemctl restart sentinelx-core-mcp

# 2. Make a request and check the logs
sudo tail -f /var/log/sentinelx-mcp/sentinelx-core-mcp.log

# 3. Decode the token manually and inspect claims
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq '{iss, aud, scope: .scope, exp}'

# 4. Disable debug when done
sudo sed -i 's/AUTH_DEBUG=true/AUTH_DEBUG=false/' /etc/sentinelx-core-mcp/sentinelx-core-mcp.env
sudo systemctl restart sentinelx-core-mcp
```

Common causes:

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Invalid issuer` | `OIDC_ISSUER` mismatch | Check the `iss` claim in the token vs the env value |
| `Invalid audience` | `OIDC_EXPECTED_AUDIENCE` mismatch | Check the `aud` claim or leave the env value empty |
| `Signature verification failed` | Wrong JWKS URI | Verify `OIDC_JWKS_URI` resolves correctly |
| Token expired | Client not refreshing | Re-acquire the token |

### `ping` works but protected tools fail

The MCP transport and session are fine. The issue is auth or scope:

1. Verify the token includes the required scope (check the `scope` claim)
2. Verify `OIDC_EXPECTED_AUDIENCE` is correct or empty
3. Enable `AUTH_DEBUG=true` and re-run to see detailed validation output

### Scope missing from token

In Keycloak, verify:

- The scope exists as a Client Scope
- The scope is assigned to the `sentinelx-mcp` client
- The scope was included in the token request (`-d "scope=sentinelx:exec ..."`)
- The scope mapper is configured to include the scope in the token (not just in the userinfo endpoint)

### Protected MCP call works but the upstream action fails

The OIDC layer is fine. Check the upstream:

```bash
# Verify sentinelx-core is running
sudo systemctl status sentinelx

# Check the internal token matches
grep SENTINELX_TOKEN /etc/sentinelx-core-mcp/sentinelx-core-mcp.env
grep SENTINEL_TOKEN /etc/sentinelx/sentinelx.env

# Test the upstream directly
curl -s -H "Authorization: Bearer YOUR_INTERNAL_TOKEN" \
  http://127.0.0.1:8091/state | jq
```

### Claude shows auth error after connecting

1. Verify the redirect URI `https://claude.ai/api/mcp/auth_callback` is in the Keycloak client
2. Verify `/.well-known/oauth-protected-resource` returns valid JSON with the correct `authorization_servers` value
3. Check `RESOURCE_URL` in the env matches the domain Claude is connecting to

---

## Summary

| Step | What you configure | Where |
|------|-------------------|-------|
| 1 | Realm | Keycloak admin |
| 2 | Client + redirect URIs | Keycloak admin |
| 3 | Client scopes | Keycloak admin |
| 4 | OIDC env vars | `/etc/sentinelx-core-mcp/sentinelx-core-mcp.env` |
| 5 | Token acquisition | curl or MCP client OAuth flow |
| 6 | Claude connection | Claude settings → Add MCP server |
| 7 | Smoke test | curl end-to-end |

Any OIDC-compatible provider follows the same pattern. Keycloak is the reference implementation used during development, but the env vars and token validation logic are provider-agnostic.
