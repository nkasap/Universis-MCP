"""Summarise an ACCESS_LOG JSONL into greed / payload metrics.

Run the server with ACCESS_LOG=eval/run.jsonl, drive it with your agent host,
then:  python eval/parse_access_log.py eval/run.jsonl
"""
import json
import sys
from collections import Counter


def main(path: str) -> None:
    api_calls = 0
    resource_reads = 0
    schema_reads = 0
    api_bytes = 0
    resource_bytes = 0
    by_resource = Counter()
    by_tool_url = Counter()

    with open(path, encoding="utf-8-sig") as f:  # tolerate a BOM
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            kind = rec.get("kind")
            if kind == "api_call":
                api_calls += 1
                api_bytes += int(rec.get("bytes", 0))
                by_tool_url[rec.get("url", "?")] += 1
            elif kind == "resource_read":
                resource_reads += 1
                resource_bytes += int(rec.get("bytes", 0))
                uri = rec.get("uri", "?")
                by_resource[uri] += 1
                if str(uri).startswith("schema://"):
                    schema_reads += 1

    print(f"api_calls          : {api_calls}  ({api_bytes:,} bytes)")
    print(f"resource_reads     : {resource_reads}  ({resource_bytes:,} bytes)")
    print(f"  schema:// reads  : {schema_reads}   <-- 'greed' signal")
    # Greed ratio: schema fetches per tool call. High => model over-fetches schemas.
    if api_calls:
        print(f"  greed ratio      : {schema_reads / api_calls:.2f} schema-reads / api-call")
    if by_resource:
        print("\ntop resources read:")
        for uri, n in by_resource.most_common(10):
            print(f"   {n:4d}  {uri}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python eval/parse_access_log.py <access_log.jsonl>")
        raise SystemExit(2)
    main(sys.argv[1])
