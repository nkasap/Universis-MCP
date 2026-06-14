# Schema-blueprint evaluation

Measures whether the v0.4.0 schema blueprints help a local model build valid
OData queries, and at what token/greed cost. Host: **Hermes-Agent** (full MCP:
tools + resources + resource templates); brains: **gemma-4-12b** and
**qwen3.6-27b** served via vLLM behind a LiteLLM OpenAI-compatible endpoint.

## What we measure
- **Filter validity** — API `200` vs `500 ERR_ATTR_UNKNOWN` (hallucinated field).
- **Field accuracy** — did it use the real field (`department`) not the trap (`departmentId`)?
- **Greed** — how often the model reads a `schema://` resource when the description
  already sufficed (from the server `ACCESS_LOG`).
- **Cost** — prompt tokens/task (from LiteLLM/vLLM) and tool/resource payload bytes
  (from `ACCESS_LOG`).

## Conditions (A/B)
Run the matrix per model. Set these env vars on the **server** before the host connects:

| Condition | `TOOL_SCHEMA_DETAIL` | `SCHEMA_RESOURCES` | Tests |
|-----------|----------------------|--------------------|-------|
| baseline  | `off`                | `false`            | no blueprints (current behaviour) |
| compact   | `compact`            | `false`            | description-only enrichment |
| full      | `full`               | `false`            | maximal description |
| resources | `compact`            | `true`             | does the model over-fetch `schema://`? |

Per-tool overrides (`schema_detail:` in `route_maps.yaml`) can be layered on top.

## Running
1. Start the server over HTTP with the access log on:
   ```bash
   ACCESS_LOG=eval/run.jsonl MCP_TRANSPORT=http MCP_PORT=9000 \
   TOOL_SCHEMA_DETAIL=compact SCHEMA_RESOURCES=false \
   FASTMCP_ROUTE_MAPS_FILE=route_maps.yaml \
   AUTH_MODE=oauth2 CLIENT_ID=... CLIENT_SECRET=... \
   uv run --with fastmcp --with httpx --with authlib --with python-dotenv --with pyyaml \
     python universis_mcp_server.py
   ```
2. Point Hermes-Agent at `http://127.0.0.1:9000/mcp`, brain = your LiteLLM endpoint.
3. Feed it the prompts from [`tasks.yaml`](tasks.yaml), capturing the transcript.
4. Summarise greed/payload:
   ```bash
   python eval/parse_access_log.py eval/run.jsonl
   ```
5. Pull prompt/completion tokens from LiteLLM/vLLM logs for the same run.

Repeat per condition × model; compare validity, greed ratio, and tokens.

> Truncate `eval/run.jsonl` between conditions so each summary is clean.
