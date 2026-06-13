import os
import asyncio
import logging
import httpx
import json
import yaml
import base64
import re
import time
from typing import Any, List, Iterable, Tuple, Dict
from dotenv import load_dotenv

import fastmcp
from fastmcp import FastMCP
# ⬇️ Updated import path for fastmcp 3.0
from fastmcp.server.providers.openapi import (
    RouteMap, MCPType, OpenAPITool, OpenAPIResource, OpenAPIResourceTemplate
)
from fastmcp.server.dependencies import get_http_headers
from authlib.integrations.httpx_client import AsyncOAuth2Client

__version__ = "0.2.2"

# ───────────────────────────────────────────────────────────────────────────────
# Environment & logging
# ───────────────────────────────────────────────────────────────────────────────
load_dotenv()

BASE_URL   = os.getenv('BASE_URL')
SCHEMA_PATH= os.getenv('SCHEMA_PATH')
SERVERNAME = os.getenv('SERVERNAME', 'Universis-API-Server')

ISSUER     = os.getenv('ISSUER')
TOKEN_PATH = os.getenv('TOKEN_PATH')
TOKEN_URL  = f"{ISSUER}{TOKEN_PATH}"

CLIENT_ID     = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')

# SCOPES may be space or comma separated; we'll normalize later
raw_scope = os.getenv('SCOPES', '')
SCOPES    = [s.strip() for s in re.split(r'[,\s]+', raw_scope) if s.strip()]

AUTH_MODE=os.getenv('AUTH_MODE', 'oauth2').lower().strip()  # 'oauth2' | 'bearer' — passthrough bearer from MCP transport

raw_required_scope = os.getenv('REQUIRED_SCOPES', '')
REQUIRED_SCOPES    = set([s.strip() for s in re.split(r'[,\s]+', raw_required_scope) if s.strip()])


TOKEN_AUTH_METHOD   = os.getenv('TOKEN_AUTH_METHOD', "client_secret_post")  # e.g., client_secret_post | client_secret_basic
FETCH_TOKEN_METHOD  = os.getenv('FETCH_TOKEN_METHOD', "POST")               # 'GET' or 'POST'
AUTH_PATH           = os.getenv('AUTH_PATH', 'NONE')                        # NONE for non-interactive client_credentials
GRANT_TYPE          = os.getenv('GRANT_TYPE', "client_credentials")

AUTH_ENDPOINT       = f"{ISSUER}{AUTH_PATH}" if AUTH_PATH and AUTH_PATH.upper() != "NONE" else None
TEMPLATE            = os.getenv('TEMPLATE')

TRANSPORT_TYPE = os.getenv('MCP_TRANSPORT', 'local').lower()  # 'local' or 'http'
MCP_HOST = os.getenv('MCP_HOST', '127.0.0.1')
MCP_PORT = int(os.getenv('MCP_PORT', '9000'))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
numeric_level = getattr(logging, LOG_LEVEL, logging.INFO)
logging.basicConfig(level=numeric_level)
logger = logging.getLogger("Universis-API-Server")
logger.info(f"✅ Universis MCP Server v{__version__} — logging level set to {LOG_LEVEL}")

# Silence noisy docket logs
for name in ("docket", "docket.worker", "docket.scheduler"):
    lg = logging.getLogger(name)
    lg.setLevel(logging.WARNING)
    lg.propagate = False

# Optional: keep httpx quieter unless debugging HTTP wire
logging.getLogger("httpx").setLevel(logging.WARNING)
# Keep authlib at INFO (token operations); change to WARNING if too chatty
logging.getLogger("authlib").setLevel(logging.INFO)

# Additional auth troubleshooting env flags
AUTH_DEBUG_FETCH_USERINFO = os.getenv("AUTH_DEBUG_FETCH_USERINFO", "false").lower() in {"1", "true", "yes"}
TOKENINFO_PATH = os.getenv("TOKENINFO_PATH")  # e.g., "/tokeninfo"
USERINFO_PATH  = os.getenv("USERINFO_PATH")   # e.g., "/userinfo" or "/protocol/openid-connect/userinfo"
INTROSPECT_PATH = os.getenv("INTROSPECT_PATH") 

# Audience/Resource (optional) for client-credentials targeting
AUDIENCE = os.getenv("AUDIENCE")
RESOURCE = os.getenv("RESOURCE")

# ───────────────────────────────────────────────────────────────────────────────
# Helpers: cleaning $refs/$defs from JSON
# ───────────────────────────────────────────────────────────────────────────────
def clean_refs(obj: Any) -> Any:
    """Recursively remove $ref and $defs from dict or list."""
    if isinstance(obj, dict):
        return {k: clean_refs(v) for k, v in obj.items() if k not in ("$ref", "$defs")}
    elif isinstance(obj, list):
        return [clean_refs(item) for item in obj]
    return obj

# ───────────────────────────────────────────────────────────────────────────────
# Route map loaders and utilities
# ───────────────────────────────────────────────────────────────────────────────
def _to_mcp_type(s: str) -> MCPType:
    s = (s or "").upper().strip()
    try:
        return MCPType[s]
    except KeyError:
        raise ValueError(f"Invalid mcp_type '{s}'. Allowed: TOOL, RESOURCE, RESOURCE_TEMPLATE, EXCLUDE")

