# Changelog

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
