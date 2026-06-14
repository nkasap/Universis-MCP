"""Tests for the schema-blueprint enrichment (Phase 1 + per-tool detail)."""
import json
import pytest
import universis_mcp_server as srv

ACADEMIC_PERIOD = {
    "required": ["id"],
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "startDate": {"type": "string", "format": "date", "nullable": True},
        "locale": {"$ref": "#/components/schemas/AcademicPeriodLocale"},
        "locales": {"type": "array", "items": {"$ref": "#/components/schemas/AcademicPeriodLocale"}},
        "dateModified": {"type": "string"},
        "createdBy": {"type": "integer"},
    },
}


@pytest.fixture(autouse=True)
def _schemas(monkeypatch):
    monkeypatch.setattr(srv, "SCHEMAS", {"AcademicPeriod": ACADEMIC_PERIOD})
    monkeypatch.setattr(srv, "TOOL_SCHEMA_DETAIL", "compact")
    monkeypatch.setattr(srv, "TOOL_SCHEMA_MAX_FIELDS", 25)
    monkeypatch.setattr(srv, "SCHEMA_RESOURCES", False)


def test_classify_fields_splits_scalar_assoc_audit():
    scalars, assocs, audit = srv.classify_fields("AcademicPeriod")
    sc = dict(scalars)
    assert sc["id"] == "integer"
    assert sc["startDate"] == "string/date?"        # format + nullable marker
    assert "name" in sc
    assert dict(assocs)["locale"] == "AcademicPeriodLocale"
    assert dict(assocs)["locales"] == "[]AcademicPeriodLocale"
    assert {n for n, _ in audit} == {"dateModified", "createdBy"}


def test_blueprint_compact_lists_fields_and_associations():
    bp = srv.build_blueprint("AcademicPeriod", "compact")
    assert "Entity AcademicPeriod" in bp
    assert "name (string)" in bp and "startDate (string/date?)" in bp
    assert "Associations" in bp and "locale" in bp
    # audit fields are NOT in compact, and (resources off) no schema:// link
    assert "dateModified" not in bp
    assert "schema://" not in bp


def test_blueprint_full_includes_audit():
    bp = srv.build_blueprint("AcademicPeriod", "full")
    assert "Read-only/audit:" in bp and "dateModified" in bp


def test_blueprint_off_is_empty():
    assert srv.build_blueprint("AcademicPeriod", "off") == ""
    assert srv.build_blueprint("Unknown", "compact") == ""


def test_blueprint_truncation_gated_resource_link(monkeypatch):
    # Many scalar fields + small cap -> "…and N more", and the schema:// link
    # appears only when SCHEMA_RESOURCES is on.
    big = {"required": [], "properties": {f"f{i}": {"type": "string"} for i in range(30)}}
    monkeypatch.setattr(srv, "SCHEMAS", {"Big": big})
    monkeypatch.setattr(srv, "TOOL_SCHEMA_MAX_FIELDS", 5)

    monkeypatch.setattr(srv, "SCHEMA_RESOURCES", False)
    bp = srv.build_blueprint("Big", "compact")
    assert "…and 25 more" in bp and "schema://Big" not in bp

    monkeypatch.setattr(srv, "SCHEMA_RESOURCES", True)
    bp = srv.build_blueprint("Big", "compact")
    assert "schema://Big" in bp


def test_resolve_entity_by_tag_and_path():
    class R:
        def __init__(self, tags, path):
            self.tags, self.path = tags, path
    assert srv.resolve_entity(R(["AcademicPeriod"], "/api/AcademicPeriods/")) == "AcademicPeriod"
    # tag miss -> singularize path segment ("AcademicPeriods" -> "AcademicPeriod")
    assert srv.resolve_entity(R([], "/api/AcademicPeriods/{id}/")) == "AcademicPeriod"
    assert srv.resolve_entity(R([], "/api/Nope/")) is None


def test_slim_schema_shape():
    s = srv.slim_schema("AcademicPeriod")
    assert s["entity"] == "AcademicPeriod"
    assert s["required"] == ["id"]
    assert any(f["name"] == "name" for f in s["fields"])
    assert any(a["name"] == "locales" for a in s["associations"])
    json.dumps(s)  # must be JSON-serialisable


def test_per_tool_schema_detail_parsing_and_lookup():
    maps = srv._parse_route_maps(
        [
            {"methods": ["GET"], "pattern": r"^/api/Students/$", "mcp_type": "TOOL",
             "schema_detail": "full"},
            {"methods": ["GET"], "pattern": r"^/api/AcademicYears/$", "mcp_type": "TOOL",
             "schema_detail": "off"},
            {"methods": "*", "pattern": ".*", "mcp_type": "EXCLUDE"},
        ],
        "test",
    )
    # detail_for walks the ordered maps (set as module state) and returns per-rule detail
    import universis_mcp_server as s
    s._ROUTE_MAPS_ORDERED = maps
    assert s.detail_for("GET", "/api/Students/") == "full"
    assert s.detail_for("GET", "/api/AcademicYears/") == "off"
    # unmatched-by-detail (EXCLUDE rule has no schema_detail) -> module default
    s.TOOL_SCHEMA_DETAIL = "compact"
    assert s.detail_for("GET", "/api/Courses/") == "compact"


def test_invalid_schema_detail_raises():
    with pytest.raises(ValueError):
        srv._parse_route_maps(
            [{"methods": ["GET"], "pattern": ".*", "schema_detail": "verbose"}], "test")