def _normalize_methods(v: Any) -> Any:
    """
    Accepts '*', a single string, or a list of strings.
    Normalizes to '*' or a list of UPPERCASE HTTP methods.
    """
    if v in (None, "*"):
        return "*"
    if isinstance(v, str):
        return [v.upper().strip()]
    if isinstance(v, list):
        return [str(m).upper().strip() for m in v]
    raise ValueError(f"Invalid methods value: {v!r}")

def _ensure_str_list(value: Any, field_name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValueError(f"{field_name} must be list[str]")
    return [t for t in value]

def _parse_route_maps(data: Any, origin: str) -> List[RouteMap]:
    """Convert a parsed JSON/YAML structure into RouteMap objects."""
    if not isinstance(data, list):
        raise ValueError(f"{origin} must be a list of map objects")
    maps: List[RouteMap] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"{origin}[{i}] must be an object")
        methods = _normalize_methods(item.get("methods", "*"))
        pattern = item.get("pattern", ".*")
        if not isinstance(pattern, str):
            raise ValueError(f"{origin}[{i}].pattern must be a string regex")
        mcp_type = _to_mcp_type(item.get("mcp_type", "TOOL"))
        tags = set(_ensure_str_list(item.get("tags"), f"{origin}[{i}].tags"))
        mcp_tags = set(_ensure_str_list(item.get("mcp_tags"), f"{origin}[{i}].mcp_tags"))
        maps.append(
            RouteMap(
                methods=methods,
                pattern=pattern,
                mcp_type=mcp_type,
                tags=tags,
                mcp_tags=mcp_tags,
            )
        )
    logger.debug("Parsed %d RouteMaps from %s", len(maps), origin)
    return maps

def load_route_maps_from_env(env_var: str = "FASTMCP_ROUTE_MAPS") -> List[RouteMap]:
    """Read route maps from an env var containing JSON or YAML."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        logger.debug("Env %s empty; no env-based route maps maps loaded", env_var)
        return []
    # Try JSON → YAML fallback
    try:
        data = json.loads(raw)
        origin = f"env:{env_var}(json)"
    except Exception:
        try:
            data = yaml.safe_load(raw)
            origin = f"env:{env_var}(yaml)"
        except Exception as e:
            raise ValueError(f"Failed to parse {env_var} as JSON/YAML: {e}") from e
    return _parse_route_maps(data, origin)

def load_route_maps_from_file(env_var: str = "FASTMCP_ROUTE_MAPS_FILE") -> List[RouteMap]:
    """Read route maps from a file whose path is given in env (JSON or YAML file)."""
    path = os.getenv(env_var, "").strip()
    if not path:
        logger.debug("Env %s not set; no file-based route maps loaded", env_var)
        return []
    if not os.path.exists(path):
        raise FileNotFoundError(f"Route maps file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    # Try JSON → YAML fallback
    try:
        data = json.loads(raw)
        origin = f"file:{path}(json)"
    except Exception:
        try:
            data = yaml.safe_load(raw)
            origin = f"file:{path}(yaml)"
        except Exception as e:
            raise ValueError(f"Failed to parse route maps file {path} as JSON/YAML: {e}") from e
    return _parse_route_maps(data, origin)

def _route_map_key(m: RouteMap) -> Tuple[Any, str, str, Tuple[str, ...], Tuple[str, ...]]:
    """A stable key to detect duplicates: (methods-normalized, pattern, mcp_type, sorted-tags, sorted-mcp_tags)"""
    methods_norm = "*" if m.methods == "*" else tuple(sorted(m.methods))
    return (
        methods_norm,
        m.pattern,
        m.mcp_type.name,
        tuple(sorted(m.tags or [])),
        tuple(sorted(m.mcp_tags or [])),
    )

def merge_route_maps(
    file_maps: Iterable[RouteMap],
    env_maps: Iterable[RouteMap],
    *,
    env_overrides_file: bool = True,
    dedupe: bool = True,
) -> List[RouteMap]:
    """Merge route maps from file and env."""
    ordered: List[RouteMap] = []
    # Order sets precedence: earlier maps match first.
    if env_overrides_file:
        ordered.extend(file_maps or [])
        ordered.extend(env_maps or [])
    else:
        ordered.extend(env_maps or [])
        ordered.extend(file_maps or [])
    if not dedupe:
        return ordered
    seen = set()
    unique: List[RouteMap] = []
    for m in ordered:
        key = _route_map_key(m)
        if key in seen:
            continue
        seen.add(key)
        unique.append(m)
    logger.info("Merged %d route maps (%d unique after dedupe)", len(ordered), len(unique))
    return unique

def load_combined_route_maps(
    *,
    env_var: str = "FASTMCP_ROUTE_MAPS",
    file_var: str = "FASTMCP_ROUTE_MAPS_FILE",
    env_overrides_file: bool = True,
    dedupe: bool = True,
) -> List[RouteMap]:
    file_maps = load_route_maps_from_file(file_var)
    env_maps = load_route_maps_from_env(env_var)
    merged = merge_route_maps(
        file_maps=file_maps,
        env_maps=env_maps,
        env_overrides_file=env_overrides_file,
        dedupe=dedupe,
    )
    return merged

# ───────────────────────────────────────────────────────────────────────────────
# Redaction & JWT decode helpers for diagnostics
# ───────────────────────────────────────────────────────────────────────────────
SENSITIVE_KEYS = {
    "access_token", "refresh_token", "id_token", "client_secret",
    "password", "authorization", "token", "secret"
}

def _redact_value(k: str, v: Any) -> Any:
    if k and k.lower() in SENSITIVE_KEYS:
        return "<redacted>"
    return v

def _redact_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """Redact known sensitive fields in a shallow dict for safe logging."""
    if not isinstance(d, dict):
        return {"_error": f"not-a-dict: {type(d)}"}
    out = {}
    for k, v in d.items():
        out[k] = _redact_value(k, v)
    return out

def _pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except Exception:
        return str(obj)

def _safe_jwt_claims(token: str) -> Dict[str, Any]:
    """Decode JWT payload safely for debugging (no signature verification)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {"_error": "not-jwt"}
        pad = "=" * (-len(parts[1]) % 4)  # fix base64 padding
        payload = base64.urlsafe_b64decode(parts[1] + pad)
        claims = json.loads(payload)
        for k in ("exp", "iat", "nbf"):
            if k in claims:
                claims[k] = int(claims[k])
        return claims
    except Exception as e:
        return {"_error": str(e)}

