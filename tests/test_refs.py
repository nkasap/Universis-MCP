"""Tests for repair_dangling_refs (v0.3.0).

The spec pipeline no longer strips all $refs (clean_refs); instead it keeps valid
refs for FastMCP to resolve and only neutralises unresolvable ones. These tests
pin that behaviour:
  - a $ref to a DEFINED schema is left untouched
  - a $ref to an UNDEFINED schema is replaced with a generic object placeholder
"""
import json
import universis_mcp_server as srv


def _spec(with_dangling: bool):
    ghost = {"$ref": "#/components/schemas/Ghost"} if with_dangling else {"type": "string"}
    return {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/api/Things/": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                # valid ref to a DEFINED schema
                                "schema": {"$ref": "#/components/schemas/Thing"}
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "Thing": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "ghost": ghost,
                    },
                }
            }
        },
    }


def test_valid_ref_is_preserved():
    spec = _spec(with_dangling=False)
    srv.repair_dangling_refs(spec)
    body = spec["paths"]["/api/Things/"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    # the ref to the DEFINED 'Thing' schema must survive for FastMCP to resolve
    assert body == {"$ref": "#/components/schemas/Thing"}


def test_dangling_ref_is_replaced_with_object():
    spec = _spec(with_dangling=True)
    srv.repair_dangling_refs(spec)
    ghost = spec["components"]["schemas"]["Thing"]["properties"]["ghost"]
    assert "$ref" not in ghost
    assert ghost.get("type") == "object"
    assert "Ghost" in ghost.get("description", "")
    # the sibling valid ref is still intact
    body = spec["paths"]["/api/Things/"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body == {"$ref": "#/components/schemas/Thing"}


def test_repair_is_noop_when_all_refs_resolve():
    spec = _spec(with_dangling=False)
    before = json.dumps(spec, sort_keys=True)
    srv.repair_dangling_refs(spec)
    assert json.dumps(spec, sort_keys=True) == before


def test_clean_refs_still_available_for_response_cleaning():
    # clean_refs is intentionally retained (used on HTTP responses), just no longer
    # applied to the spec.
    assert hasattr(srv, "clean_refs")
    assert srv.clean_refs({"$ref": "x", "keep": 1}) == {"keep": 1}
