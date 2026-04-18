# OIDC provider alternatives for SentinelX Core MCP

`sentinelx-core-mcp` works with any OIDC-compatible provider. Keycloak is the reference implementation used during development, but it is heavyweight for a simple homelab setup.

This document covers three lighter alternatives and helps you choose the right one for your situation.

---

## Choosing a provider

| Provider | Best for | Resource usage | Complexity |
|----------|---------|---------------|-----------|
| **Keycloak** | Enterprise, existing deployments | High (~512 MB RAM) | High |
| **Authentik** | Homelabs, self-hosting, rich UI | Medium (~300 MB RAM) | Medium |
| **Zitadel** | API-driven, multi-server setups | Low (~100 MB RAM) | Medium |
| **Authelia** | Simple SSO proxy, no user management | Very low (~50 MB RAM) | Low — but limited |

**Recommendation for most SentinelX users:**

- Already running Keycloak → keep using it, see [`keycloak-example.md`](keycloak-example.md)
- Homelab, want a good UI → **Authentik**
- Multiple servers, API-first, want low resource usage → **Zitadel**
- Just want the simplest possible setup → **Zitadel** (single binary, fastest to get running)

---

## Option A — Authentik

Authentik is the most homelab-friendly full IAM. Large community, excellent documentation, intuitive UI with a visual flow designer.

### 1. Install with Docker Compose

```bash
mkdir authentik && cd authentik

# Download the official compose file
curl -O https://goauthentik.io/docker-compose.yml

# Generate a secret key and password
echo "PG_PASS=$(openssl rand -hex 16)" > .env
echo "AUTHENTIK_SECRET_KEY=$(openssl rand -hex 32)" >> .env
echo "AUTHENTIK_ERROR_REPORTING__ENABLED=false" >> .env

docker compose up -d
```

Authentik will be available at `https://your-server:9443`.

First-time setup: visit `https://your-server:9443/if/flow/initial-setup/` to create the admin user.

### 2. Create an application and provider

In the Authentik admin UI (`Admin interface → Applications`):

1. Go to **Applications → Providers → Create**
2. Choose **OAuth2/OpenID Provider**
3. Configure:

| Field | Value |
|-------|-------|
| Name | `sentinelx-mcp` |
| Authorization flow | `default-provider-authorization-explicit-consent` |
| Client type | Confidential |
| Redirect URIs | `https://claude.ai/api/mcp/auth_callback` (add others as needed) |

4. Note the **Client ID** and **Client Secret**
5. Go to **Applications → Create** and link it to the provider

### 3. Create custom scopes

Authentik supports custom scopes via **Property Mappings**.

Go to **Customization → Property Mappings → Create → Scope Mapping** for each scope:

```
sentinelx:exec
sentinelx:edit
sentinelx:state
sentinelx:service
sentinelx:restart
sentinelx:upload
sentinelx:script
sentinelx:capabilities
```

For each mapping, set the expression to:
```python
return None
```

Then assign the mappings to your provider under **Advanced protocol settings → Scopes**.

### 4. Configure sentinelx-core-mcp

```env
OIDC_ISSUER=https://authentik.example.com/application/o/sentinelx-mcp/
OIDC_JWKS_URI=https://authentik.example.com/application/o/sentinelx-mcp/jwks/
OIDC_EXPECTED_AUDIENCE=   # leave empty or set to your client ID
RESOURCE_URL=https://sentinelx.example.com
```

> The issuer URL in Authentik ends with a trailing slash and includes the application slug. Verify it at `https://authentik.example.com/application/o/sentinelx-mcp/.well-known/openid-configuration`

---

## Option B — Zitadel

Zitadel is a single Go binary with a clean API-first design. Lower resource usage than Authentik or Keycloak, and the fastest to get running from scratch.

> **Note:** Zitadel switched from Apache 2.0 to AGPL 3.0 in 2025. Review the license if this matters for your use case.

### 1. Install with Docker Compose

```bash
mkdir zitadel && cd zitadel

curl -LO https://raw.githubusercontent.com/zitadel/zitadel/main/deploy/compose/docker-compose.yml
curl -LO https://raw.githubusercontent.com/zitadel/zitadel/main/deploy/compose/.env.example
cp .env.example .env

# Edit .env — set ZITADEL_EXTERNALDOMAIN to your domain
nano .env

docker compose up -d --wait
```

