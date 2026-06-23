# Universis MCP Server

> **Version:** 0.4.0

A FastMCP-based MCP server that wraps the Universis REST API (Ellogon/OData) into MCP tools and resources. Uses OAuth2 **client_credentials** flow to obtain tokens, or supports **bearer passthrough** from the MCP transport.

## Features

- **OperationId injection** — Automatically generates human-readable MCP tool names from OpenAPI paths (e.g., `GET_Institutes`, `GET_Students_WithId`)
- **Dual auth modes** — OAuth2 client_credentials (default) or bearer passthrough
- **Route maps** — Filter/rename/exclude endpoints via YAML/JSON route maps
- **Streamable HTTP transport** — Runs as an MCP server over HTTP (SSE)
- **OData support** — Full `$filter`, `$top`, `$skip`, `$orderby`, `$select`, `$expand` support
- **Auto-refresh** — OAuth2 tokens are automatically refreshed before expiry

## Prerequisites

- Python 3.11+
- uv (fast Python package manager)

## Quick Start

```bash
# Clone the repository
git clone https://github.com/nkasap/Universis-MCP.git
cd Universis-MCP

# Run with uv (auto-installs dependencies)
export AUTH_MODE="oauth2"
export MCP_TRANSPORT="http"
uv run --with httpx --with fastmcp --with python-dotenv --with authlib \
  python3 universis_mcp_server.py
```

## Configuration

All configuration is through environment variables:

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `BASE_URL` | Base URL of the Universis REST API | `https://api.example.com` |
| `SCHEMA_PATH` | Path to the OpenAPI schema | `/api-docs/schema` |
| `ISSUER` | OAuth2 issuer URL | `https://sso.example.com/realms/universis` |
| `TOKEN_PATH` | OAuth2 token endpoint path | `/protocol/openid-connect/token` |
| `CLIENT_ID` | OAuth2 client ID | (your client ID) |
| `CLIENT_SECRET` | OAuth2 client secret | (your client secret) |
| `SCOPES` | OAuth2 scopes (space or comma separated) | `registrar` |

### Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_MODE` | `oauth2` | `oauth2` = use client_credentials, `bearer` = passthrough Authorization header from MCP transport |
| `TOKEN_AUTH_METHOD` | `client_secret_post` | `client_secret_post` or `client_secret_basic` |
| `FETCH_TOKEN_METHOD` | `POST` | HTTP method for token endpoint |
| `GRANT_TYPE` | `client_credentials` | OAuth2 grant type |
| `AUDIENCE` | — | Optional audience parameter |
| `RESOURCE` | — | Optional resource parameter |
| `REQUIRED_SCOPES` | — | Scopes checked against token before API calls |

### Transport

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRANSPORT` | `local` | `local` = stdio, `http` = streamable HTTP server |
| `MCP_HOST` | `127.0.0.1` | HTTP server bind address |
| `MCP_PORT` | `9000` | HTTP server port |

### Route Maps

| Variable | Description |
|----------|-------------|
| `FASTMCP_ROUTE_MAPS` | Inline JSON/YAML array of route map objects |
| `FASTMCP_ROUTE_MAPS_FILE` | Path to a JSON/YAML file with route map objects |

### Templates

| Variable | Description |
|----------|-------------|
| `TEMPLATE` | Path to a JSON file mapping endpoint summaries to human-readable display names |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Debug

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_DEBUG_FETCH_USERINFO` | `false` | Fetch userinfo/introspection for debug logging |
| `USERINFO_PATH` | — | Path to OIDC userinfo endpoint |
| `INTROSPECT_PATH` | — | Path to OAuth2 token introspection endpoint |
| `TOKENINFO_PATH` | — | Path to legacy token info endpoint |

## Usage

### OAuth2 mode (default)

```bash
# Run as stdio (for MCP client that spawns subprocesses)
export AUTH_MODE="oauth2"
export CLIENT_ID="your-client-id"
export CLIENT_SECRET="your-client-secret"
uv run universis_mcp_server.py

# Run as streamable HTTP server
export MCP_TRANSPORT="http"
uv run universis_mcp_server.py
```

### Bearer passthrough mode

```bash
export AUTH_MODE="bearer"
export MCP_TRANSPORT="http"
uv run universis_mcp_server.py
```

Then connect with an MCP client that provides the Bearer token:

