# data.gov.ie MCP server

**Ask Claude questions about Ireland's open data.**

This is an [MCP](https://modelcontextprotocol.io) server for
[data.gov.ie](https://data.gov.ie), the Irish Government's open data portal
(about 22,000 datasets from the CSO, councils, Tusla, the Oireachtas and others).
With it, an AI assistant can search the catalogue, open datasets and analyse
them in conversation, without anyone needing to download or clean a spreadsheet.

> "Which Dublin councils publish FOI disclosure logs, and how often does each one refuse requests?"
>
> "What's the CSO's most recent data on homelessness, broken down by region?"
>
> "What has been published on data.gov.ie this week?"

---

## Use it (no install)

If you use Claude, you can connect the hosted version in about a minute:

1. In Claude (web, desktop or mobile), go to **Customize → Connectors**.
2. Click **+**, then **Add custom connector**.
3. Name it `data.gov.ie` and paste this URL:

   ```
   https://YOUR-RENDER-URL.onrender.com/mcp
   ```

4. Leave the authentication fields empty, then start a new chat and ask away.

It's free to use, read-only, and needs no login. It also works with any other
AI tool that supports remote MCP servers.

---

## What it can do

| Tool | Description |
|---|---|
| `search_datasets` | Search the full catalogue, filtered by publisher, theme or file format |
| `list_facets` | See which publishers, themes and formats exist, and how many datasets each has |
| `get_dataset` | Get a dataset's description, licence and every file it contains |
| `preview_resource` | Open a file and see its columns, a summary of each column, and sample rows |
| `query_resource` | Filter, group, count, sum and sort a file's contents |
| `recently_changed` | List what's been published or updated in the last N days |

It reads CSV, TSV, Excel (XLSX), JSON, GeoJSON and the CSO's JSON-stat format.
JSON-stat tables are flattened into ordinary rows, which makes the CSO's
12,000+ statistical tables as easy to query as a spreadsheet.

### Example

Asked to look at Dublin City Council's 2023 FOI disclosure log, the server
found the dataset, profiled its 442 requests, and grouped them by requester
and decision in three tool calls. Journalists and members of the public had
almost identical outcomes: about 38% granted, 37% part-granted and 26% refused.

---

## Run your own copy

### Hosted (public URL)

See **[DEPLOY.md](DEPLOY.md)** for a step-by-step guide to deploying on Render
using only the GitHub and Render websites, with no command line. It takes
about 15 minutes.

The hosted entry point is `http_server.py`. It adds rate limiting, a shared
24-hour download cache and a `/health` endpoint. The `Dockerfile` works on any
host that runs containers.

### Local (Claude Desktop or Claude Code)

This requires Python 3.10 or newer.

```bash
git clone https://github.com/fergal-adams/datagovie-mcp.git
cd datagovie-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python live_check.py    # optional: test against the live portal
```

**Claude Desktop.** Open Settings → Developer → Edit Config, and add the
following inside `"mcpServers"`, using your own absolute paths:

```json
"datagovie": {
  "command": "/full/path/to/datagovie-mcp/.venv/bin/python",
  "args": ["/full/path/to/datagovie-mcp/server.py"]
}
```

Then quit Claude Desktop with Cmd+Q and reopen it.

**Claude Code:**

```bash
claude mcp add datagovie -- /full/path/to/datagovie-mcp/.venv/bin/python /full/path/to/datagovie-mcp/server.py
```

### Settings

All settings are optional environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `DATAGOVIE_MAX_MB` | `50` locally, `25` hosted | Largest file the server will download |
| `DATAGOVIE_CACHE` | system temp folder | Where downloads are cached |
| `DATAGOVIE_API` | `https://data.gov.ie/api/3/action/` | Point it at another CKAN portal |
| `RATE_LIMIT_GLOBAL_PER_MIN` | `300` | Hosted only: total tool calls per minute |

data.gov.ie runs on [CKAN](https://ckan.org), so changing `DATAGOVIE_API`
should let this server work with other CKAN portals, such as data.gov.uk,
with little or no change.

---

## Limitations

- **View counts aren't available.** The portal's API rejects requests for them,
  so the "least viewed" sort falls back to sorting by date.
- **Large files are skipped.** Queries run in memory, so very large files
  (mainly big GeoJSON map layers) are over the size limit. The server returns
  a direct download link instead.
- **Data quality varies by publisher.** Column names, date formats and coding
  differ between datasets, and sometimes between years of the same dataset.
  The server reports what's in the file; it doesn't clean it.
- **PDFs and other non-tabular files** can be found and linked to, but not queried.

## About the data

All data comes from [data.gov.ie](https://data.gov.ie) and is mostly published
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). If you reuse
it, credit the original publisher. This is an independent project and isn't
affiliated with the Irish Government or the data.gov.ie team.

## Licence

The code is [MIT](LICENSE) licensed.