Zitadel will be available at `https://your-domain:8080`.

The first-run output includes a machine user key file — save it, you'll need it to bootstrap via API.

### 2. Create a project and application

In the Zitadel console:

1. Create a new **Project** (e.g. `sentinelx`)
2. Inside the project, create an **Application**:
   - Type: **Web**
   - Name: `sentinelx-mcp`
   - Auth method: **Code + PKCE** (for interactive clients) or **Basic** (for machine-to-machine)
3. Add redirect URIs:
   - `https://claude.ai/api/mcp/auth_callback`
   - `https://chatgpt.com/aip/g-*/oauth/callback` (if using ChatGPT)
4. Note the **Client ID** (and **Client Secret** if using Basic auth)

### 3. Create custom scopes (Actions)

Zitadel handles custom scopes via **Actions**. Go to **Actions → Create** and add a flow that includes the sentinelx scopes in the token claims.

Alternatively, for a simpler setup, you can skip custom scopes and use a single broad scope. In the MCP env, set `OIDC_EXPECTED_AUDIENCE` to the client ID and leave individual scope enforcement loose during testing.

### 4. Configure sentinelx-core-mcp

```env
OIDC_ISSUER=https://zitadel.example.com
OIDC_JWKS_URI=https://zitadel.example.com/oauth/v2/keys
OIDC_EXPECTED_AUDIENCE=your-client-id@your-project-id
RESOURCE_URL=https://sentinelx.example.com
```

> Verify the correct issuer and JWKS URI at `https://zitadel.example.com/.well-known/openid-configuration`

---

## Option C — Zitadel Cloud (zero infrastructure)

If you don't want to self-host the identity provider at all, Zitadel offers a **free cloud tier** that is sufficient for personal use.

1. Create a free account at [zitadel.com](https://zitadel.com)
2. Create a project and application following the same steps as Option B
3. Your issuer will be `https://your-instance.zitadel.cloud`

This is the fastest path to a working setup if you just want to try SentinelX without running any additional infrastructure.

---

## Common configuration regardless of provider

Once you have any OIDC provider running, the MCP env vars follow the same pattern:

```env
# Always required
OIDC_ISSUER=https://your-provider/your-realm-or-tenant
OIDC_JWKS_URI=https://your-provider/your-realm-or-tenant/jwks-path

# Set to your client ID, or leave empty to skip audience validation
OIDC_EXPECTED_AUDIENCE=

# The public URL of your MCP endpoint
RESOURCE_URL=https://sentinelx.example.com
```

**Finding the correct values:** every OIDC provider exposes a discovery document at:
```
https://your-provider/.well-known/openid-configuration
```

The `issuer` and `jwks_uri` fields in that document are exactly what you need.

```bash
curl -s https://your-provider/.well-known/openid-configuration | jq '{issuer, jwks_uri}'
```

---

## Troubleshooting any provider

If tokens are rejected, the fastest way to diagnose is:

```bash
# 1. Enable debug mode
sudo sed -i 's/AUTH_DEBUG=false/AUTH_DEBUG=true/' /etc/sentinelx-core-mcp/sentinelx-core-mcp.env
sudo systemctl restart sentinelx-core-mcp

# 2. Decode your token and check the claims
echo YOUR_TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq '{iss, aud, scope: .scope}'

# 3. Compare iss with OIDC_ISSUER — they must match exactly
# 4. Compare aud with OIDC_EXPECTED_AUDIENCE — or leave it empty

# 5. Disable debug when done
sudo sed -i 's/AUTH_DEBUG=true/AUTH_DEBUG=false/' /etc/sentinelx-core-mcp/sentinelx-core-mcp.env
sudo systemctl restart sentinelx-core-mcp
```

Common mismatches:

| Error | Cause | Fix |
|-------|-------|-----|
| `Invalid issuer` | Trailing slash difference or wrong realm | Copy `iss` from the decoded token into `OIDC_ISSUER` exactly |
| `Invalid audience` | Provider uses client ID or resource URL in `aud` | Set `OIDC_EXPECTED_AUDIENCE` to match, or leave empty |
| `No address associated with hostname` | Service started before DNS was ready | Ensure `network-online.target` is in the systemd unit — already fixed in v0.1.1+ |
| `Missing required scope` | Scope not in token | Verify scope is assigned to the client in the provider |
