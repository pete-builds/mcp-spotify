"""The HTTP transport needs a bearer token, and enforces it once it has one.

This server answers LAN clients at http://192.168.86.20:3703/mcp and its tools
create, edit and delete playlists on a real Spotify account. Before
pete-mcp-core 18f2261 it started with no token and answered every caller with
200. Now a streamable-http start with no ``MCP_AUTH_TOKEN`` must refuse, and a
start with one must turn away any request that does not present it.

Auth is built when ``server`` is imported, from environment variables, so each
scenario boots ``server.main()`` in a fresh interpreter, the way
tests/test_lan_host.py does. uvicorn's ``Server.serve`` is replaced by a
recorder, which captures the exact ASGI app ``run_server`` hands uvicorn
without opening a socket. That app is then driven in process through httpx's
ASGI transport, inside its lifespan. Nothing here reaches Spotify: the client
credentials are dummies and no tool is called.

The subprocess runs from an empty directory with ``PYTHON_DOTENV_DISABLED``
set, so neither pydantic-settings nor python-dotenv can pick up a developer's
real ``.env`` and quietly hand the "no token" cases a token.

Positive controls: the same probe that reports "refused" for a tokenless boot
reports "served" for a boot with a token, and the same request that reads 401
without the header reads 200 with it. A probe that could only ever say
"refused", or only ever 401, would prove nothing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LAN_IP = "192.168.86.20"
PORT = 3703
REPO = Path(__file__).resolve().parent.parent

# Obviously fake on purpose. Never put anything that looks like a credential here.
TOKEN = "test-token-not-a-secret"
WRONG_TOKEN = "wrong-token-not-a-secret"

_PROBE = r"""
import asyncio, json, os, sys
import httpx, uvicorn
from fastmcp import FastMCP

out = {
    "served": False,
    "run_transport": None,
    "import_error": None,
    "main_error": None,
    "auth_provider": None,
}
captured = {}


async def _record(self, sockets=None):
    captured["app"] = self.config.app
    out["served"] = True

uvicorn.Server.serve = _record

_real_run = FastMCP.run


def _run(self, *args, **kwargs):
    transport = kwargs.get("transport", args[0] if args else None)
    out["run_transport"] = transport
    if transport == "stdio":
        return None  # the stdio loop was reached; do not block on stdin
    return _real_run(self, *args, **kwargs)

FastMCP.run = _run


def _describe(exc):
    cls = type(exc)
    return {"type": f"{cls.__module__}.{cls.__qualname__}", "message": str(exc)}


def _finish():
    print("PROBE_RESULT " + json.dumps(out))
    sys.exit(0)


try:
    import server
except Exception as exc:
    out["import_error"] = _describe(exc)
    _finish()

auth = server.mcp.auth
out["auth_provider"] = None if auth is None else type(auth).__name__

try:
    server.main()
except Exception as exc:
    out["main_error"] = _describe(exc)
    _finish()

app = captured.get("app")
if app is None:
    _finish()

import healthcheck as shim
from pete_mcp_core import healthcheck as core_health

lan_ip, port, token, wrong_token = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
init = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "auth-probe", "version": "0"},
    },
}
base_headers = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def _jsonrpc(resp):
    # Streamable HTTP answers a POST with plain JSON or with one SSE event.
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):])
        return None
    try:
        return resp.json()
    except ValueError:
        return None