# ───────────────────────────────────────────────────────────────────────────────
# Auth-only client for Client Credentials
# ───────────────────────────────────────────────────────────────────────────────
class OAuthClient(AsyncOAuth2Client):
    """Authlib AsyncOAuth2Client used only to obtain/refresh client-credentials tokens."""

    def __init__(
        self,
        token_url: str,
        grant_type: str | None = None,
        authorization_endpoint: str | None = None,
        scope: str | None = None,
        *,
        base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_endpoint_auth_method: str | None = "client_secret_post",
        fetch_token_method: str | None = "POST",
        timeout: float = 30.0,
    ):
        super().__init__(
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
            token_endpoint_auth_method=token_endpoint_auth_method,
            timeout=timeout,
        )
        self._token_url = token_url
        self._fetch_token_method = fetch_token_method
        self._authorization_endpoint = authorization_endpoint
        self._grant_type = grant_type
        # Normalize scope: list/tuple -> space-delimited string
        if isinstance(scope, (list, tuple)):
            self._scope = " ".join(s for s in scope if s)
        else:
            self._scope = scope
        self._token_lock = asyncio.Lock()

    async def ensure_token(self):
        """Fetch or refresh client-credentials token if missing/expired."""
        async with self._token_lock:
            if not getattr(self, "token", None) or self._is_expired(self.token):
                try:
                    if getattr(self, "_grant_type", None) == 'client_credentials':
                        params = {
                            "grant_type": self._grant_type,
                            "scope": self._scope,
                        }
                        if AUDIENCE:
                            params["audience"] = AUDIENCE
                        if RESOURCE:
                            params["resource"] = RESOURCE

                        resp = await self.fetch_token(
                            self._token_url,
                            method=self._fetch_token_method,
                            **params
                        )
                        # Ensure an access_token is present
                        if not resp or not resp.get("access_token"):
                            err  = resp.get("error") if isinstance(resp, dict) else None
                            desc = resp.get("error_description") if isinstance(resp, dict) else None
                            raise RuntimeError(f"No access_token in response. error={err}, description={desc}")
                    else:
                        # Interactive grants not implemented in this server
                        raise NotImplementedError("Only client_credentials is implemented in this server.")
                except Exception as e:
                    logger.error(f":x: OAuth token fetch failed: {e}")
                    raise

    @staticmethod
    def _is_expired(token: dict) -> bool:
        """
        Determine if token is expired, robustly handling seconds vs milliseconds.
        """
        now = time.time()

        expires_at = token.get("expires_at")
        if expires_at is not None:
            # Convert ms to seconds if it looks like a millisecond timestamp
            if isinstance(expires_at, (int, float)) and expires_at > 1e12:
                expires_at = expires_at / 1000.0
            # Refresh 30s before expiry to avoid mid-request token death
            try:
                return float(expires_at) <= (now + 30.0)
            except Exception:
                return False

        expires_in = token.get("expires_in")
        if isinstance(expires_in, (int, float)):
            # Convert ms to seconds if it looks too large
            if expires_in > 1e7:  # ~115 days in seconds; improbable for expires_in
                expires_in = expires_in / 1000.0
            try:
                # If we only have expires_in, treat <= 5s remaining as expired
                return float(expires_in) <= 5.0
            except Exception:
                return False

        return False

# ───────────────────────────────────────────────────────────────────────────────
# Troubleshooting helper: fetch user/token info from IdP
# ───────────────────────────────────────────────────────────────────────────────

