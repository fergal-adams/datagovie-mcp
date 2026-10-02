#!/usr/bin/env python3
"""
datagovie-mcp: an MCP server for Ireland's open data portal, data.gov.ie.

Lets an AI assistant search the catalogue (~22,000 datasets), inspect a
dataset's metadata and files, preview a file, and run simple queries and
aggregations against CSV, JSON-stat (CSO), JSON and XLSX resources.

Run:  python3 server.py            (stdio transport, for Claude Desktop / Claude Code)
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import itertools
import json
import os
import statistics
import tempfile
import time
from collections import OrderedDict
from typing import Any

import httpx

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

API = os.environ.get("DATAGOVIE_API", "https://data.gov.ie/api/3/action/")
MAX_DOWNLOAD = int(os.environ.get("DATAGOVIE_MAX_MB", "50")) * 1024 * 1024
CACHE_DIR = os.environ.get("DATAGOVIE_CACHE", os.path.join(tempfile.gettempdir(), "datagovie-cache"))
CACHE_TTL = 24 * 3600
HEADERS = {"User-Agent": "datagovie-mcp/1.1 (+https://data.gov.ie)"}
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("DATAGOVIE_MAX_CONCURRENT_DOWNLOADS", "4"))
PARSED_CACHE_SIZE = int(os.environ.get("DATAGOVIE_PARSED_CACHE", "24"))
_download_slots: asyncio.Semaphore | None = None
_parsed: "OrderedDict[tuple, tuple]" = OrderedDict()
TABULAR = {"CSV", "TSV", "JSON-STAT", "JSON", "XLSX", "XLS", "GEOJSON"}

SORTS = {
    "relevance": "score desc, metadata_modified desc",
    "recent": "metadata_modified desc",
    "popular": "views_recent desc",
    "least_viewed": "views_total asc",
    "name": "name asc",
}
FACETS = {"publisher": "organization", "format": "res_format", "theme": "theme", "licence": "license_id"}

mcp = _Server(
    "data.gov.ie",
    instructions=(
        "Tools for Ireland's open data portal (data.gov.ie). Typical flow: "
        "search_datasets -> get_dataset -> preview_resource -> query_resource. "
        "Use list_facets to discover publisher and theme slugs for filtering. "
        "Most CSO tables are JSON-stat cubes; they are flattened to one row per cell "
        "with a 'value' column. Always cite the dataset title and publisher."
    ),
)


# --------------------------------------------------------------------------- CKAN

async def ckan(action: str, **params: Any) -> Any:
    params = {k: v for k, v in params.items() if v is not None}
    async with httpx.AsyncClient(headers=HEADERS, timeout=60, follow_redirects=True) as c:
        r = await c.get(API + action, params=params)
    if r.status_code >= 400:
        try:
            detail = r.json().get("error")
        except ValueError:
            detail = r.text[:300]
        raise RuntimeError(f"data.gov.ie API returned {r.status_code} for {action}: {detail}")
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"data.gov.ie API error: {data.get('error')}")
    return data["result"]


async def _search(**params: Any) -> tuple[dict, list[str]]:
    """package_search that degrades gracefully if the portal rejects view tracking
    or a views-based sort. Returns (result, notes about any fallbacks used)."""
    notes: list[str] = []
    attempts = [dict(params)]
    no_track = {k: v for k, v in params.items() if k != "include_tracking"}
    attempts.append(no_track)
    if "views" in str(params.get("sort", "")):
        attempts.append({**no_track, "sort": "metadata_modified desc"})
    attempts.append({k: v for k, v in no_track.items() if k != "sort"})
    last_err: Exception | None = None
    for i, p in enumerate(attempts):
        try:
            res = await ckan("package_search", **p)
            if "include_tracking" in params and "include_tracking" not in p:
                notes.append("View counts unavailable from the portal, so they are omitted.")
            if p.get("sort") != params.get("sort"):
                notes.append(f"Requested sort was rejected; fell back to '{p.get('sort', 'default')}'.")
            return res, notes
        except Exception as e:  # try the next, simpler variant
            last_err = e
    raise RuntimeError(f"Search failed: {last_err}")


def _fq(publisher, theme, fmt, extra=None):
    parts = []
    if publisher:
        parts.append(f'organization:"{publisher}"')
    if theme:
        parts.append(f'theme:"{theme}"')
    if fmt:
        parts.append(f'res_format:"{fmt.upper()}"')
    if extra:
        parts.append(extra)
    return " AND ".join(parts) or None


def _summary(p: dict) -> dict:
    track = p.get("tracking_summary") or {}
    return {
        "name": p.get("name"),
        "title": p.get("title"),
        "publisher": (p.get("organization") or {}).get("title"),
        "publisher_slug": (p.get("organization") or {}).get("name"),
        "modified": (p.get("metadata_modified") or "")[:10],
        "formats": sorted({(r.get("format") or "").upper() for r in p.get("resources", []) if r.get("format")}),
        "views_total": track.get("total"),
        "views_recent": track.get("recent"),
        "notes": (p.get("notes") or "")[:240],
        "url": f"https://data.gov.ie/dataset/{p.get('name')}",
    }


# --------------------------------------------------------------------------- fetching + parsing

async def _resource(resource_id: str) -> dict:
    return await ckan("resource_show", id=resource_id)


async def _download(url: str) -> bytes:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, hashlib.sha256(url.encode()).hexdigest())
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, "rb") as f:
            return f.read()
    global _download_slots
    if _download_slots is None:
        _download_slots = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    async with _download_slots:
        return await _download_uncached(url, path)


async def _download_uncached(url: str, path: str) -> bytes:
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_TTL:
        with open(path, "rb") as f:  # another request fetched it while we waited
            return f.read()
    buf = bytearray()
    async with httpx.AsyncClient(headers=HEADERS, timeout=120, follow_redirects=True) as c:
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_DOWNLOAD:
                    raise ValueError(
                        f"File exceeds {MAX_DOWNLOAD // 1048576} MB limit (set DATAGOVIE_MAX_MB to raise it). "
                        f"Download it directly: {url}"
                    )
    with open(path, "wb") as f:
        f.write(buf)
    return bytes(buf)


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_csv(raw: bytes, delimiter: str | None = None) -> list[dict]:
    text = _decode(raw)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    return [{(k or "").strip(): v for k, v in row.items()} for row in csv.DictReader(io.StringIO(text), delimiter=delimiter)]


def _jsonstat_datasets(obj: Any) -> list[dict]:
    if isinstance(obj, dict) and obj.get("class") == "dataset":
        return [obj]
    if isinstance(obj, dict) and obj.get("class") == "collection":
        return [d for item in obj.get("link", {}).get("item", []) for d in _jsonstat_datasets(item)]
    if isinstance(obj, dict):  # JSON-stat 1.x bundle
        return [v for v in obj.values() if isinstance(v, dict) and "dimension" in v and "value" in v]
    return []


def _flatten_jsonstat(ds: dict) -> list[dict]:
    dims = ds.get("id") or ds["dimension"].get("id")
    sizes = ds.get("size") or ds["dimension"].get("size")
    axes = []
    for d in dims:
        cat = ds["dimension"][d].get("category", {})
        idx = cat.get("index")
        if idx is None:
            codes = list(cat.get("label", {}).keys())
        elif isinstance(idx, dict):
            codes = sorted(idx, key=idx.get)
        else:
            codes = list(idx)
        labels = cat.get("label", {})
        name = ds["dimension"][d].get("label") or d
        axes.append((name, [labels.get(c, c) for c in codes]))
    values = ds.get("value", [])
    total = 1
    for s in sizes:
        total *= s
    get = (lambda i: values.get(str(i))) if isinstance(values, dict) else (lambda i: values[i] if i < len(values) else None)
    rows = []
    for i, combo in enumerate(itertools.product(*[a[1] for a in axes])):
        if i >= total:
            break
        row = {axes[j][0]: combo[j] for j in range(len(axes))}
        row["value"] = get(i)
        rows.append(row)
    return rows


def _parse_json(raw: bytes) -> tuple[list[dict] | None, Any]:
    obj = json.loads(_decode(raw))
    stats = _jsonstat_datasets(obj)
    if stats:
        return _flatten_jsonstat(stats[0]), obj
    if isinstance(obj, dict) and obj.get("type") == "FeatureCollection":
        return [{**(f.get("properties") or {}), "_geometry_type": (f.get("geometry") or {}).get("type")}
                for f in obj.get("features", [])], obj
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        return obj, obj
    if isinstance(obj, dict):
        for v in obj.values():  # common pattern: {"results": [...]}
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return v, obj
    return None, obj


def _parse_xlsx(raw: bytes, sheet: str | None) -> list[dict]:
    try:
        import openpyxl
    except ImportError as e:
        raise ValueError("XLSX support needs openpyxl: pip install openpyxl") from e
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    header = None
    for row in it:  # first row with 2+ non-empty cells is the header
        if sum(c not in (None, "") for c in row) >= 2:
            header = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(row)]
            break
    if header is None:
        return []
    return [dict(zip(header, r)) for r in it if any(c not in (None, "") for c in r)]


async def _load(resource_id: str, sheet: str | None = None) -> tuple[dict, list[dict] | None, Any]:
    key = (resource_id, sheet)
    hit = _parsed.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        _parsed.move_to_end(key)
        return hit[1]
    result = await _load_uncached(resource_id, sheet)
    _parsed[key] = (time.time(), result)
    while len(_parsed) > PARSED_CACHE_SIZE:
        _parsed.popitem(last=False)
    return result


async def _load_uncached(resource_id: str, sheet: str | None) -> tuple[dict, list[dict] | None, Any]:
    res = await _resource(resource_id)
    url, fmt = res.get("url") or "", (res.get("format") or "").upper().lstrip(".")
    if not url.startswith("http"):
        raise ValueError("Resource has no downloadable URL.")
    if fmt not in TABULAR:
        raise ValueError(f"Format '{fmt}' isn't tabular. Open it directly: {url}")
    raw = await _download(url)
    if fmt in ("CSV", "TSV"):
        return res, _parse_csv(raw, "\t" if fmt == "TSV" else None), None
    if fmt in ("XLSX", "XLS"):
        return res, _parse_xlsx(raw, sheet), None
    rows, obj = _parse_json(raw)
    return res, rows, obj


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("€", "").replace("%", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _profile(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    out = []
    for col in rows[0].keys():
        vals = [r.get(col) for r in rows]
        present = [v for v in vals if v not in (None, "")]
        nums = [n for n in (_num(v) for v in present) if n is not None]
        info: dict[str, Any] = {"column": col, "non_empty": len(present), "distinct": len({str(v) for v in present})}
        if present and len(nums) / len(present) > 0.9:
            info.update(type="numeric", min=min(nums), max=max(nums), mean=round(statistics.fmean(nums), 3))
        else:
            counts: dict[str, int] = {}
            for v in present:
                counts[str(v)] = counts.get(str(v), 0) + 1
            info.update(type="text", top_values=sorted(counts.items(), key=lambda kv: -kv[1])[:5])
        out.append(info)
    return out


def _match_col(rows: list[dict], name: str) -> str:
    cols = list(rows[0].keys()) if rows else []
    for c in cols:
        if c == name:
            return c
    for c in cols:
        if c.lower() == name.lower():
            return c
    raise ValueError(f"Unknown column '{name}'. Columns: {cols}")


# --------------------------------------------------------------------------- tools

@mcp.tool()
async def search_datasets(
    query: str = "",
    publisher: str | None = None,
    theme: str | None = None,
    format: str | None = None,
    sort: str = "relevance",
    limit: int = 10,
    offset: int = 0,
) -> dict:
    """Search the data.gov.ie catalogue.

    query: free text; supports Lucene syntax ("exact phrase", -exclude, OR).
    publisher: organisation slug, e.g. "central-statistics-office", "tusla" (see list_facets).
    theme: e.g. "Government", "Health", "Housing", "Society", "Justice".
    format: resource format, e.g. "CSV", "JSON-STAT", "GEOJSON".
    sort: relevance | recent | popular | least_viewed | name.
    limit: 1-50.
    """
    res, notes = await _search(
        q=query or "*:*",
        fq=_fq(publisher, theme, format),
        sort=SORTS.get(sort, SORTS["relevance"]),
        rows=max(1, min(limit, 50)),
        start=max(0, offset),
        include_tracking="true",
    )
    out = {"total_matches": res["count"], "offset": offset, "results": [_summary(p) for p in res["results"]]}
    if notes:
        out["notes"] = notes
    return out


@mcp.tool()
async def list_facets(facet: str = "publisher", query: str = "", limit: int = 50) -> dict:
    """Count datasets by publisher, theme, format or licence, optionally within a search.

    facet: publisher | theme | format | licence. Returns slugs usable as search filters.
    """
    field = FACETS.get(facet, facet)
    res = await ckan(
        "package_search",
        q=query or "*:*",
        rows=0,
        **{"facet.field": json.dumps([field]), "facet.limit": max(1, min(limit, 500))},
    )
    items = res.get("search_facets", {}).get(field, {}).get("items", [])
    return {
        "facet": facet,
        "total_datasets": res["count"],
        "values": sorted(
            [{"slug": i["name"], "label": i.get("display_name"), "count": i["count"]} for i in items],
            key=lambda x: -x["count"],
        ),
    }


@mcp.tool()
async def get_dataset(name_or_id: str) -> dict:
    """Full metadata for one dataset, including every resource (file) with its id and format.
    Pass a resource id to preview_resource or query_resource."""
    p = await ckan("package_show", id=name_or_id, include_tracking="true")
    base = _summary(p)
    base["notes"] = p.get("notes")
    base["licence"] = p.get("license_title")
    base["created"] = (p.get("metadata_created") or "")[:10]
    base["tags"] = [t["name"] for t in p.get("tags", [])]
    base["extras"] = {e["key"]: e["value"] for e in p.get("extras", []) if e.get("value")}
    base["resources"] = [
        {
            "id": r["id"],
            "name": r.get("name"),
            "format": (r.get("format") or "").upper(),
            "url": r.get("url"),
            "size_bytes": r.get("size"),
            "last_modified": (r.get("last_modified") or r.get("created") or "")[:10],
            "queryable": (r.get("format") or "").upper().lstrip(".") in TABULAR,
        }
        for r in p.get("resources", [])
    ]
    return base


@mcp.tool()
async def preview_resource(resource_id: str, rows: int = 15, sheet: str | None = None) -> dict:
    """Download a resource (cached for 24h) and show its columns, a column profile
    (types, ranges, top values) and the first rows. Handles CSV, TSV, XLSX, JSON,
    GeoJSON (properties only) and CSO JSON-stat (flattened to one row per cell)."""
    res, data, obj = await _load(resource_id, sheet)
    out: dict[str, Any] = {"resource": res.get("name"), "format": res.get("format"), "url": res.get("url")}
    if data is None:
        out["note"] = "JSON isn't a table; showing its top-level structure."
        out["structure"] = (
            {k: type(v).__name__ for k, v in obj.items()} if isinstance(obj, dict) else type(obj).__name__
        )
        return out
    out.update(
        row_count=len(data),
        columns=list(data[0].keys()) if data else [],
        profile=_profile(data),
        rows=data[: max(1, min(rows, 100))],
    )
    return out


@mcp.tool()
async def query_resource(
    resource_id: str,
    where: dict[str, Any] | None = None,
    contains: dict[str, str] | None = None,
    columns: list[str] | None = None,
    group_by: list[str] | None = None,
    aggregate: str = "count",
    sort_by: str | None = None,
    descending: bool = True,
    limit: int = 50,
    sheet: str | None = None,
) -> dict:
    """Filter, group and aggregate a tabular resource.

    where: exact matches (case-insensitive), e.g. {"County": "Wicklow"}; a list value means any-of.
    contains: substring matches, e.g. {"Request Description": "housing"}.
    columns: which columns to return (ignored when grouping).
    group_by: columns to group on; aggregate is then "count", "sum:<col>", "mean:<col>",
              "min:<col>" or "max:<col>". Result column is named "result".
    sort_by: column (or "result" when grouping). limit: max rows returned (1-500).
    """
    _, data, _ = await _load(resource_id, sheet)
    if data is None:
        raise ValueError("This resource isn't tabular; use preview_resource to inspect it.")
    rows = data
    for col, val in (where or {}).items():
        c = _match_col(data, col)
        wanted = {str(v).lower() for v in (val if isinstance(val, list) else [val])}
        rows = [r for r in rows if str(r.get(c, "")).lower() in wanted]
    for col, sub in (contains or {}).items():
        c = _match_col(data, col)
        rows = [r for r in rows if sub.lower() in str(r.get(c, "")).lower()]
    matched = len(rows)

    if group_by:
        keys = [_match_col(data, g) for g in group_by]
        op, _, target = aggregate.partition(":")
        tcol = _match_col(data, target) if target else None
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault(tuple(r.get(k) for k in keys), []).append(r)
        result = []
        for gk, members in groups.items():
            if op == "count":
                v: Any = len(members)
            else:
                nums = [n for n in (_num(m.get(tcol)) for m in members) if n is not None]
                fn = {"sum": sum, "mean": statistics.fmean, "min": min, "max": max}.get(op)
                if fn is None:
                    raise ValueError("aggregate must be count, sum:<col>, mean:<col>, min:<col> or max:<col>")
                v = round(fn(nums), 4) if nums else None
            result.append({**dict(zip(keys, gk)), "result": v})
        rows = result

    if sort_by:
        sc = "result" if group_by and sort_by == "result" else _match_col(rows or data, sort_by)
        numeric = [r for r in rows if _num(r.get(sc)) is not None]
        text = [r for r in rows if _num(r.get(sc)) is None and r.get(sc) not in (None, "")]
        blank = [r for r in rows if _num(r.get(sc)) is None and r.get(sc) in (None, "")]
        rows = (sorted(numeric, key=lambda r: _num(r.get(sc)), reverse=descending)
                + sorted(text, key=lambda r: str(r.get(sc)).lower(), reverse=descending)
                + blank)

    if columns and not group_by:
        cols = [_match_col(data, c) for c in columns]
        rows = [{c: r.get(c) for c in cols} for r in rows]

    lim = max(1, min(limit, 500))
    return {"matched_rows": matched, "returned": min(lim, len(rows)), "truncated": len(rows) > lim, "rows": rows[:lim]}


@mcp.tool()
async def recently_changed(days: int = 7, publisher: str | None = None, limit: int = 25) -> dict:
    """Datasets created or updated in the last N days, newest first. Useful for spotting
    new releases, quiet revisions or methodology changes."""
    res, notes = await _search(
        q="*:*",
        fq=_fq(publisher, None, None, f"metadata_modified:[NOW-{max(1, days)}DAYS TO NOW]"),
        sort="metadata_modified desc",
        rows=max(1, min(limit, 100)),
        include_tracking="true",
    )
    out = {"total_changed": res["count"], "results": [_summary(p) for p in res["results"]]}
    if notes:
        out["notes"] = notes
    return out


if __name__ == "__main__":
    mcp.run()