```python
import httpx, json

# Step 1: Initialize session
r = httpx.post("http://127.0.0.1:9000/mcp",
    headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    content=json.dumps({
        "jsonrpc": "2.0", "id": "1", "method": "initialize",
        "params": {"protocolVersion": "2026-04-05", "capabilities": {}, "clientInfo": {"name": "my-app", "version": "1.0"}}
    }),
)
session_id = r.headers.get("mcp-session-id")

# Step 2: List tools
r = httpx.post("http://127.0.0.1:9000/mcp",
    headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Session-Id": session_id},
    content=json.dumps({"jsonrpc":"2.0", "id":"2", "method":"tools/list", "params":{}}),
    # In bearer mode, add: auth=(client)
)
print(list(r.json()))  # List all tool names
```

### Using with an MCP Client (Hermes, Claude Code, etc.)

Add to your MCP server configuration:

```yaml
mcp_servers:
  universis-api:
    command: "uv"
    args:
      - run
      - "--with"
      - httpx
      - "--with"
      - fastmcp
      - "--with"
      - python-dotenv
      - "--with"
      - authlib
      - /path/to/universis_mcp_server.py
    env:
      BASE_URL: "https://api.example.com"
      SCHEMA_PATH: "/api-docs/schema"
      SERVERNAME: "Universis API"
      ISSUER: "https://sso.example.com/realms/universis"
      AUTH_PATH: "/protocol/openid-connect/auth"
      GRANT_TYPE: "client_credentials"
      TOKEN_PATH: "/protocol/openid-connect/token"
      TOKEN_AUTH_METHOD: "client_secret_post"
      FETCH_TOKEN_METHOD: "POST"
      CLIENT_ID: "<YOUR_CLIENT_ID>"
      CLIENT_SECRET: "<YOUR_CLIENT_SECRET>"
      SCOPES: "registrar"
      REQUIRED_SCOPES: "profile, registrar"
      LOG_LEVEL: "WARNING"
      TEMPLATE: "/path/to/lookup_templates.json"
      FASTMCP_ROUTE_MAPS_FILE: "/path/to/route_maps.yaml"
    connect_timeout: 120
    timeout: 120
```

## Route Maps

Route maps control which endpoints become MCP tools, resources, or are excluded. They are useful to:
- Limit the number of available tools (Universis has 3000+ endpoints)
- Group endpoints logically
- Rename tool tags

