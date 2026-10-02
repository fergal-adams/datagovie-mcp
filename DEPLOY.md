# Hosting datagovie-mcp publicly

When this is done, anyone with Claude can use the server by pasting one URL into
**Customize → Connectors**. They don't need Python, a config file or a Terminal.

You'll use **GitHub**, to hold the code, and **Render**, to run it. Neither
step needs the command line.

## 1. Put the code on GitHub (about 5 minutes)

1. Go to https://github.com/new. Name the repository `datagovie-mcp`, set it to
   **Public**, and click **Create repository**.
2. On the next page, click **uploading an existing file**.
3. Drag in these files from the folder: `server.py`, `http_server.py`,
   `requirements.txt`, `requirements-hosted.txt`, `Dockerfile`, `.dockerignore`,
   `.gitignore`, `render.yaml`, `README.md`, `DEPLOY.md`, `LICENSE` and `live_check.py`.
   **Don't upload the `.venv` folder.**
   macOS hides files that start with a dot. To see them in Finder, press
   **Cmd+Shift+.** before dragging.
4. Click **Commit changes**.

## 2. Deploy on Render (about 10 minutes)

1. Go to https://render.com and sign up with your GitHub account.
2. Click **New → Blueprint** and select the `datagovie-mcp` repository.
3. Render reads `render.yaml` and sets everything up. Click **Apply**, then wait
   for the build, which takes a few minutes the first time.
4. Open the service. Its URL looks like `https://datagovie-mcp-xxxx.onrender.com`.
   Add `/health` to the end of that URL and visit it. You should see `{"ok":true}`.

**Choosing a plan.** `render.yaml` uses the **Starter** paid plan, which stays on
all the time. You can change `plan: starter` to `plan: free` to pay nothing.
The trade-off is that the free plan sleeps when nobody is using it and takes
about a minute to wake up, so someone's first request after a quiet spell may
time out and need a retry. That's fine for testing, but a poor experience for a
public tool. Check Render's pricing page for current costs.

## 3. Connect it to Claude

1. In Claude (web, desktop or mobile), go to **Customize → Connectors**.
2. Click **+**, then **Add custom connector**.
3. Name it "data.gov.ie" and enter your URL **with `/mcp` on the end**, for
   example `https://datagovie-mcp-xxxx.onrender.com/mcp`.
4. Leave the authentication fields empty, because the server is public and read-only.

Anyone you share the URL with does the same three steps. On Claude's Free plan,
people can only add a limited number of custom connectors, so they may need to
remove another one first.

Once this works, you can remove the local `datagovie` entry from
`claude_desktop_config.json`, so you don't have two copies of the same tools.

## Updating

Edit a file on GitHub, or upload a new version, and commit it. Render redeploys
automatically.

## Settings (Render → your service → Environment)

| Variable | Default | What it does |
|---|---|---|
| `RATE_LIMIT_GLOBAL_PER_MIN` | `300` | Maximum tool calls per minute across all users |
| `RATE_LIMIT_PER_IP_PER_MIN` | `0` (off) | Per-IP limit. See the note below. |
| `DATAGOVIE_MAX_MB` | `25` | Largest file the server will download |
| `DATAGOVIE_MAX_CONCURRENT_DOWNLOADS` | `4` | Simultaneous downloads from the portal |
| `DATAGOVIE_PARSED_CACHE` | `24` | Number of parsed files kept in memory |

**Why the rate limit is global rather than per user.** Claude connects to your
server from Anthropic's cloud, not from each person's device, so all Claude
users can arrive from the same small set of IP addresses. A per-IP limit would
throttle everyone together, which is why it's off by default. It's there in
case non-Claude clients start using the server heavily.

**Caching.** Each file is downloaded from the portal and parsed once, then kept
for 24 hours. Popular datasets are therefore fast for everyone, and the server
isn't repeatedly hitting data.gov.ie.
