# Changelog

Notable changes to this project. Versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases before
0.3.0 are recorded only in the git history and the `v*` tags.

## 0.3.0 (not yet tagged)

### Breaking

- **The HTTP server now requires a bearer token and will not start without
  one.** Until now it started with no token and answered every caller on the
  network, including the tools that create, edit and delete playlists. From
  this version:

  - `MCP_AUTH_TOKEN` must be set before the container starts. If it is missing
    or blank the server exits at startup and logs `MCP_AUTH_TOKEN is not set`.
    Under `restart: unless-stopped` the container then restarts in a loop until
    a token is in place. It never falls back to serving without one.
  - Every client must send `Authorization: Bearer <token>`. A request with no
    header, or with the wrong token, gets `401`.
  - There is no switch that runs HTTP open. `MCP_AUTH_REQUIRED=false` does not
    do it: the server still refuses to start.

  **Before you upgrade:** generate a token with
  `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, put it in
  `.env` on the host as `MCP_AUTH_TOKEN=...`, run `chmod 600 .env`, and add the
  header to every client entry. Keep the token out of `docker-compose.yml` and
  out of git. The README shows the client entry.

  Local stdio use is unaffected: with `MCP_TRANSPORT=stdio` there is no network
  listener and no token is needed.

### Changed

- `pete-mcp-core` moves from `60cc81e` to `18f2261`. That is where the
  requirement comes from: the shared settings now default to requiring a token,
  and `run_server` refuses a streamable-http server that has no auth provider.
  The same range carries the library's session reaping fixes. `mcp` (2.3.0) and
  `fastmcp` (4.1.4) do not move.
- The container healthcheck is unchanged and stays healthy with auth on. It
  probes `/__pete_mcp_liveness`, which is not a transport path, sends no token,
  and reads the `404` it gets back as "the server answered".

### Added

- `tests/test_auth_required.py`: boots the real server and drives its real ASGI
  app to prove the refusal, the `401`s, the authenticated `initialize`, the
  healthcheck, and that stdio still starts with no token.