> **First match wins, and the last rule should be an explicit `EXCLUDE`.**
> Rules are evaluated top-to-bottom and the first one that matches a path
> decides its fate. Always end the file with a catch-all
> `{ methods: "*", pattern: ".*", mcp_type: EXCLUDE }` rule so that endpoints
> you did not explicitly list are dropped, rather than relying on FastMCP's
> implicit "default = TOOL" behaviour (which would expose all ~3000 endpoints).
>
> The shipped [`route_maps.yaml`](route_maps.yaml) is **read-only**: it exposes
> GET collection endpoints (`GET_Students`) and GET item-by-id endpoints
> (`GET_Students_WithId`) for the common registrar entities, and excludes every
> write method. To enable a write endpoint, add a rule for it **above** the
> final `EXCLUDE` rule (see [Enabling write endpoints](#enabling-write-endpoints)).

### Format

Route maps are an array of objects, each with:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `methods` | string or string[] | `*` | HTTP methods to match. `*` matches all |
| `pattern` | string | `.*` | Python regex to match against the raw OpenAPI path (e.g., `^/api/Students/$`) |
| `mcp_type` | string | `TOOL` | `TOOL`, `RESOURCE`, `RESOURCE_TEMPLATE`, or `EXCLUDE` |
| `tags` | string[] | `[]` | FastMCP tags for the generated tool |
| `mcp_tags` | string[] | `[]` | MCP protocol-level tags |
| `schema_detail` | string | (env default) | Per-tool blueprint verbosity for GET tools: `off`, `ref`, `compact`, or `full` (see [Schema blueprints](#schema-blueprints)) |

### Example `route_maps.yaml`

```yaml
# Expose GET collection endpoints for common collections
- methods: ["GET"]
  pattern: "^/api/Institutes/$"
  mcp_type: TOOL
  tags: ["Institute"]
  mcp_tags: ["Institute entrypoints"]

- methods: ["GET"]
  pattern: "^/api/Students/$"
  mcp_type: TOOL
  tags: ["Student"]
  mcp_tags: ["Students entrypoints"]

# Expose GET item-by-id endpoints (powers the GET_*_WithId tools).
# \{[^/]+\} matches a single path parameter such as /api/Institutes/{id}/.
- methods: ["GET"]
  pattern: "^/api/(Institutes|Students|StudyPrograms|Courses)/\\{[^/]+\\}/?$"
  mcp_type: TOOL

# Explicit catch-all: exclude everything else (keep this LAST)
- methods: "*"
  pattern: ".*"
  mcp_type: EXCLUDE
```

See the shipped [`route_maps.yaml`](route_maps.yaml) for the full read-only set.

Route maps loaded from `FASTMCP_ROUTE_MAPS_FILE` have lower priority than those from `FASTMCP_ROUTE_MAPS` (env var), unless `env_overrides_file` is set to `False`.

### Using inline JSON

```bash
export FASTMCP_ROUTE_MAPS='[{"methods":"*","pattern":".*\\/Me\\/.*","mcp_type":"EXCLUDE","mcp_tags":["MCP_EXCLUDED"]}]'
```

## Schema blueprints

Universis' OpenAPI spec has no `operationId`s and (historically) opaque parameters,
so an agent couldn't tell which fields an entity has — leading to invalid filters
like `departmentId eq 170` (the real field is `department`). Since the server now
keeps `components/schemas` (v0.3.0), each **GET** tool's description is enriched
with a compact **field blueprint** derived from the entity schema, so the agent
knows what to put in `$filter` / `$select` / `$orderby` / `$expand`.

Example (appended to `GET_AcademicPeriods`):

```
Entity AcademicPeriod — filterable & selectable fields ($filter/$select/$orderby):
  id (integer), name (string), alternateName (string), description (string), url (string), ...
Associations (use $expand, or filter by id e.g. `locale eq <id>`): locale, locales
$filter ops: eq ne gt ge lt le, and/or/not, contains/startswith/endswith; strings in single quotes, dates ISO yyyy-MM-dd.
```

### Verbosity & token control

| Variable | Default | Description |
|----------|---------|-------------|
| `TOOL_SCHEMA_DETAIL` | `compact` | Global default detail: `off` (no block), `ref` (pointer to `schema://Entity`), `compact` (capped field list), `full` (all fields + audit) |
| `TOOL_SCHEMA_MAX_FIELDS` | `25` | In `compact`, cap the scalar field list; the rest are summarised as "…and N more" |
| `SCHEMA_RESOURCES` | `false` | If `true`, also expose concrete `schema://<Entity>` resources (slim field table) and a shared `odata://filter-help` resource |
| `SCHEMA_RESOURCE_ENTITIES` | (unset) | Comma/space list of entities to expose as resources. **Unset → auto-restrict to entities that are exposed as tools.** Set → that explicit allowlist only (for env-only setups that don't use `route_maps.yaml`) |

Per-tool override via the `schema_detail` field in a route map (route maps already
curate the toolset, so this is where the token budget is best spent — give `full`
to a few central entities and `off` to lookups the agent never filters):

```yaml
- methods: ["GET"]
  pattern: "^/api/Students/$"
  mcp_type: TOOL
  schema_detail: full          # central entity → all fields

- methods: ["GET"]
  pattern: "^/api/AcademicYears/$"
  mcp_type: TOOL
  schema_detail: off           # lookup → no field block
```

Resources are a **bounded, discoverable** set (concrete `schema://<Entity>` entries
in `list_resources`), not an open template — so a client cannot probe arbitrary
entity names. Two complementary ways to choose the set:
- **Auto (default):** only entities that are exposed as tools get a resource — the
  set tracks your curated `route_maps.yaml` with no extra config.
- **Explicit:** `SCHEMA_RESOURCE_ENTITIES="AcademicPeriod,Student"` — useful when
  you configure purely via the MCP server's `env:` block and don't ship a YAML.

> **Greed note.** The `schema://<Entity>` link inside a tool description is only
> shown when that description was actually truncated *and* the entity has a
> resource, so small/unselected entities give the agent no reason to fetch. The
> resource itself returns a *slim field table*, not the raw schema, so even an
> over-eager client can't blow the context window.

## How Tool Names Are Generated

The server automatically injects `operationId` values into the OpenAPI spec before passing it to FastMCP.

**Naming convention:** `{HTTP_METHOD}_{StaticPathSegments}[_With{PathParam}]`

Examples:

| Endpoint | Tool Name |
|----------|-----------|
| `GET /api/Institutes/` | `GET_Institutes` |
| `GET /api/Institutes/{id}/` | `GET_Institutes_WithId` |
| `POST /api/Institutes/{id}/` | `POST_Institutes_WithId` |
| `GET /api/Students/{id}/courses/` | `GET_Students_courses_WithId` |
| `GET /api/Students/{id}/courses/{courseId}/` | `GET_Students_courses_WithId_WithCourseId` |

### Known limitations

- The Universis OpenAPI spec has **no operationIds** (0/3000+ endpoints). The operationId injection is essential — without it all endpoints get generic names.
- OData query params (`$filter`, `$top`, etc.) may require string values depending on the FastMCP version.

## Tool Examples

### List institutes

```
GET_Institutes
  $top: 5
```

Returns 5 institutes.

### Get institute by ID

```
GET_Institutes_WithId
  id: 111
```

Returns "ΔΗΜΟΚΡΙΤΕΙΟ ΠΑΝΕΠΙΣΤΗΜΙΟ ΘΡΑΚΗΣ".

### List students with filter

The `Student` entity exposes the department as the **`department`** attribute (a
numeric id) — there is no `departmentId` field, so filtering on `departmentId`
returns a `500 ERR_ATTR_UNKNOWN` from the API.

```
GET_Students
  $filter: "department eq 170"
  $top: 10
```

### Get student with expand

```
GET_Students_WithId
  id: 193432
  $expand: "person,institute,studyProgram"
```

### Create/update institute

> ⚠️ Write endpoints are **not** exposed by the shipped read-only `route_maps.yaml`.
> Enable them first (see [Enabling write endpoints](#enabling-write-endpoints)).

```
POST_Institutes
  body: { ... Institute JSON ... }
```

#### Enabling write endpoints

Add a rule **above** the final `EXCLUDE` rule in your route maps, e.g.:

```yaml
# Allow create/update on Institutes
- methods: ["POST", "PUT"]
  pattern: "^/api/Institutes/(\\{[^/]+\\}/)?$"
  mcp_type: TOOL
  tags: ["Institute"]
```

#### Writing data: Universis/`@themost` conventions

The Universis backend is built on [`@themost`](https://github.com/themost-framework),
which has a couple of save semantics worth knowing when you call the write tools.
These are **server-side business rules**, not MCP behaviour — the MCP tool simply
forwards your JSON body to the API.

- **Inserts of client-keyed entities need `"$state": 1`.** Entities whose primary
  key is assigned by the client (e.g. `Course`, where `id` is the course code)
  are treated as *updates* by default, so a plain insert fails with
  `E_PREVIOUS` ("The previous state of the object cannot be determined"). Add
  `"$state": 1` to the posted object to force an insert. Entities with
  server-generated numeric keys (post without an `id`) do not need this.

  ```jsonc
  // POST_Courses  (collection endpoint takes an array body)
  {
    "body": [
      { "$state": 1, "id": "DEMO01", "displayCode": "D01",
        "name": "Demo course", "department": "999",
        "isEnabled": true, "isShared": false }
    ]
  }
  ```

- **Updates only change the fields you send**, but some fields are immutable once
  set (e.g. `Course.courseStructureType` → `"Structure type cannot be changed"`).
  The safe pattern for a partial edit is: read the current object, change the one
  field, and post the whole object back so nothing else looks like it changed.

- **Deletes** use the item tool with the id in the path, e.g.
  `DELETE_Courses_WithId { "id": 990002 }`.

> Tip: scope write/delete route maps tightly (e.g. a single test department) while
> you are experimenting — see the dept-scoped examples above.

## Running the tests

```bash
uv run --with fastmcp --with httpx --with authlib --with python-dotenv \
  --with pyyaml --with pytest pytest -q
```

The suite (`tests/test_fixes.py`) covers the auth token handling, the OpenAPI
parameter sanitizer, and the tool-name pluralizer.

## Project Structure

```
Universis-MCP/
├── universis_mcp_server.py   # The MCP server
├── README.md                 # This file
├── route_maps.yaml           # Example route maps (optional)
├── CHANGELOG.md              # Release notes
└── tests/
    ├── test_fixes.py         # Regression tests (auth, sanitizer, pluralizer)
    └── test_refs.py          # $ref preservation / dangling-ref repair
```

## Architecture

1. **Load OpenAPI spec** from `BASE_URL + SCHEMA_PATH` (JSON or YAML)
2. **Inject operationIds** — generate human-readable names
3. **Sanitize** — fix common OpenAPI spec issues (empty required fields, missing schemas)
4. **Repair dangling refs** — keep all valid `$ref`s (FastMCP 3.x resolves them, which
   is what gives POST tools their request-body field schemas) and replace only the
   handful of unresolvable `$ref`s with a generic `object` placeholder
5. **Filter with route maps** — choose which endpoints become tools
6. **Authenticate** — OAuth2 client_credentials or bearer passthrough
7. **Create FastMCP server** — using `FastMCP.from_openapi()`
8. **Serve** — stdio or streamable HTTP transport

> **Note on `$ref` handling (changed in v0.3.0).** Earlier versions stripped *all*
> `$ref`/`$defs` from the spec because FastMCP <3 aborted the whole build on the
> first unresolvable reference. That also destroyed every request-body schema, so
> POST tools had empty inputs. FastMCP 3.x resolves valid refs itself and tolerates
> bad ones, so the server now preserves valid refs and only neutralises the few
> dangling ones (logged at startup). Response-body cleaning (`clean_refs`) is
> unchanged and still applied to HTTP responses.
