#!/usr/bin/env python3
"""Smoke-test the server's tools against the live data.gov.ie API.
Run once after install:  python3 live_check.py"""
import asyncio, json
import server as s

def show(label, obj, n=600):
    print(f"\n== {label}\n{json.dumps(obj, ensure_ascii=False, default=str)[:n]}")

async def main():
    r = await s.search_datasets('"FOI Disclosure Log"', format="CSV", limit=3)
    show("search FOI logs", r)
    show("publishers", await s.list_facets("publisher", limit=5))
    show("themes (checks the 'theme' facet field)", await s.list_facets("theme", limit=5))
    show("least viewed (checks view-count sorting)", await s.search_datasets(sort="least_viewed", limit=3))
    show("recently changed", await s.recently_changed(days=3, limit=3))
    if r["results"]:
        d = await s.get_dataset(r["results"][0]["name"])
        res = next((x for x in d["resources"] if x["queryable"]), None)
        if res:
            show("preview", await s.preview_resource(res["id"], rows=3), 1200)
    cso = await s.search_datasets("population", publisher="central-statistics-office", format="JSON-STAT", limit=1)
    if cso["results"]:
        d = await s.get_dataset(cso["results"][0]["name"])
        js = next((x for x in d["resources"] if x["format"] == "JSON-STAT"), None)
        if js:
            show("CSO JSON-stat preview", await s.preview_resource(js["id"], rows=3), 1200)

asyncio.run(main())
