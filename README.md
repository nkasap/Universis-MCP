# Universis MCP Server

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

### Format

Route maps are an array of objects, each with:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `methods` | string or string[] | `*` | HTTP methods to match. `*` matches all |
| `pattern` | string | `.*` | Python regex to match against the raw OpenAPI path (e.g., `^/api/Students/$`) |
| `mcp_type` | string | `TOOL` | `TOOL`, `RESOURCE`, `RESOURCE_TEMPLATE`, or `EXCLUDE` |
| `tags` | string[] | `[]` | FastMCP tags for the generated tool |
| `mcp_tags` | string[] | `[]` | MCP protocol-level tags |

### Example `route_maps.yaml`

```yaml
# Only expose GET endpoints for common collections
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

- methods: ["GET"]
  pattern: "^/api/StudyPrograms/$"
  mcp_type: TOOL
  tags: ["StudyProgram"]
  mcp_tags: ["Study programs entrypoints"]

- methods: ["GET"]
  pattern: "^/api/Courses/$"
  mcp_type: TOOL
  tags: ["Course"]
  mcp_tags: ["Courses entrypoints"]

# Fallback: exclude everything else
- methods: "*"
  pattern: ".*"
  mcp_type: EXCLUDE
```

Route maps loaded from `FASTMCP_ROUTE_MAPS_FILE` have lower priority than those from `FASTMCP_ROUTE_MAPS` (env var), unless `env_overrides_file` is set to `False`.

### Using inline JSON

```bash
export FASTMCP_ROUTE_MAPS='[{"methods":"*","pattern":".*\\/Me\\/.*","mcp_type":"EXCLUDE","mcp_tags":["MCP_EXCLUDED"]}]'
```

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

```
GET_Students
  $filter: "departmentId eq 170"
  $top: 10
```

### Get student with expand

```
GET_Students_WithId
  id: 193432
  $expand: "person,institute,studyProgram"
```

### Create/update institute

```
POST_Institutes
  body: { ... Institute JSON ... }
```

## Project Structure

```
Universis-MCP/
├── universis_mcp_server.py   # The MCP server
├── README.md                 # This file
└── route_maps.yaml           # Example route maps (optional)
```

## Architecture

1. **Load OpenAPI spec** from `BASE_URL + SCHEMA_PATH` (JSON or YAML)
2. **Inject operationIds** — generate human-readable names
3. **Sanitize** — fix common OpenAPI spec issues (empty required fields, missing schemas)
4. **Clean refs** — remove `$ref` and `$defs` that FastMCP cannot resolve
5. **Filter with route maps** — choose which endpoints become tools
6. **Authenticate** — OAuth2 client_credentials or bearer passthrough
7. **Create FastMCP server** — using `FastMCP.from_openapi()`
8. **Serve** — stdio or streamable HTTP transport
