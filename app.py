import os
import time
import base64
from typing import Any, Dict, Optional, Set

import httpx
import jwt
from urllib.parse import urlparse
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError

load_dotenv()

MCP_PORT = int(os.getenv("MCP_PORT", "8099"))

SENTINELX_URL = os.getenv("SENTINELX_URL", "http://127.0.0.1:8092").rstrip("/")
SENTINEL_TOKEN = os.getenv("SENTINEL_TOKEN", "").strip()

OIDC_ISSUER = os.getenv("OIDC_ISSUER", "https://auth.example.com/realms/sentinelx").rstrip("/")
OIDC_JWKS_URI = os.getenv(
    "OIDC_JWKS_URI",
    "https://auth.example.com/realms/sentinelx/protocol/openid-connect/certs",
).strip()
OIDC_EXPECTED_AUDIENCE = os.getenv("OIDC_EXPECTED_AUDIENCE", "").strip()

RESOURCE_URL = os.getenv("RESOURCE_URL", "https://sentinelx.example.com").rstrip("/")

AUTH_DEBUG = os.getenv("AUTH_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}

SERVER_NAME = "sentinelx-core-mcp"
SERVER_VERSION = "0.3.5"

INSTRUCTIONS = f"""
This MCP server proxies tool calls to an internal SentinelX agent.

- Internal SentinelX base URL: {SENTINELX_URL}
- External resource URL: {RESOURCE_URL}
- OAuth issuer: {OIDC_ISSUER}

Exposed tools:
- ping(): simple server health check
- sentinel_state(): fetch SentinelX /state
- sentinel_exec(cmd): execute an allowed command through SentinelX /exec
- sentinel_restart(service): restart an allowed service through SentinelX /restart
- sentinel_service(service, action): execute a supported service action through SentinelX /service
- sentinel_upload_file(target_path, file_url=None, content_base64=None, filename=None, overwrite=False): upload a file in one request through SentinelX /upload; provide exactly one of file_url or content_base64
- sentinel_upload_init(target_path, total_size, filename=None, overwrite=False): initialize a chunked upload through SentinelX /upload/init
- sentinel_upload_chunk(upload_id, index, chunk_base64, filename="chunk.bin"): upload one chunk through SentinelX /upload/chunk
- sentinel_upload_complete(upload_id, sha256=None): finalize a chunked upload through SentinelX /upload/complete
- sentinel_edit(...): structured file edit through SentinelX /edit without shell quoting
- sentinel_edit_upload_init(): initialize large structured edit upload through SentinelX /edit/upload/init
- sentinel_edit_upload_file(upload_id, role, input_file): upload role=new|old file for structured editing through SentinelX /edit/upload/file
- sentinel_edit_upload_complete(...): finalize large structured edit through SentinelX /edit/upload/complete
- sentinel_script_run(...): execute a temporary bash/python3 script through SentinelX /script/run
- sentinel_capabilities(): returns allowed commands, rich service metadata, categories, locations, playbooks and embedded help exposed by SentinelX
- sentinel_help(): returns the help section exposed by SentinelX capabilities

Security model:
- ping() is public for connectivity checks.
- Protected tools require OAuth Bearer access tokens.
- Tokens are validated against the configured OIDC JWKS endpoint.
- SentinelX itself enforces its allowlist, service policies and its own internal Bearer token.
"""

mcp = FastMCP(name=SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS)

_jwk_client = PyJWKClient(OIDC_JWKS_URI)
_jwks_last_reset = 0.0


def _debug(msg: str) -> None:
    if AUTH_DEBUG:
        print(f"[AUTH DEBUG] {msg}", flush=True)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _headers_get(headers: Any, name: str) -> Optional[str]:
    if headers is None:
        return None

    try:
        value = headers.get(name)
        if value:
            return value
    except Exception:
        pass

    try:
        value = headers.get(name.lower())
        if value:
            return value
    except Exception:
        pass

    try:
        value = headers.get(name.title())
        if value:
            return value
    except Exception:
        pass

    return None


def _get_request_auth_header() -> Optional[str]:
    """
    Robust extraction of Authorization header.

    Strategy:
    1. Try FastMCP's HTTP request helper (best for streamable HTTP).
    2. Fallback to mcp.get_context() shapes.
    """
    try:
        request = get_http_request()
        if request is not None:
            auth = request.headers.get("authorization") or request.headers.get("Authorization")
            _debug(f"get_http_request() present={request is not None}, auth_present={bool(auth)}")
            if auth:
                return auth
    except Exception as exc:
        _debug(f"get_http_request() failed: {exc!r}")

    try:
        ctx = mcp.get_context()
        headers = None

        if hasattr(ctx, "request_context") and getattr(ctx, "request_context", None) is not None:
            rc = ctx.request_context
            if hasattr(rc, "headers"):
                headers = rc.headers
            elif hasattr(rc, "request") and getattr(rc, "request", None) is not None:
                req = rc.request
                if hasattr(req, "headers"):
                    headers = req.headers

        if headers is None and hasattr(ctx, "request") and getattr(ctx, "request", None) is not None:
            req = ctx.request
            if hasattr(req, "headers"):
                headers = req.headers

        auth = _headers_get(headers, "authorization")
        _debug(f"mcp.get_context() fallback auth_present={bool(auth)}")
        return auth
    except Exception as exc:
        _debug(f"mcp.get_context() fallback failed: {exc!r}")
        return None


def _normalize_scopes(claims: Dict[str, Any]) -> Set[str]:
    """
    Support both Keycloak-style 'scope' string and possible 'scp' list/string.
    """
    raw = claims.get("scope")
    if raw is None:
        raw = claims.get("scp")

    if raw is None:
        return set()

    if isinstance(raw, str):
        return {item.strip() for item in raw.split() if item.strip()}

    if isinstance(raw, list):
        return {str(item).strip() for item in raw if str(item).strip()}

    return set()


def _extract_token() -> str:
    auth = _get_request_auth_header()
    _debug(f"Authorization header present? {bool(auth)}")

    if not auth:
        raise PermissionError("Missing Authorization header (Bearer token required).")

    if not auth.lower().startswith("bearer "):
        raise PermissionError("Invalid Authorization header format. Use: Bearer <token>")

    token = auth.split(" ", 1)[1].strip()
    if not token:
        raise PermissionError("Bearer token is empty.")

    _debug(f"Bearer token extracted, prefix={token[:16]}...")
    return token


def _decode_token(token: str) -> Dict[str, Any]:
    global _jwk_client, _jwks_last_reset

    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token).key
    except Exception:
        if time.time() - _jwks_last_reset > 10:
            _jwk_client = PyJWKClient(OIDC_JWKS_URI)
            _jwks_last_reset = time.time()
        signing_key = _jwk_client.get_signing_key_from_jwt(token).key

    options = {
        "require": ["exp", "iat", "iss"],
        "verify_signature": True,
        "verify_exp": True,
        "verify_iat": True,
        "verify_iss": True,
        "verify_aud": bool(OIDC_EXPECTED_AUDIENCE),
    }

    kwargs: Dict[str, Any] = {
        "key": signing_key,
        "algorithms": [
            "RS256", "RS384", "RS512",
            "PS256", "PS384", "PS512",
            "ES256", "ES384", "ES512",
            "EdDSA",
        ],
        "issuer": OIDC_ISSUER,
        "options": options,
    }

    if OIDC_EXPECTED_AUDIENCE:
        kwargs["audience"] = OIDC_EXPECTED_AUDIENCE

    claims = jwt.decode(token, **kwargs)
    _debug(f"Token decoded. claims_keys={list(claims.keys())}")
    _debug(f"scope/scp={claims.get('scope', claims.get('scp'))}")
    return claims