async def fetch_and_log_userinfo(token: str, issuer_base: str) -> Dict[str, Any]:
    """
    Troubleshooting helper for service-account tokens:
      1) If TOKENINFO_PATH is configured, try GET {issuer_base}{TOKENINFO_PATH} (rare, legacy)
      2) If USERINFO_PATH configured AND 'openid' in scope, try GET {issuer_base}{USERINFO_PATH}
      3) If INTROSPECT_PATH configured, try POST {issuer_base}{INTROSPECT_PATH} with client auth
    Returns {} on failure. All logs redact sensitive fields.
    """
    if not AUTH_DEBUG_FETCH_USERINFO:
        return {}

    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(connect=10.0, read=10.0, write=10.0, pool=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        # 1) TOKENINFO (if present)
        if TOKENINFO_PATH:
            url = issuer_base.rstrip("/") + TOKENINFO_PATH
            try:
                r = await client.get(url, headers=headers)
                ct = (r.headers.get("content-type") or "").lower()
                if r.status_code == 200 and "json" in ct:
                    data = r.json()
                    logger.debug("[AUTH] Token info (%s): %s", url, _pretty(_redact_dict(data)))
                    _log_identity_summary(data)
                    return data
                else:
                    logger.debug("[AUTH] Tokeninfo fallback: %s -> %s %s", url, r.status_code, ct)
            except httpx.HTTPError as e:
                logger.debug("[AUTH] Tokeninfo request failed for %s: %s", url, e)

        # 2) USERINFO (only when 'openid' was actually requested; usually not for client_credentials)
        if USERINFO_PATH:
            # Peek at scopes to decide if we should try userinfo
            # We infer scope from the token container in TokenManager before calling us,
            # so just attempt and let 403 guide us; but Keycloak requires 'openid' for userinfo.
            url = issuer_base.rstrip("/") + USERINFO_PATH
            try:
                r = await client.get(url, headers=headers)
                ct = (r.headers.get("content-type") or "").lower()
                if r.status_code == 200 and "json" in ct:
                    data = r.json()
                    logger.debug("[AUTH] User info (%s): %s", url, _pretty(_redact_dict(data)))
                    _log_identity_summary(data)
                    return data
                else:
                    logger.debug("[AUTH] Userinfo fallback: %s -> %s %s", url, r.status_code, ct)
            except httpx.HTTPError as e:
                logger.debug("[AUTH] Userinfo request failed for %s: %s", url, e)

        # 3) INTROSPECTION (Keycloak-friendly for client_credentials)
        if INTROSPECT_PATH:
            url = issuer_base.rstrip("/") + INTROSPECT_PATH
            try:
                # RFC 7662: POST, x-www-form-urlencoded: token=<access_token>
                # Client authenticates using Basic (preferred) or form params.
                auth = (CLIENT_ID or "", CLIENT_SECRET or "")
                data = {"token": token}
                r = await client.post(url, auth=auth, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
                ct = (r.headers.get("content-type") or "").lower()
                if r.status_code == 200 and "json" in ct:
                    info = r.json()
                    logger.debug("[AUTH] Introspection (%s): %s", url, _pretty(_redact_dict(info)))
                    _log_identity_summary(info)
                    return info
                else:
                    logger.debug("[AUTH] Introspection fallback: %s -> %s %s", url, r.status_code, ct)
            except httpx.HTTPError as e:
                logger.debug("[AUTH] Introspection request failed for %s: %s", url, e)

    logger.debug("[AUTH] Userinfo/introspection fetch: all endpoints failed.")
    return {}


def _log_identity_summary(claims: Dict[str, Any]) -> None:
    """Log key identity/authorization hints from token info/introspection."""
    if not isinstance(claims, dict):
        return

    user = (
        claims.get("username")
        or claims.get("preferred_username")
        or claims.get("sub")
        or claims.get("user_id")
    )
    scope = claims.get("scope") or claims.get("scp")
    aud   = claims.get("aud")   or claims.get("resource") or claims.get("audience")

    # Keycloak-specific roles often appear here:
    realm_roles = None
    client_roles = None

    if "realm_access" in claims and isinstance(claims["realm_access"], dict):
        realm_roles = claims["realm_access"].get("roles")

    if "resource_access" in claims and isinstance(claims["resource_access"], dict):
        # Log the keys and first few roles for visibility
        client_roles = {
            k: v.get("roles")
            for k, v in claims["resource_access"].items()
            if isinstance(v, dict)
        }

    logger.debug(
        "[AUTH] Identity summary: user=%s scope=%s aud=%s realm_roles=%s client_roles=%s",
        user, scope, aud, realm_roles, client_roles)



# ───────────────────────────────────────────────────────────────────────────────
# TokenManager (purely auth)
# ───────────────────────────────────────────────────────────────────────────────

class TokenManager:
    def __init__(self, oauth_client: OAuthClient, token_url: str, scope: str | None = None):
        self.oauth_client = oauth_client
        self.token_url = token_url
        self.scope = scope

    async def get_token(self) -> str:
        # Ensure we have a valid token
        await self.oauth_client.ensure_token()

        # Token container returned by Authlib (dict-like)
        tok = getattr(self.oauth_client, "token", {}) or {}
        token = tok.get("access_token")

        # Log the token container (redacted)
        logger.debug("[AUTH] token container: %s", _pretty(_redact_dict(tok)))

        # Try JWT decode and log selected claims (opaque tokens will show {'_error': 'not-jwt'})
        claims = _safe_jwt_claims(token or "")
        logger.debug("[AUTH] JWT decode result: %s", claims)

        # --- Decide and log the effective user identity key used for mapping ---
        effective_user = (
            (isinstance(claims, dict) and (
                claims.get("preferred_username") or
                claims.get("username") or
                claims.get("sub")
            )) or None
        )
        logger.debug("[AUTH] Effective user key used for API mapping: %s", effective_user)

        # Optional: surface Keycloak roles if present
        if isinstance(claims, dict):
            realm_roles = claims.get("realm_access", {}).get("roles")
            resource_access = claims.get("resource_access")
            if realm_roles:
                logger.debug("[AUTH] JWT realm_roles=%s", realm_roles)
            if isinstance(resource_access, dict) and resource_access:
                short_ra = {k: v.get("roles") for k, v in resource_access.items() if isinstance(v, dict)}
                logger.debug("[AUTH] JWT resource_access=%s", short_ra)

        # Try common alt fields even for opaque tokens
        alt_scope = tok.get("scope") or (isinstance(claims, dict) and claims.get("scope")) or tok.get("scp") or (isinstance(claims, dict) and claims.get("scp"))
        alt_aud   = tok.get("aud") or (isinstance(claims, dict) and claims.get("aud")) or tok.get("resource") or tok.get("audience")
        alt_roles = ((isinstance(claims, dict) and (claims.get("roles") or claims.get("permissions") or claims.get("authorities"))) or
                     tok.get("roles") or tok.get("permissions"))
        logger.debug("[AUTH] aud=%s scope=%s roles=%s", alt_aud, alt_scope, alt_roles)

        # --- Fail-fast required scopes check ---
        granted_scopes = set((tok.get("scope") or "").split())
        missing_scopes = REQUIRED_SCOPES - granted_scopes
        if missing_scopes:
            logger.warning(
                "[AUTH] Missing required scopes: %s (granted: %s). "
                "Downstream API may return 403 (e.g., user mapping/claims missing).",
                " ".join(sorted(missing_scopes)),
                " ".join(sorted(granted_scopes)) or "<none>"
            )
            # If you prefer hard fail here, raise RuntimeError instead of warning.

        if token:
            # Optional: fetch user info / introspection for diagnostics (won't raise)
            try:
                await fetch_and_log_userinfo(token, ISSUER)
            except Exception as e:
                logger.debug("[AUTH] Userinfo/Introspection fetch threw: %s", e)
            return token

        # No token → surface provider errors (if any) and fail loudly.
        # Returning None here would propagate as a literal "Bearer None"
        # Authorization header downstream, producing a confusing 401.
        err  = tok.get("error")
        desc = tok.get("error_description")
        logger.error("Token fetch failed. error=%s, description=%s", err, desc)
        raise RuntimeError(
            f"Failed to obtain an access token from {self.token_url}. "
            f"error={err}, description={desc}"
        )

# ───────────────────────────────────────────────────────────────────────────────
# CleaningAsyncClient (no auth logic inside; uses request hook to inject token)
# ───────────────────────────────────────────────────────────────────────────────
class CleaningAsyncClient(httpx.AsyncClient):
    """
    httpx.AsyncClient that:
    - injects Authorization header via a provided async token supplier,
    - cleans '$refs'/'$defs' in JSON responses.
    """
    def __init__(self, token_supplier=None, *args, **kwargs):
        """
        token_supplier: async callable () -> str (returns bearer token)
        """
        self._token_supplier = token_supplier
        # Attach request hook for token injection
        hooks = kwargs.pop("event_hooks", {})
        req_hooks = hooks.get("request", [])
        req_hooks.append(self._inject_token)
        hooks["request"] = req_hooks
        super().__init__(event_hooks=hooks, *args, **kwargs)

    async def _inject_token(self, request: httpx.Request):
        # AUTH_MODE == "bearer" -- passthrough Authorization header from MCP transport
        if AUTH_MODE == "bearer":
            mcp_headers = get_http_headers(include={"authorization"})
            if "authorization" in mcp_headers:
                # Do not log any portion of the token, even at DEBUG.
                logger.debug("[AUTH] Bearer passthrough from MCP transport")
                request.headers["Authorization"] = mcp_headers["authorization"]
                return
            # No inbound Authorization header. We can only fall back to the
            # OAuth2 token supplier if one was actually configured; in pure
            # bearer mode it is None, so calling it would raise TypeError.
            if self._token_supplier is None:
                raise RuntimeError(
                    "AUTH_MODE='bearer' but the incoming MCP request carried no "
                    "Authorization header and no OAuth2 token supplier is configured. "
                    "Provide a Bearer token from the MCP client, or set AUTH_MODE='oauth2'."
                )
            logger.warning(
                "[AUTH] AUTH_MODE='bearer' but no Authorization header in MCP transport"
            )
            logger.info("[AUTH] Falling back to OAuth2 token supplier")

        if self._token_supplier is None:
            raise RuntimeError(
                "No OAuth2 token supplier configured and no Bearer token available "
                "to authenticate the upstream request."
            )
        token = await self._token_supplier()
        logger.debug("[AUTH] Injecting bearer token for %s", request.url)
        request.headers["Authorization"] = f"Bearer {token}"

    async def send(self, request: httpx.Request, *args, **kwargs) -> httpx.Response:
        # Delegate to base AsyncClient
        response = await super().send(request, *args, **kwargs)
        # Debug log (optional)
        if logger.isEnabledFor(logging.DEBUG):
            try:
                logger.debug(f"🔎 Raw API response from {response.request.url}:\n{response.text}")
            except Exception:
                pass
        # Clean JSON responses
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            try:
                data = response.json()
                cleaned = clean_refs(data)
                response._content = json.dumps(cleaned).encode("utf-8")
                response.headers["content-type"] = "application/json"
            except Exception:
                # If parsing fails, return original response unmodified
                return response
        return response

# ───────────────────────────────────────────────────────────────────────────────
# Templates & utilities
# ───────────────────────────────────────────────────────────────────────────────
lookup_templates = {}
if TEMPLATE and os.path.exists(TEMPLATE):
    try:
        with open(TEMPLATE, "r", encoding="utf-8") as f:
            lookup_templates = json.load(f)
        logger.info(f"✅ Loaded templates from {TEMPLATE}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to load template file {TEMPLATE}: {e}. Falling back to auto-generated names.")
else:
    logger.info("ℹ️ TEMPLATE not configured; using auto-generated tool names")

def split_pascal_case(name: str) -> str:
    return re.sub(r'(?<!^)(?=[A-Z])', ' ', name).strip()

_irregular_singulars = {
    'people': 'person', 'statuses': 'status', 'analyses': 'analysis',
    'indices': 'index', 'metrices': 'metric', 'axes': 'axis',
    'bases': 'base', 'cases': 'case', 'phases': 'phase',
}
_irregular_plurals = {v: k for k, v in _irregular_singulars.items()}

def singularize(word: str) -> str:
    word = word.strip()
    if not word:
        return word
    lw = word.lower()
    if lw in _irregular_singulars:
        return _irregular_singulars[lw]
    if word.endswith('ses'):
        return word[:-2]       # classes -> class
    if word.endswith('xes'):
        return word[:-2]       # indexes -> index
    if word.endswith('zes'):
        return word[:-2]       # quizzes -> quiz
    if word.endswith('ves'):
        return word[:-2] + 'f' # leaves -> leaf
    if word.endswith('ies'):
        return word[:-2] + 'y' # entries -> entry
    if word.endswith('es') and len(word) > 3:
        return word[:-2]       # statuses -> status (also handled above)
    if word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word

def pluralize(word: str) -> str:
    word = word.strip()
    if not word:
        return word
    lw = word.lower()
    if lw in _irregular_plurals:
        return _irregular_plurals[lw]
    if word.endswith('y') and not word.endswith('ay') and not word.endswith('ey') \
       and not word.endswith('iy') and not word.endswith('oy') and not word.endswith('uy'):
        return word[:-1] + 'ies'   # entry -> entries
    if word.endswith('sh') or word.endswith('ch') or word.endswith('ss') or word.endswith('x'):
        return word + 'es'         # class -> classes
    # Check the 'ff' case before the single-'f' case, otherwise 'f' matches
    # first and 'staff' becomes 'stafves'.
    if word.endswith('ff'):
        return word + 's'          # staff -> staffs
    if word.endswith('f'):
        return word[:-1] + 'ves'   # leaf -> leaves
    return word + 's'

def clean_segment(segment: str) -> str:
    if segment.lower() == 'createdby':
        return 'creator'
    if segment.lower() == 'modifiedby':
        return 'modifier'
    return split_pascal_case(segment)

def generate_summary(method: str, path: str, tags: list, summary: str) -> str:
    resource_raw = tags[0] if tags else path.split('/')[2]
    resource_words = split_pascal_case(resource_raw)
    entity_singular = singularize(resource_words)
    entity_plural = pluralize(resource_words)
    if summary in lookup_templates and method.upper() in lookup_templates[summary]:
        template = lookup_templates[summary][method.upper()]
        return template.format(entity_singular=entity_singular, entity_plural=entity_plural)
    trailing = path.split('/')[-1]
    if trailing not in ['{id}', '']:
        trailing_clean = clean_segment(trailing)
        return f"Retrieve {trailing_clean} for {entity_singular}"
    return summary

def customize_components(route: Any, component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate) -> None:
    action = route.operation_id or route.path.split("/")[-1] or ""
    rec_object = route.operation_id or route.path.split("/")[2] or ""
    clean_action = action.replace("{", "").replace("}", "").replace("/", "_")
    if route.operation_id:
        # Use operation_id directly (already includes method prefix like GET_Institutes)
        component.name = route.operation_id
        enriched_summary = generate_summary(route.method, route.path, route.tags, route.summary or "No description")
        component.description = f"{enriched_summary}"
    else:
        component.name = f"{route.method}_{rec_object}" + (f"_{clean_action}" if clean_action else "")
        enriched_summary = generate_summary(route.method, route.path, route.tags, route.summary or "No description")
        component.description = f"{enriched_summary}"
    if isinstance(component, OpenAPITool):
        param_details = []
        for p in getattr(route, "parameters", []) or []:
            # Handle ParameterInfo object in fastmcp 3.0 (has attributes instead of dict keys)
            param_name = p.name if hasattr(p, 'name') else p.get('name', 'unknown') if isinstance(p, dict) else 'unknown'
            param_schema = p.schema if hasattr(p, 'schema') else p.get('schema', {}) if isinstance(p, dict) else {}
            param_type = param_schema.get('type', 'unknown') if isinstance(param_schema, dict) else (getattr(param_schema, 'type', 'unknown') if param_schema else 'unknown')
            param_desc = p.description if hasattr(p, 'description') else p.get('description', 'No description') if isinstance(p, dict) else 'No description'
            param_details.append(f"- {param_name} ({param_type}): {param_desc}")
        request_body = getattr(route, "request_body", None)
        if request_body:
            param_details.append("\nRequest Body:")
            props = request_body.get("properties", {}) if isinstance(request_body, dict) else {}
            if not props:
                schema = (
                    request_body.get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                ) if isinstance(request_body, dict) else {}
                props = schema.get("properties", {}) if isinstance(schema, dict) else {}
            for k, v in props.items():
                param_details.append(f"- {k} ({v.get('type', 'unknown')}): {v.get('description', 'No description')}")
        if param_details:
            component.description += "\nParameters:\n" + "\n".join(param_details)

# ── 2) Sanitizer tuned to your schema ──────────────────────────────────────────
PATH_PARAM_RE = re.compile(r"\{([^}/]+)\}")

def sanitize_openapi_for_fastmcp(spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Minimal, standards-compliant fixes so FastMCP's parser (2.14+) can validate:
    • Remove empty `required: []` lists in components.schemas
    • Normalize Parameter Objects: add missing `in`, `schema`, `required` for path
    • Ensure all templated path params appear in operation.parameters
    NOTE: Does NOT touch `$ref` anywhere.
    """
    if not isinstance(spec, dict):
        return spec
    # 1) Components: remove empty required lists
    comps = spec.get("components", {})
    schemas = comps.get("schemas", {})
    if isinstance(schemas, dict):
        for _, schema in list(schemas.items()):
            if isinstance(schema, dict) and isinstance(schema.get("required"), list) and len(schema["required"]) == 0:
                schema.pop("required")
    # 2) Paths: normalize parameters per operation
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return spec
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        tmpl_params = set(PATH_PARAM_RE.findall(path))
        for method, op in path_item.items():
            if method.lower() not in {"get", "put", "post", "delete", "patch", "options", "head"}:
                continue
            if not isinstance(op, dict):
                continue
            params = op.get("parameters", [])
            if not isinstance(params, list):
                params = []
            op["parameters"] = params
            # Normalize Parameter Objects
            for p in params:
                if not isinstance(p, dict):
                    continue
                if "$ref" in p:
                    continue  # Reference Object — leave untouched
                name = p.get("name")
                if not name or not isinstance(name, str):
                    continue
                # infer `in`
                if "in" not in p:
                    p["in"] = "path" if name in tmpl_params else "query"
                # path params must be required
                if p["in"] == "path":
                    p["required"] = True
                # add a minimal schema
                if "schema" not in p or not isinstance(p["schema"], dict):
                    sch: Dict[str, Any] = {"type": "string"}
                    lname = name.lower()
                    if name.endswith("Uuid") or lname.endswith("uuid"):
                        sch["format"] = "uuid"
                    elif "date" in lname:
                        sch["format"] = "date"
                    elif lname in {"recursivedelete", "directsupervisingrolesonly", "directonly", "onlydirectsupervision"}:
                        # Compare against the lowercased name; entries here must
                        # be lowercase or they can never match (e.g. the old
                        # camelCase "recursiveDelete" was dead).
                        sch = {"type": "boolean"}
                    p["schema"] = sch
            # ensure all templated path params exist
            existing = {p.get("name") for p in params if isinstance(p, dict)}
            missing = [n for n in tmpl_params if n not in existing]
            for n in missing:
                params.append({"name": n, "in": "path", "required": True, "schema": {"type": "string"}})
    return spec
def normalize_null_strings(obj: Any, keys: Iterable[str] = ("summary", "description", "operationId", "title", "name", "example")) -> Any:
    """
    Recursively replace None for known string-valued keys with empty string.
    Returns the same object for chaining.
    """
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if v is None and k in keys:
                obj[k] = ""
            else:
                obj[k] = normalize_null_strings(v, keys)
        return obj
    if isinstance(obj, list):
        return [normalize_null_strings(i, keys) for i in obj]
    return obj

# ───────────────────────────────────────────────────────────────────────────────
# Fetch OpenAPI doc (kept as in your original; placed here for completeness)
# ───────────────────────────────────────────────────────────────────────────────
async def load_openapi_doc(base_url: str, path: str) -> dict:
    """Fetch OpenAPI doc as dict, supporting JSON or YAML, with diagnostics."""
    url = base_url.rstrip("/") + path
    timeout = httpx.Timeout(connect=30.0, read=30.0, write=30.0, pool=30.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url)
        logger.debug("GET %s -> %s %s", url, r.status_code, r.headers.get("content-type"))
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        text = r.text
        data = None
        if "json" in ctype:
            try:
                data = r.json()
            except Exception as e:
                logger.warning("JSON parse failed (%r); trying YAML safe_load", e)
        if data is None:
            data = yaml.safe_load(text)  # YAML also parses JSON
        if not isinstance(data, dict):
            raise ValueError(f"Doc is not a mapping/dict (got {type(data).__name__}).")
        ver = str(data.get("openapi", "")).strip()
        if not ver.startswith("3."):
            # springdoc also exposes YAML at /v3/api-docs.yaml if needed
            raise ValueError(f"Missing/invalid 'openapi' version (got {ver!r}), expected 3.x.y")
        logger.info("✅ Loaded OpenAPI %s successfully", ver)
        return data

# ───────────────────────────────────────────────────────────────────────────────
# operationId injection (needed when Universis spec has empty operationIds)
# ───────────────────────────────────────────────────────────────────────────────

def _pascal_to_mcp_id(path: str, method: str) -> str:
    """Build operation ID: GET /api/Institutes/{id}/ -> GET_Institutes_WithId

    Rules:
    - Remove /api prefix
    - Remove trailing slashes
    - Split on '/'
    - Static segments keep as-is (already PascalCase from Universis)
    - Path params {param} -> WithParam (capitalized)
    - Multiple path params -> GET_X_WithId_WithCourseId etc.
    """
    clean = path.strip('/')
    if clean.startswith('api/'):
        clean = clean[4:]
    segments = [s for s in clean.split('/') if s]

    static_parts = []
    param_parts = []
    for s in segments:
        m = PATH_PARAM_RE.match(s)
        if m:
            pname = m.group(1)
            param_parts.append(pname[0].upper() + pname[1:] if pname else '')
        else:
            static_parts.append(s)

    entity_part = '_'.join(static_parts) if static_parts else ''
    param_part = 'With' + '_With'.join(param_parts) if param_parts else ''

    parts = [method.upper(), entity_part]
    if param_part:
        parts.append(param_part)

    return '_'.join(filter(None, parts))


def inject_operation_ids(spec: dict) -> dict:
    """Inject operationId into every endpoint that doesn't have one."""
    paths = spec.get('paths', {})
    for path, methods in paths.items():
        for method, op in methods.items():
            if method not in ('get', 'put', 'post', 'delete', 'patch', 'options', 'head'):
                continue
            if isinstance(op, dict) and not op.get('operationId'):
                op['operationId'] = _pascal_to_mcp_id(path, method)
    return spec

# ───────────────────────────────────────────────────────────────────────────────
# Main entry
# ───────────────────────────────────────────────────────────────────────────────
async def main():
    openapi_spec = await load_openapi_doc(BASE_URL, SCHEMA_PATH)
    openapi_spec = inject_operation_ids(openapi_spec)  # ← Add BEFORE sanitize! 
    openapi_spec = sanitize_openapi_for_fastmcp(openapi_spec)
    openapi_spec = clean_refs(openapi_spec)  # ← Add this
    openapi_spec = normalize_null_strings(openapi_spec)  # ← Then this

    route_maps = load_combined_route_maps(
        env_var="FASTMCP_ROUTE_MAPS",
        file_var="FASTMCP_ROUTE_MAPS_FILE",
        env_overrides_file=True,  # env rules win by being later → first-match-wins
        dedupe=True,
    )

    # Normalize scopes into a space-delimited string for OAuth
    normalized_scope = " ".join(SCOPES) if SCOPES else None

    # Auth-only client to manage tokens (only for AUTH_MODE != "bearer")
    oauth_client = None
    token_manager = None
    if AUTH_MODE != "bearer":
        oauth_client = OAuthClient(
        token_url=TOKEN_URL,
        authorization_endpoint=AUTH_ENDPOINT,
        grant_type=GRANT_TYPE,
        scope=normalized_scope,
        base_url=BASE_URL,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        fetch_token_method=FETCH_TOKEN_METHOD,
        token_endpoint_auth_method=TOKEN_AUTH_METHOD,
        timeout=30.0,
    )
    if AUTH_MODE != "bearer":
        await oauth_client.ensure_token()  # prefetch for first call

        # Token manager: provides async token supplier
        token_manager = TokenManager(oauth_client, TOKEN_URL, normalized_scope)

    # Cleaning client used by FastMCP.from_openapi (no auth logic inside)
    token_supplier = token_manager.get_token if token_manager else None
    client = CleaningAsyncClient(
        token_supplier=token_supplier,  # async callable or None
        base_url=BASE_URL,
        timeout=30.0,
    )

    mcp = FastMCP.from_openapi(
        openapi_spec=openapi_spec,
        client=client,  # ← httpx.AsyncClient-compatible
        name=SERVERNAME,
        mcp_component_fn=customize_components,
        route_maps=route_maps,
    )

    try:
    # ✅ CONFIGURABLE TRANSPORT LOGIC (NEW)
        if TRANSPORT_TYPE == "http":
            logger.info(f"🚀 Starting MCP server in HTTP mode at {MCP_HOST}:{MCP_PORT}...")
            await mcp.run_async(transport="http", host=MCP_HOST, port=MCP_PORT)
        else:
            logger.info(f"🚀 Starting MCP server in LOCAL (stdio) mode...")
            await mcp.run_async()
    finally:
        await client.aclose()
        if oauth_client:
            await oauth_client.aclose()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 MCP server stopped gracefully")