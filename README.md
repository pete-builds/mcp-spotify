# mcp-spotify

A Spotify MCP server designed to run as a **remote service** over Streamable HTTP — not a local stdio subprocess.

Most Spotify MCP servers today ([varunneal/spotify-mcp](https://github.com/varunneal/spotify-mcp), [marcelmarais/spotify-mcp-server](https://github.com/marcelmarais/spotify-mcp-server), etc.) launch as a subprocess on the same machine as your AI client. If you have more than one machine — a homelab, a shared dev server, a laptop that moves around — you end up running N copies with N separate OAuth flows.

This server runs **once**, in a Docker container, exposes Spotify tools over Streamable HTTP on a port, and any MCP client on your LAN or Tailscale network connects to it with one URL and one bearer token. OAuth happens once in a bootstrap script; the refresh token lives on the server.

> Previously published as `spotify-mcp-sse`. Renamed to `mcp-spotify` on the migration from the deprecated HTTP+SSE transport to Streamable HTTP (MCP spec 2025-06-18). The old URL `https://github.com/pete-builds/spotify-mcp-sse` redirects to this repo.

## Features

Nine tools, oriented around creating and managing playlists:

| Tool | Purpose |
|---|---|
| `search_artist` | Resolve artist name → Spotify ID for disambiguation |
| `get_artist_top_tracks` | Artist's most popular tracks (works around the deprecated `/top-tracks` endpoint) |
| `create_playlist_from_artists` | Build a new playlist from a list of artist names; shuffle interleaves |
| `add_artists_to_playlist` | Extend an existing playlist; accepts URL, URI, ID, or exact name |
| `create_playlist_from_tracks` | Build a playlist from specific track URIs you already have |
| `list_my_playlists` | List playlists you own or follow |
| `update_playlist` | Rename, edit description, toggle public |
| `delete_playlist` | Remove from your library (Spotify's "unfollow") |
| `remove_tracks_from_playlist` | Remove specific tracks |

## Feb 2026 Spotify API migration — why this matters

Spotify shipped a breaking API migration on Feb 11, 2026. If you built a Spotify integration before then and haven't touched it since, it's broken. If you're starting a new one now, Spotify's dashboard quietly puts you in "Development Mode" with severe restrictions. This repo documents and works around all of it:

- `/artists/{id}/top-tracks` returns 403 for new apps → falls back to track search with `artist:` filter
- Artist search with `limit=1` returns the *wrong* artist (e.g. `Radiohead` → `Thom Yorke`) → always requests ≥2 and prefers exact name match
- Search `limit` caps at 10 (docs still say 50) → clamps internally
- `POST /users/{id}/playlists` removed → uses `POST /me/playlists`
- `POST /playlists/{id}/tracks` → renamed to `/items` (takes `{"uris": [...]}`)
- `DELETE /playlists/{id}/items` takes a *different* shape: `{"items": [{"uri": "..."}]}`
- Write operations require the authenticating user to be explicitly added to the app's User Management tab in the Spotify Developer dashboard (even the app creator)

## Quick start

### 1. Register a Spotify app

https://developer.spotify.com/dashboard → Create app.

- Redirect URI: `http://127.0.0.1:8765/callback` (exact — case-sensitive, no trailing slash)
- API: Web API
- Save **Client ID** and **Client Secret**

In the app's Settings → User Management, add yourself (name + the email on your Spotify account). This is required for any write operations.

### 2. One-time OAuth bootstrap (on your local machine)

```bash
git clone https://github.com/pete-builds/mcp-spotify
cd mcp-spotify
export SPOTIFY_CLIENT_ID=...
export SPOTIFY_CLIENT_SECRET=...
python3 bootstrap.py
```

Your browser opens, you authorize, the terminal prints a long-lived refresh token.

### 3. Deploy the server

Copy the code to whatever host will run the container (typically a homelab box, LAN-accessible).

Generate a bearer token. The server requires one for HTTP: these tools create, edit and delete playlists on your account, so the server will not answer a caller that cannot present it.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Write `.env` next to `docker-compose.yml`, then lock it down:

```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REFRESH_TOKEN=...
MCP_AUTH_TOKEN=<the token you just generated>
```

```bash
chmod 600 .env
```

The token lives in `.env` on the host and nowhere else. Never put it in `docker-compose.yml` or a compose override, and never commit it (`.env` is gitignored).

Start it:

```bash
docker compose up -d --build
```

Server is now at `http://<host>:3703/mcp` (Streamable HTTP).

If `MCP_AUTH_TOKEN` is missing or blank the container does not serve. It exits at startup, logs `MCP_AUTH_TOKEN is not set`, and `restart: unless-stopped` keeps restarting it until `.env` has a token. There is no setting that runs HTTP without one: `MCP_AUTH_REQUIRED=false` does not open it either.

### 4. Register with your MCP client

Every request must carry the token in an `Authorization: Bearer` header. A request without it, or with the wrong one, gets `401`.

Claude Code:

```bash
claude mcp add --transport http --scope user spotify http://<host>:3703/mcp \
  --header "Authorization: Bearer <your token>"
```

Or as a JSON entry (`.mcp.json`, or any client that takes the same shape):

```json
{
  "mcpServers": {
    "spotify": {
      "type": "http",
      "url": "http://<host>:3703/mcp",
      "headers": {
        "Authorization": "Bearer <your token>"
      }
    }
  }
}
```

That file now holds a credential, so keep it out of git and readable only by you.

Any other MCP client that supports Streamable HTTP: point it at the same URL and configure the same header.

### Local stdio (development)

For a local subprocess instead of the HTTP service, set `MCP_TRANSPORT=stdio`:

```bash
MCP_TRANSPORT=stdio python server.py
```

stdio opens no network listener, so no bearer token is needed and `MCP_AUTH_TOKEN` can stay unset. The Spotify credentials are still required. `MCP_TRANSPORT=stdio` is the only switch: without it the server defaults to HTTP and refuses to start until a token is set.

## Configuration

Environment variables (all optional except the credentials and the bearer token):

| Var | Default | Purpose |
|---|---|---|
| `SPOTIFY_CLIENT_ID` | none | From Spotify Developer dashboard (required) |
| `SPOTIFY_CLIENT_SECRET` | none | From Spotify Developer dashboard (required) |
| `SPOTIFY_REFRESH_TOKEN` | none | From `bootstrap.py` (required) |
| `MCP_AUTH_TOKEN` | none | Bearer token clients must send (required for HTTP; not needed for stdio) |
| `MCP_TRANSPORT` | `streamable-http` | `streamable-http` or `stdio` |
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `MCP_PORT` | `3703` | Listening port |

The included `docker-compose.yml` uses `network_mode: host`. If you'd rather expose with a port mapping, replace that with:

```yaml
    ports:
      - "3703:3703"
```

## OAuth scopes

Bootstrap requests these:

- `playlist-modify-private`
- `playlist-modify-public`
- `playlist-read-private`

If you add tools that need more (e.g. `user-top-read` for "my listening history"), extend `SCOPES` in `bootstrap.py` and re-run it; you'll get a fresh refresh token with the new permissions.

## Architecture

- [FastMCP](https://github.com/PrefectHQ/fastmcp) 4 over Streamable HTTP transport; the protocol revision (2025-06-18 or 2026-07-28) is negotiated per connection
- [httpx](https://www.python-httpx.org/) async client with in-memory access-token cache and automatic refresh on 401 or expiry
- Stdlib-only bootstrap helper (`http.server`, `webbrowser`, `urllib`) — no extra deps for the one-time OAuth dance
- `python:3.13-slim` base image, `fastmcp==4.0.10`, `httpx==0.28.1`

## License

MIT. See [LICENSE](./LICENSE).

## Credits

Built by [Pete Stergion](https://brooksnewmedia.com) as part of Brooks New Media's homelab toolkit. The Feb 2026 API workarounds were hard-won; PRs welcome if you spot a cleaner approach.