def _require_oauth_scope(required_scope: str) -> Dict[str, Any]:
    token = _extract_token()

    try:
        claims = _decode_token(token)
    except InvalidTokenError as exc:
        raise PermissionError(f"Invalid access token: {exc}") from exc
    except Exception as exc:
        raise PermissionError(f"Unable to validate access token: {exc}") from exc

    scopes = _normalize_scopes(claims)
    if required_scope not in scopes:
        raise PermissionError(
            f"Missing required scope '{required_scope}'. Token scopes: {sorted(scopes)}"
        )

    return claims


async def _sentinelx_get(path: str) -> Dict[str, Any]:
    url = f"{SENTINELX_URL}{path}"
    headers = {"Authorization": f"Bearer {SENTINEL_TOKEN}"}
    timeout = httpx.Timeout(10.0, connect=5.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def _sentinelx_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{SENTINELX_URL}{path}"
    headers = {
        "Authorization": f"Bearer {SENTINEL_TOKEN}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(75.0, connect=5.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


async def _sentinelx_post_multipart(path: str, data: Dict[str, Any], files: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{SENTINELX_URL}{path}"
    headers = {"Authorization": f"Bearer {SENTINEL_TOKEN}"}
    timeout = httpx.Timeout(300.0, connect=5.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, data=data, files=files)
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def ping() -> Dict[str, Any]:
    """
    Simple health check (does not call SentinelX).
    Public endpoint for connectivity checks.
    """
    auth_present = bool(_get_request_auth_header())

    return {
        "ok": True,
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "_meta": {
            "resource_url": RESOURCE_URL,
            "sentinelx_url": SENTINELX_URL,
            "auth_header_present": auth_present,
        },
    }


@mcp.tool()
async def sentinel_state() -> Dict[str, Any]:
    """
    Get SentinelX internal state (/state).
    Requires scope: sentinelx:state
    """
    claims = _require_oauth_scope("sentinelx:state")

    start = _now_ms()
    data = await _sentinelx_get("/state")
    data["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return data


@mcp.tool()
async def sentinel_exec(cmd: str) -> Dict[str, Any]:
    """
    Execute a command through SentinelX (/exec).
    Requires scope: sentinelx:exec
    """
    claims = _require_oauth_scope("sentinelx:exec")

    if not cmd or not cmd.strip():
        return {"error": "cmd is required"}

    start = _now_ms()
    data = await _sentinelx_post("/exec", {"cmd": cmd.strip()})
    data["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return data


@mcp.tool()
async def sentinel_restart(service: str) -> Dict[str, Any]:
    """
    Restart an allowed service through SentinelX (/restart).
    Requires scope: sentinelx:restart
    """
    claims = _require_oauth_scope("sentinelx:restart")

    if not service or not service.strip():
        return {"error": "service is required"}

    start = _now_ms()
    data = await _sentinelx_post("/restart", {"service": service.strip()})
    data["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return data

@mcp.tool()
async def sentinel_service(service: str, action: str) -> Dict[str, Any]:
    """
    Execute a supported service action through SentinelX (/service).
    Uses the same scope as sentinel_restart for now: sentinelx:service
    """
    claims = _require_oauth_scope("sentinelx:service")

    if not service or not service.strip():
        return {"error": "service is required"}
    if not action or not action.strip():
        return {"error": "action is required"}

    start = _now_ms()
    data = await _sentinelx_post(
        "/service",
        {
            "service": service.strip(),
            "action": action.strip(),
        },
    )
    data["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return data
    
    
async def _read_input_file_bytes(input_file: str) -> tuple[bytes, str]:
    """
    Resolve a file reference string into (content_bytes, filename).

    Supported forms:
    - local path, e.g. /mnt/data/file.txt
    - file:///mnt/data/file.txt
    - http://...
    - https://...
    """
    if not input_file or not input_file.strip():
        raise ValueError("input_file is required")

    raw = input_file.strip()

    if raw.startswith("file://"):
        parsed = urlparse(raw)
        local_path = parsed.path
        if not local_path or not os.path.isfile(local_path):
            raise FileNotFoundError(f"input_file does not exist: {raw}")
        with open(local_path, "rb") as f:
            return f.read(), os.path.basename(local_path) or "upload.bin"

    if raw.startswith("http://") or raw.startswith("https://"):
        timeout = httpx.Timeout(300.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(raw)
            response.raise_for_status()
            parsed = urlparse(str(response.url))
            filename = os.path.basename(parsed.path) or "upload.bin"
            return response.content, filename

    local_path = os.path.abspath(raw)
    if not os.path.isfile(local_path):
        raise FileNotFoundError(f"input_file does not exist or is not a file: {local_path}")

    with open(local_path, "rb") as f:
        return f.read(), os.path.basename(local_path) or "upload.bin"


@mcp.tool()
async def sentinel_upload_file(
    target_path: str,
    file_url: Optional[str] = None,
    content_base64: Optional[str] = None,
    filename: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """
    Upload a file in one request through SentinelX (/upload).
    Requires scope: sentinelx:upload

    Exactly one of:
    - file_url: real http/https URL
    - content_base64: backward-compatible content upload
    """
    claims = _require_oauth_scope("sentinelx:upload")

    if not target_path or not target_path.strip():
        return {"error": "target_path is required"}

    has_url = bool(file_url and file_url.strip())
    has_base64 = bool(content_base64 and content_base64.strip())

    if has_url == has_base64:
        return {"error": "provide exactly one of: file_url or content_base64"}

    try:
        if has_url:
            raw_url = file_url.strip()
            if not (raw_url.startswith("http://") or raw_url.startswith("https://")):
                return {"error": "file_url must start with http:// or https://"}

            timeout = httpx.Timeout(300.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(raw_url)
                response.raise_for_status()
                content = response.content

                parsed = urlparse(str(response.url))
                detected_name = os.path.basename(parsed.path) or "upload.bin"

            effective_name = (
                filename
                or detected_name
                or os.path.basename(target_path.strip())
                or "upload.bin"
            )
            upload_mode = "file_url"

        else:
            try:
                content = base64.b64decode(content_base64, validate=True)
            except Exception as exc:
                return {"error": f"invalid base64: {exc}"}

            effective_name = (
                filename
                or os.path.basename(target_path.strip())
                or "upload.bin"
            )
            upload_mode = "content_base64"

        start = _now_ms()
        result = await _sentinelx_post_multipart(
            "/upload",
            data={
                "target_path": target_path.strip(),
                "overwrite": str(bool(overwrite)).lower(),
            },
            files={
                "file": (effective_name, content, "application/octet-stream"),
            },
        )
        result["_meta"] = {
            "proxy": SERVER_NAME,
            "ms": _now_ms() - start,
            "subject": claims.get("sub"),
            "preferred_username": claims.get("preferred_username"),
            "upload_mode": upload_mode,
        }
        return result

    except Exception as exc:
        return {"error": f"upload failed: {exc}"}


@mcp.tool()
async def sentinel_upload_init(
    target_path: str,
    total_size: int,
    filename: Optional[str] = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """
    Initialize a chunked upload through SentinelX (/upload/init).
    Requires scope: sentinelx:upload
    """
    claims = _require_oauth_scope("sentinelx:upload")

    if not target_path or not target_path.strip():
        return {"error": "target_path is required"}
    if total_size < 0:
        return {"error": "total_size must be >= 0"}

    start = _now_ms()
    result = await _sentinelx_post(
        "/upload/init",
        {
            "target_path": target_path.strip(),
            "overwrite": bool(overwrite),
            "total_size": int(total_size),
            "filename": filename,
        },
    )
    result["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return result


@mcp.tool()
async def sentinel_upload_chunk(
    upload_id: str,
    index: int,
    chunk_base64: str,
    filename: str = "chunk.bin",
) -> Dict[str, Any]:
    """
    Upload one chunk through SentinelX (/upload/chunk).
    Requires scope: sentinelx:upload
    """
    claims = _require_oauth_scope("sentinelx:upload")

    if not upload_id or not upload_id.strip():
        return {"error": "upload_id is required"}
    if index < 0:
        return {"error": "index must be >= 0"}
    if not chunk_base64 or not chunk_base64.strip():
        return {"error": "chunk_base64 is required"}

    try:
        chunk = base64.b64decode(chunk_base64, validate=True)
    except Exception as exc:
        return {"error": f"invalid base64: {exc}"}

    start = _now_ms()
    result = await _sentinelx_post_multipart(
        "/upload/chunk",
        data={
            "upload_id": upload_id.strip(),
            "index": str(int(index)),
        },
        files={
            "chunk": (filename or "chunk.bin", chunk, "application/octet-stream"),
        },
    )
    result["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return result


@mcp.tool()
async def sentinel_upload_complete(upload_id: str, sha256: Optional[str] = None) -> Dict[str, Any]:
    """
    Finalize a chunked upload through SentinelX (/upload/complete).
    Requires scope: sentinelx:upload
    """
    claims = _require_oauth_scope("sentinelx:upload")

    if not upload_id or not upload_id.strip():
        return {"error": "upload_id is required"}

    payload: Dict[str, Any] = {"upload_id": upload_id.strip()}
    if sha256:
        payload["sha256"] = sha256.strip()

    start = _now_ms()
    result = await _sentinelx_post("/upload/complete", payload)
    result["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return result


@mcp.tool()
async def sentinel_edit(
    path: str,
    mode: str,
    sudo: bool = False,
    old: Optional[str] = None,
    new_text: Optional[str] = None,
    pattern: Optional[str] = None,
    start_marker: Optional[str] = None,
    end_marker: Optional[str] = None,
    count: int = 0,
    multiline: bool = False,
    dotall: bool = False,
    interpret_escapes: bool = False,
    backup_dir: Optional[str] = None,
    validator: Optional[str] = None,
    validator_preset: Optional[str] = None,
    diff: bool = False,
    dry_run: bool = False,
    allow_no_change: bool = False,
    create: bool = False,
) -> Dict[str, Any]:
    """
    Structured file edit through SentinelX (/edit) without shell quoting.
    Requires scope: sentinelx:edit
    """
    claims = _require_oauth_scope("sentinelx:edit")

    if not path or not path.strip():
        return {"error": "path is required"}
    if not mode or not mode.strip():
        return {"error": "mode is required"}
    if count < 0:
        return {"error": "count must be >= 0"}

    payload: Dict[str, Any] = {
        "path": path.strip(),
        "mode": mode.strip(),
        "sudo": bool(sudo),
        "count": int(count),
        "multiline": bool(multiline),
        "dotall": bool(dotall),
        "interpret_escapes": bool(interpret_escapes),
        "diff": bool(diff),
        "dry_run": bool(dry_run),
        "allow_no_change": bool(allow_no_change),
        "create": bool(create),
    }

    if old is not None:
        payload["old"] = old
    if new_text is not None:
        payload["new_text"] = new_text
    if pattern is not None:
        payload["pattern"] = pattern
    if start_marker is not None:
        payload["start_marker"] = start_marker
    if end_marker is not None:
        payload["end_marker"] = end_marker
    if backup_dir is not None:
        payload["backup_dir"] = backup_dir
    if validator is not None:
        payload["validator"] = validator
    if validator_preset is not None:
        payload["validator_preset"] = validator_preset

    start = _now_ms()
    result = await _sentinelx_post("/edit", payload)
    result["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return result


@mcp.tool()
async def sentinel_edit_upload_init() -> Dict[str, Any]:
    """
    Initialize a large structured edit upload through SentinelX (/edit/upload/init).
    Requires scope: sentinelx:edit
    """
    claims = _require_oauth_scope("sentinelx:edit")

    start = _now_ms()
    result = await _sentinelx_post("/edit/upload/init", {})
    result["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return result


@mcp.tool()
async def sentinel_edit_upload_file(
    upload_id: str,
    role: str,
    input_file: str,
) -> Dict[str, Any]:
    """
    Upload a role file for structured editing through SentinelX (/edit/upload/file).

    role must be:
    - new
    - old

    input_file supports:
    - local path
    - file:///...
    - http://...
    - https://...

    Requires scope: sentinelx:edit
    """
    claims = _require_oauth_scope("sentinelx:edit")

    if not upload_id or not upload_id.strip():
        return {"error": "upload_id is required"}
    if role not in {"new", "old"}:
        return {"error": "role must be 'new' or 'old'"}
    if not input_file or not input_file.strip():
        return {"error": "input_file is required"}

    try:
        content, detected_name = await _read_input_file_bytes(input_file)
    except Exception as exc:
        return {"error": f"unable to read input_file: {exc}"}

    start = _now_ms()
    result = await _sentinelx_post_multipart(
        "/edit/upload/file",
        data={
            "upload_id": upload_id.strip(),
            "role": role,
        },
        files={
            "file": (detected_name or f"{role}.bin", content, "application/octet-stream"),
        },
    )
    result["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return result


@mcp.tool()
async def sentinel_edit_upload_complete(
    upload_id: str,
    path: str,
    mode: str,
    sudo: bool = False,
    pattern: Optional[str] = None,
    start_marker: Optional[str] = None,
    end_marker: Optional[str] = None,
    count: int = 0,
    multiline: bool = False,
    dotall: bool = False,
    interpret_escapes: bool = False,
    backup_dir: Optional[str] = None,
    validator: Optional[str] = None,
    validator_preset: Optional[str] = None,
    diff: bool = False,
    dry_run: bool = False,
    allow_no_change: bool = False,
    create: bool = False,
) -> Dict[str, Any]:
    """
    Finalize a large structured edit through SentinelX (/edit/upload/complete).
    Requires scope: sentinelx:edit
    """
    claims = _require_oauth_scope("sentinelx:edit")

    if not upload_id or not upload_id.strip():
        return {"error": "upload_id is required"}
    if not path or not path.strip():
        return {"error": "path is required"}
    if not mode or not mode.strip():
        return {"error": "mode is required"}
    if count < 0:
        return {"error": "count must be >= 0"}

    payload: Dict[str, Any] = {
        "upload_id": upload_id.strip(),
        "path": path.strip(),
        "mode": mode.strip(),
        "sudo": bool(sudo),
        "count": int(count),
        "multiline": bool(multiline),
        "dotall": bool(dotall),
        "interpret_escapes": bool(interpret_escapes),
        "diff": bool(diff),
        "dry_run": bool(dry_run),
        "allow_no_change": bool(allow_no_change),
        "create": bool(create),
    }

    if pattern is not None:
        payload["pattern"] = pattern
    if start_marker is not None:
        payload["start_marker"] = start_marker
    if end_marker is not None:
        payload["end_marker"] = end_marker
    if backup_dir is not None:
        payload["backup_dir"] = backup_dir
    if validator is not None:
        payload["validator"] = validator
    if validator_preset is not None:
        payload["validator_preset"] = validator_preset

    start = _now_ms()
    result = await _sentinelx_post("/edit/upload/complete", payload)
    result["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return result


@mcp.tool()
async def sentinel_script_run(
    interpreter: str,
    content: str,
    args: Optional[list[str]] = None,
    cwd: Optional[str] = None,
    timeout: int = 60,
    sudo: bool = False,
    cleanup: bool = True,
    filename: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Execute a temporary structured script through SentinelX (/script/run).
    Requires scope: sentinelx:script
    """
    claims = _require_oauth_scope("sentinelx:script")

    if not interpreter or not interpreter.strip():
        return {"error": "interpreter is required"}
    if interpreter.strip() not in {"bash", "python3"}:
        return {"error": "interpreter must be bash or python3"}
    if not content or not content.strip():
        return {"error": "content is required"}
    if timeout < 1 or timeout > 300:
        return {"error": "timeout must be between 1 and 300 seconds"}

    payload: Dict[str, Any] = {
        "interpreter": interpreter.strip(),
        "content": content,
        "cwd": cwd,
        "timeout": int(timeout),
        "sudo": bool(sudo),
        "cleanup": bool(cleanup),
        "filename": filename,
        "env": env,
    }
    if args is not None:
        payload["args"] = args

    start = _now_ms()
    result = await _sentinelx_post("/script/run", payload)
    result["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }
    return result


@mcp.tool()
async def sentinel_capabilities() -> Dict[str, Any]:
    """
    Get SentinelX capabilities (/capabilities), including allowed commands,
    rich service metadata, categories, locations, playbooks and embedded help.
    Requires scope: sentinelx:capabilities
    """
    claims = _require_oauth_scope("sentinelx:capabilities")

    start = _now_ms()
    data = await _sentinelx_get("/capabilities")

    data["_meta"] = {
        "proxy": SERVER_NAME,
        "ms": _now_ms() - start,
        "subject": claims.get("sub"),
        "preferred_username": claims.get("preferred_username"),
    }

    return data


@mcp.tool()
async def sentinel_help() -> Dict[str, Any]:
    """
    Get the help section exposed by SentinelX capabilities.
    Currently sourced from SentinelX /capabilities -> help.
    Requires scope: sentinelx:capabilities
    """
    claims = _require_oauth_scope("sentinelx:capabilities")

    start = _now_ms()
    data = await _sentinelx_get("/capabilities")
    help_data = data.get("help", {})

    return {
        "help": help_data,
        "_meta": {
            "proxy": SERVER_NAME,
            "ms": _now_ms() - start,
            "subject": claims.get("sub"),
            "preferred_username": claims.get("preferred_username"),
        },
    }


if __name__ == "__main__":
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class OAuthDiscoveryMiddleware(BaseHTTPMiddleware):
        """
        Return 401 with WWW-Authenticate on GET /mcp without Authorization.

        Claude and other MCP clients do a GET /mcp to detect whether OAuth
        is required. Without a session ID FastMCP returns 400 (Bad Request),
        which clients interpret as "server is down". This middleware intercepts
        that specific case and returns 401 + WWW-Authenticate so the client
        knows to start the OAuth flow.
        """
        async def dispatch(self, request, call_next):
            if (
                request.method == "GET"
                and request.url.path.rstrip("/") == "/mcp"
                and not request.headers.get("authorization")
                and not request.headers.get("mcp-session-id")
            ):
                return JSONResponse(
                    status_code=401,
                    content={"error": "unauthorized", "message": "Bearer token required"},
                    headers={
                        "WWW-Authenticate": (
                            f'Bearer resource_metadata="{RESOURCE_URL}/.well-known/oauth-protected-resource"'
                        )
                    },
                )
            return await call_next(request)

    app = mcp.http_app(transport="streamable-http")
    app.add_middleware(OAuthDiscoveryMiddleware)
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT)

