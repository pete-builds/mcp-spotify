"""Tool-level test: a server tool returns shaped JSON output.

This exercises the server.py wiring (env validation, _format, error handling)
with the underlying Spotify client mocked, so no live API call is made. We
reach the underlying coroutine through the FastMCP FunctionTool's `.fn`
attribute and swap the module-level client for an AsyncMock.
"""

import json
import os
from unittest.mock import AsyncMock

os.environ.setdefault("SPOTIFY_CLIENT_ID", "test-id")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test-secret")
os.environ.setdefault("SPOTIFY_REFRESH_TOKEN", "test-refresh")

import pytest

import server
from clients.spotify import SpotifyError


def _tool_fn(tool):
    """Return the raw coroutine function behind an mcp tool, tolerant of the
    FastMCP wrapper shape."""
    return getattr(tool, "fn", tool)


@pytest.mark.asyncio
async def test_search_artist_tool_returns_json(monkeypatch):
    fake = AsyncMock()
    fake.search_artists.return_value = [
        {
            "id": "abc",
            "name": "Radiohead",
            "popularity": 90,
            "genres": ["rock"],
            "followers": 999,
            "url": "https://x/abc",
        }
    ]
    monkeypatch.setattr(server, "spotify", fake)

    out = await _tool_fn(server.search_artist)("Radiohead", limit=3)
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert parsed[0]["id"] == "abc"
    assert parsed[0]["name"] == "Radiohead"
    fake.search_artists.assert_awaited_once_with("Radiohead", limit=3)


@pytest.mark.asyncio
async def test_search_artist_tool_shapes_error(monkeypatch):
    fake = AsyncMock()
    fake.search_artists.side_effect = SpotifyError("boom (500)")
    monkeypatch.setattr(server, "spotify", fake)

    out = await _tool_fn(server.search_artist)("X")
    parsed = json.loads(out)
    assert "error" in parsed
    assert "boom" in parsed["error"]
