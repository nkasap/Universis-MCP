"""Regression tests for the bug fixes shipped in v0.2.2.

Covers:
  #1  TokenManager.get_token raises instead of returning None
  #2  CleaningAsyncClient._inject_token bearer-mode fallback no longer crashes
  #5  sanitize_openapi_for_fastmcp infers boolean for 'recursiveDelete' & co.
  #6  pluralize handles the 'ff' ending before the single-'f' ending

Run with:  uv run --with fastmcp --with httpx --with authlib \
               --with python-dotenv --with pyyaml --with pytest pytest -q
"""
import asyncio

import httpx
import pytest

import universis_mcp_server as srv


def run(coro):
    """Execute a coroutine to completion without requiring pytest-asyncio."""
    return asyncio.run(coro)


# ───────────────────────────────────────────────────────────────────────────
# #6 pluralize: the 'ff' ending must be handled before the single-'f' ending
# ───────────────────────────────────────────────────────────────────────────
def test_pluralize_double_f_is_not_swallowed():
    # Regression: previously 'staff' -> 'stafves' because endswith('f') matched
    # before the endswith('ff') branch.
    assert srv.pluralize("staff") == "staffs"


def test_pluralize_single_f_still_becomes_ves():
    assert srv.pluralize("leaf") == "leaves"


@pytest.mark.parametrize(
    "singular,plural",
    [
        ("course", "courses"),
        ("entry", "entries"),
        ("class", "classes"),
        ("index", "indices"),  # irregular plural defined in the module
    ],
)
def test_pluralize_common_cases(singular, plural):
    assert srv.pluralize(singular) == plural


# ───────────────────────────────────────────────────────────────────────────
# #5 sanitize_openapi_for_fastmcp: boolean inference for known flag params
# ───────────────────────────────────────────────────────────────────────────
def _spec_with_query_param(param_name: str) -> dict:
    """Minimal OpenAPI 3 spec with one GET operation carrying a schemaless query param."""
    return {
        "openapi": "3.0.1",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/api/Things/{id}/": {
                "get": {
                    "parameters": [
                        # No "schema" and no "in" -> sanitizer must fill them in.
                        {"name": param_name},
                    ],
                }
            }
        },
    }


def _param_schema(spec: dict, name: str) -> dict:
    params = spec["paths"]["/api/Things/{id}/"]["get"]["parameters"]
    for p in params:
        if p.get("name") == name:
            return p.get("schema", {})
    raise AssertionError(f"param {name!r} not found")


def test_recursive_delete_infers_boolean():
    # Regression: the set previously contained camelCase "recursiveDelete" but was
    # compared against the lowercased name, so it never matched.
    spec = srv.sanitize_openapi_for_fastmcp(_spec_with_query_param("recursiveDelete"))
    assert _param_schema(spec, "recursiveDelete") == {"type": "boolean"}


@pytest.mark.parametrize(
    "param_name",
    ["directOnly", "onlyDirectSupervision", "directSupervisingRolesOnly"],
)
def test_other_boolean_flags_infer_boolean(param_name):
    spec = srv.sanitize_openapi_for_fastmcp(_spec_with_query_param(param_name))
    assert _param_schema(spec, param_name) == {"type": "boolean"}


def test_uuid_and_date_formats_still_inferred():
    spec = srv.sanitize_openapi_for_fastmcp(_spec_with_query_param("studentUuid"))
    assert _param_schema(spec, "studentUuid") == {"type": "string", "format": "uuid"}

    spec = srv.sanitize_openapi_for_fastmcp(_spec_with_query_param("registrationDate"))
    assert _param_schema(spec, "registrationDate") == {"type": "string", "format": "date"}


def test_plain_query_param_defaults_to_string():
    spec = srv.sanitize_openapi_for_fastmcp(_spec_with_query_param("name"))
    assert _param_schema(spec, "name") == {"type": "string"}


# ───────────────────────────────────────────────────────────────────────────
# #1 TokenManager.get_token must raise (not return None) when no token is issued
# ───────────────────────────────────────────────────────────────────────────
class _FakeOAuthClient:
    """Stand-in for OAuthClient that returns a preset token container."""

    def __init__(self, token):
        self.token = token

    async def ensure_token(self):
        return None


def test_get_token_raises_when_no_access_token():
    mgr = srv.TokenManager(
        _FakeOAuthClient({"error": "invalid_client", "error_description": "bad creds"}),
        token_url="https://issuer.example/token",
    )
    with pytest.raises(RuntimeError) as exc:
        run(mgr.get_token())
    # The provider error should be surfaced in the message.
    assert "invalid_client" in str(exc.value)


def test_get_token_returns_access_token_when_present():
    mgr = srv.TokenManager(
        _FakeOAuthClient({"access_token": "abc123", "scope": "registrar"}),
        token_url="https://issuer.example/token",
    )
    assert run(mgr.get_token()) == "abc123"


# ───────────────────────────────────────────────────────────────────────────
# #2 CleaningAsyncClient._inject_token bearer-mode fallback must not crash
# ───────────────────────────────────────────────────────────────────────────
def _make_request() -> httpx.Request:
    return httpx.Request("GET", "https://api.example/api/Students/")


def test_bearer_mode_without_header_and_no_supplier_raises(monkeypatch):
    # Regression: this path used to call self._token_supplier() with a None
    # supplier (bearer mode never builds one) -> TypeError. It must raise a
    # clear RuntimeError instead.
    monkeypatch.setattr(srv, "AUTH_MODE", "bearer")
    monkeypatch.setattr(srv, "get_http_headers", lambda include=None: {})

    client = srv.CleaningAsyncClient(token_supplier=None, base_url="https://api.example")
    try:
        with pytest.raises(RuntimeError) as exc:
            run(client._inject_token(_make_request()))
        assert "bearer" in str(exc.value).lower()
    finally:
        run(client.aclose())


def test_bearer_passthrough_sets_authorization(monkeypatch):
    monkeypatch.setattr(srv, "AUTH_MODE", "bearer")
    monkeypatch.setattr(
        srv, "get_http_headers", lambda include=None: {"authorization": "Bearer xyz"}
    )

    client = srv.CleaningAsyncClient(token_supplier=None, base_url="https://api.example")
    try:
        req = _make_request()
        run(client._inject_token(req))
        assert req.headers["Authorization"] == "Bearer xyz"
    finally:
        run(client.aclose())


def test_oauth_mode_without_supplier_raises(monkeypatch):
    monkeypatch.setattr(srv, "AUTH_MODE", "oauth2")

    client = srv.CleaningAsyncClient(token_supplier=None, base_url="https://api.example")
    try:
        with pytest.raises(RuntimeError):
            run(client._inject_token(_make_request()))
    finally:
        run(client.aclose())


def test_oauth_mode_injects_supplied_token(monkeypatch):
    monkeypatch.setattr(srv, "AUTH_MODE", "oauth2")

    async def supplier():
        return "tok-42"

    client = srv.CleaningAsyncClient(token_supplier=supplier, base_url="https://api.example")
    try:
        req = _make_request()
        run(client._inject_token(req))
        assert req.headers["Authorization"] == "Bearer tok-42"
    finally:
        run(client.aclose())