async def main():
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        base = f"http://{lan_ip}:{port}"
        async with httpx.AsyncClient(transport=transport, base_url=base) as client:
            cases = {
                "no_header": {},
                "wrong_token": {"authorization": f"Bearer {wrong_token}"},
                "right_token": {"authorization": f"Bearer {token}"},
            }
            for name, extra in cases.items():
                resp = await client.post("/mcp", json=init, headers={**base_headers, **extra})
                out[name] = {
                    "status": resp.status_code,
                    "www_authenticate": resp.headers.get("www-authenticate"),
                    "session_minted": bool(resp.headers.get("mcp-session-id")),
                    "jsonrpc": _jsonrpc(resp) if resp.status_code == 200 else None,
                }

            # The container healthcheck, by its own rules: healthcheck.py is a
            # shim over pete_mcp_core.healthcheck.main, which resolves the path
            # and the healthy codes exactly like this and sends no credential.
            path = os.getenv("MCP_HEALTH_PATH") or core_health.DEFAULT_HEALTH_PATH
            codes = core_health._resolve_healthy_codes(os.getenv("MCP_HEALTH_CODES"))
            resp = await client.get(path)
            out["health"] = {
                "shim_is_core_main": shim.main is core_health.main,
                "path": path,
                "status": resp.status_code,
                "healthy_codes": sorted(codes),
                "transport_paths": sorted(core_health.TRANSPORT_PATHS),
                "session_minted": bool(resp.headers.get("mcp-session-id")),
            }
    _finish()

asyncio.run(main())
"""


def _boot(tmp_path: Path, **env_overrides: str) -> dict:
    """Boot server.main() in a fresh interpreter and report what happened.

    Every FASTMCP_, MCP_ and SPOTIFY_ variable is dropped from the inherited
    environment first, so a scenario gets exactly the variables it names.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("FASTMCP_", "MCP_", "SPOTIFY_")) and k != "PYTHONPATH"
    }
    env.update(
        {
            "SPOTIFY_CLIENT_ID": "test-id",
            "SPOTIFY_CLIENT_SECRET": "test-secret",
            "SPOTIFY_REFRESH_TOKEN": "test-refresh",
            "MCP_PORT": str(PORT),
            "FASTMCP_SHOW_SERVER_BANNER": "false",
            "FASTMCP_CHECK_FOR_UPDATES": "off",
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTHONPATH": str(REPO),
        }
    )
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, LAN_IP, str(PORT), TOKEN, WRONG_TOKEN],
        cwd=tmp_path,  # empty: no .env for pydantic-settings to find
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # a crashed probe is reported below with its stderr
    )
    for line in proc.stdout.splitlines():
        if line.startswith("PROBE_RESULT "):
            result = json.loads(line.removeprefix("PROBE_RESULT "))
            result["stderr"] = proc.stderr
            return result
    pytest.fail(f"probe produced no result (rc={proc.returncode}):\n{proc.stderr[-3000:]}")


