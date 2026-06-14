# Changelog

## v0.3.0

### Changed
- **Stop stripping `$ref`s from the OpenAPI spec.** Previous versions ran
  `clean_refs` over the spec, deleting every `$ref`/`$defs` to avoid the hard
  build failures that FastMCP <3 raised on the first unresolvable reference. That
  also erased every request-body schema, so POST tools were created with empty
  inputs. FastMCP 3.x resolves valid refs natively and tolerates bad ones, so the
  spec pipeline now keeps valid refs — restoring request-body field schemas on
  write endpoints (e.g. `POST_*_WithId` now exposes the entity's typed fields
  instead of nothing).

- **Disable output validation (`validate_output=False`).** Keeping response
  schemas means FastMCP would otherwise validate API responses against them, and
  the Universis spec declares many fields as `string` without `nullable: true`
  while the API legitimately returns `null` (e.g. `GET_Students` failed with
  "None is not of type 'string'"). Structured-output *typing* is still available
  to clients; it is simply no longer *enforced*. Request-body schemas are
  unaffected.

### Added
- **`repair_dangling_refs`** — surgically replaces only the spec's *unresolvable*
  internal `$ref`s (the Universis generator emits ~15 references to schemas it
  never defines, such as `Object`, `ContactPoint`, `GradeScaleValueLocale`) with a
  generic `object` placeholder, and logs the repaired set at startup. Valid refs
  are left intact. This avoids dropping or guessing whole endpoints while keeping
  the build robust across FastMCP versions.
- `tests/test_refs.py` — covers ref preservation, dangling-ref replacement, and the
  no-op case.
- **Docs: Universis/`@themost` write conventions** — documented that client-keyed
  inserts (e.g. `Course`) require `"$state": 1`, that some fields are immutable on
  update, and the safe partial-update pattern.

### Validation
- Verified end-to-end against the live Universis API (read + write) on a test
  department: `GET` reads, a `POST` insert/update on a previously "empty-body"
  endpoint (now carrying a populated request-body schema), and a `DELETE` all
  succeed through the MCP server.

### Notes
- `clean_refs` is retained and still applied to HTTP **responses** (unchanged); it
  is simply no longer applied to the **spec**.

## v0.2.2

### Fixed
- **Auth: `TokenManager.get_token` no longer returns `None`.** When the IdP
  issues no `access_token`, the method now raises a `RuntimeError` (surfacing the
  provider `error`/`error_description`) instead of silently sending a literal
  `Authorization: Bearer None` header downstream.
- **Auth: bearer-mode fallback no longer crashes.** In `AUTH_MODE='bearer'` with
  no inbound `Authorization` header and no OAuth2 token supplier configured,
  `CleaningAsyncClient._inject_token` previously called a `None` supplier and
  raised `TypeError`. It now raises a clear, actionable `RuntimeError`. The same
  guard was added to the OAuth2 path.
- **OpenAPI sanitizer: boolean flag inference.** `recursiveDelete` was listed in
  camelCase but compared against the lowercased parameter name, so it never
  matched and the param was typed as `string`. The lookup set is now lowercase,
  so `recursiveDelete`, `directOnly`, `onlyDirectSupervision`, and
  `directSupervisingRolesOnly` correctly infer `{"type": "boolean"}`.
- **Tool-name pluralizer: `ff` ending.** `pluralize` checked `endswith('f')`
  before `endswith('ff')`, turning `staff` into `stafves`. The `ff` case is now
  handled first (`staff` → `staffs`).

### Changed
- **README: corrected the student filter example.** The `Student` entity has no
  `departmentId` attribute (that filter returns `500 ERR_ATTR_UNKNOWN`); the
  correct field is `department` (a numeric id), verified against the live API.
- **`route_maps.yaml` reconciled with the README examples.** Added read-only GET
  item-by-id rules (e.g. `/api/Institutes/{id}/`) so the documented
  `GET_*_WithId` tools actually resolve, and made the catch-all `EXCLUDE` rule
  explicit and prominently documented. Write methods remain excluded by default.
- **Security: removed token-prefix logging.** The bearer passthrough path no
  longer logs the first characters of the token at DEBUG.
- Removed the unused `from http import client` import.
- Added `__version__` and a startup version log line.

### Added
- `tests/test_fixes.py` — regression tests for all of the above.
- `CHANGELOG.md`.

## v0.2.0
- Rename to `universis_mcp_server.py`, add `AUTH_MODE` passthrough support,
  operationId injection, and documentation.