@pytest.fixture(scope="module")
def with_token(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One HTTP boot with a token set, shared by the request-level tests."""
    return _boot(tmp_path_factory.mktemp("with-token"), MCP_AUTH_TOKEN=TOKEN)


# --- HTTP with no token refuses to start ---------------------------------


def test_http_without_a_token_refuses_to_start(tmp_path: Path) -> None:
    """The default deployment shape: streamable-http, nothing else set."""
    result = _boot(tmp_path)
    assert result["served"] is False, result
    assert result["import_error"] is not None, result
    assert result["import_error"]["type"] == "pete_mcp_core.auth.AuthRequiredError", result
    assert "Bearer token required for client 'spotify'" in result["import_error"]["message"]


def test_refusal_names_the_variable_to_set(tmp_path: Path) -> None:
    """The library error does not say which variable is missing. An operator
    reading the log of a container that will not start needs the name."""
    result = _boot(tmp_path, MCP_LOG_FORMAT="text")
    assert "MCP_AUTH_TOKEN is not set" in result["stderr"], result["stderr"][-2000:]


@pytest.mark.parametrize("blank", ["", "   "])
def test_http_with_a_blank_token_refuses_to_start(tmp_path: Path, blank: str) -> None:
    """`MCP_AUTH_TOKEN=` copied from .env.example and never filled in is no token."""
    result = _boot(tmp_path, MCP_AUTH_TOKEN=blank)
    assert result["served"] is False, result
    assert result["import_error"] is not None, result
    assert result["import_error"]["type"] == "pete_mcp_core.auth.AuthRequiredError", result


def test_auth_required_false_does_not_open_http(tmp_path: Path) -> None:
    """MCP_AUTH_REQUIRED=false is not a way to run HTTP open. The import gets
    past build_auth_provider, then run_server refuses a streamable-http server
    with no auth provider, because main() never passes allow_unauthenticated."""
    result = _boot(tmp_path, MCP_AUTH_REQUIRED="false")
    assert result["served"] is False, result
    assert result["import_error"] is None, result
    assert result["auth_provider"] is None, result
    assert result["main_error"] is not None, result
    assert result["main_error"]["type"] == "builtins.ValueError", result
    assert "Refusing to start streamable-http with no auth provider" in (
        result["main_error"]["message"]
    )


def test_control_http_with_a_token_starts(with_token: dict) -> None:
    """Positive control: the same probe reports a served app when a token is
    set, so "refused" above is not the only thing it can say."""
    assert with_token["import_error"] is None, with_token
    assert with_token["main_error"] is None, with_token
    assert with_token["run_transport"] == "streamable-http", with_token
    assert with_token["served"] is True, with_token
    assert with_token["auth_provider"] == "StaticTokenVerifier", with_token


# --- HTTP with a token turns away callers who do not present it ----------


def test_initialize_without_authorization_header_is_401(with_token: dict) -> None:
    case = with_token["no_header"]
    assert case["status"] == 401, case
    assert case["session_minted"] is False, case


def test_initialize_with_the_wrong_token_is_401(with_token: dict) -> None:
    case = with_token["wrong_token"]
    assert case["status"] == 401, case
    assert case["session_minted"] is False, case


def test_initialize_with_the_right_token_succeeds(with_token: dict) -> None:
    case = with_token["right_token"]
    assert case["status"] != 401, case
    assert case["status"] == 200, case
    reply = case["jsonrpc"]
    assert reply is not None, case
    assert "error" not in reply, reply
    assert reply["id"] == 1, reply
    assert reply["result"]["serverInfo"]["name"] == "Spotify", reply
    assert case["session_minted"] is True, case


# --- The container healthcheck stays healthy with auth on ----------------


def test_healthcheck_is_healthy_with_auth_on_and_no_token_presented(with_token: dict) -> None:
    """Docker's probe sends no credential. If auth turned its answer into an
    unhealthy code, the container would flap forever while serving fine."""
    health = with_token["health"]
    # healthcheck.py really is the shared probe, so its rules are the ones read here.
    assert health["shim_is_core_main"] is True, health
    assert health["status"] in health["healthy_codes"], health
    # 500 must still fail the container; a set that accepts it accepts anything.
    assert 500 not in health["healthy_codes"], health


def test_healthcheck_path_is_not_a_transport_path(with_token: dict) -> None:
    """A probe on the /mcp mount would either read 401 or mint a session per
    probe. The sentinel does neither."""
    health = with_token["health"]
    assert health["path"] not in health["transport_paths"], health
    assert not health["path"].startswith(("/mcp", "/sse")), health
    assert health["session_minted"] is False, health


def test_container_config_does_not_repoint_the_healthcheck() -> None:
    """The probe above uses the default path and codes, which is only what the
    container does while neither the Dockerfile nor compose overrides them."""
    for name in ("Dockerfile", "docker-compose.yml"):
        live = [
            line
            for line in (REPO / name).read_text().splitlines()
            if not line.lstrip().startswith("#")
        ]
        offenders = [ln for ln in live if "MCP_HEALTH_PATH" in ln or "MCP_HEALTH_CODES" in ln]
        assert offenders == [], f"{name}: {offenders}"
    assert 'CMD python healthcheck.py' in (REPO / "Dockerfile").read_text()


# --- stdio still works for local development -----------------------------


def test_stdio_without_a_token_still_starts(tmp_path: Path) -> None:
    """stdio has no network listener, so the token requirement does not apply.
    MCP_TRANSPORT=stdio is the only thing a local stdio user has to set."""
    result = _boot(tmp_path, MCP_TRANSPORT="stdio")
    assert result["import_error"] is None, result
    assert result["main_error"] is None, result
    assert result["auth_provider"] is None, result
    assert result["run_transport"] == "stdio", result
    assert result["served"] is False, result
